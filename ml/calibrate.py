"""温度缩放置信度校准 + 转人工阈值敏感性分析（申请书 4.3 节的工程实现）。

流程：
 1) 在验证集上收集三个头的 logits 与真实标签；
 2) 网格搜索 + 精细化搜索每个头的温度参数 T（最小化 NLL）；
 3) 计算校准前后的 ECE（10 bins）并绘制可靠性图；
 4) 六档阈值 (60%~85%) 敏感性分析：转人工率 与 三度漏诊率（会被自动放行的
    "真三度但 AI 没判三度"的案例比例），绘制权衡曲线并选择最优阈值；
 5) 在测试集上给出校准后的最终指标，写入 models/calibration.json。
"""
import argparse
import copy
import json
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import torch  # noqa: E402
from torch.utils.data import DataLoader  # noqa: E402

from ml.data import BurnDataset  # noqa: E402
from server import config  # noqa: E402
from server.net import BurnNet  # noqa: E402


def softmax(z):
    z = z - z.max(axis=1, keepdims=True)
    e = np.exp(z)
    return e / e.sum(axis=1, keepdims=True)


def fuse(p3, pa, pb):
    a = 0.5 * (p3[:, 0] + pa[:, 0])
    out = np.stack([a, (1 - a) * pb[:, 0], (1 - a) * pb[:, 1]], axis=1)
    out = np.clip(out, 1e-9, 1.0)
    return out / out.sum(axis=1, keepdims=True)


def nll(probs, labels):
    p = np.clip(probs[np.arange(len(labels)), labels], 1e-9, 1)
    return float(-np.log(p).mean())


def ece_score(conf, correct, bins=10):
    ece = 0.0
    table = []
    for i in range(bins):
        lo, hi = i / bins, (i + 1) / bins
        m = (conf > lo) & (conf <= hi)
        if m.sum() == 0:
            table.append({"bin": f"{lo:.1f}-{hi:.1f}", "n": 0, "conf": None, "acc": None})
            continue
        table.append({
            "bin": f"{lo:.1f}-{hi:.1f}", "n": int(m.sum()),
            "conf": round(float(conf[m].mean()), 4),
            "acc": round(float(correct[m].mean()), 4),
        })
        ece += m.mean() * abs(conf[m].mean() - correct[m].mean())
    return float(ece), table


def collect_logits(net, loader, device):
    net.eval()
    L3, LA, LB, Y = [], [], [], []
    with torch.no_grad():
        for x, y in loader:
            l3, la, lb = net(x.to(device))
            L3.append(l3.cpu().numpy())
            LA.append(la.cpu().numpy())
            LB.append(lb.cpu().numpy())
            Y.append(y.numpy())
    return np.concatenate(L3), np.concatenate(LA), np.concatenate(LB), np.concatenate(Y)


def fit_temperature(logits, labels):
    """单头温度搜索：网格 [0.5, 3.0] 步长 0.05，再在最优附近以 0.005 步长细化。"""
    best_t, best = 1.0, float("inf")
    for t in np.arange(0.5, 3.001, 0.05):
        v = nll(softmax(logits / t), labels)
        if v < best:
            best, best_t = v, t
    fine = np.arange(max(0.3, best_t - 0.06), best_t + 0.06, 0.005)
    for t in fine:
        v = nll(softmax(logits / t), labels)
        if v < best:
            best, best_t = v, t
    return round(float(best_t), 3)


def fit_temperatures_fused(l3, la, lb, labels, rounds=2):
    """三头级联融合后的联合温度拟合（坐标下降）：目标是最小化融合概率的 NLL，
    保证"校准后置信度"在最终输出（而非单个头）上成立。"""
    T = {"head3": 1.0, "head_a": 1.0, "head_b": 1.0}
    keys = [("head3", l3), ("head_a", la), ("head_b", lb)]

    def fused_nll(T):
        cal = fuse(softmax(l3 / T["head3"]), softmax(la / T["head_a"]), softmax(lb / T["head_b"]))
        return nll(cal, labels)

    cur = fused_nll(T)
    for _ in range(rounds):
        for key, logits in keys:
            best_t, best_v = T[key], cur
            for t in np.arange(0.4, 3.001, 0.05):
                tt = dict(T); tt[key] = float(t)
                v = fused_nll(tt)
                if v < best_v:
                    best_v, best_t = v, float(t)
            for t in np.arange(max(0.3, best_t - 0.06), best_t + 0.06, 0.005):
                tt = dict(T); tt[key] = float(t)
                v = fused_nll(tt)
                if v < best_v:
                    best_v, best_t = v, float(t)
            T[key], cur = round(best_t, 3), best_v
    return T


def threshold_analysis(conf, pred, labels):
    rows = []
    for th in [0.60, 0.65, 0.70, 0.75, 0.80, 0.85]:
        refer = conf < th
        referral_rate = float(refer.mean())
        # 漏诊：真实三度、AI 未判三度、且置信度高于阈值被"自动放行"（无人复核）
        is2 = labels == 2
        released_wrong = is2 & (pred != 2) & (~refer)
        miss_rate = float(released_wrong.sum() / max(is2.sum(), 1))
        rows.append({
            "threshold": th,
            "referral_rate": round(referral_rate, 4),
            "full_thickness_miss_rate": round(miss_rate, 4),
            "n_referral": int(refer.sum()),
        })
    # 选择：漏诊率最小；并列时在转人工率 <= 0.35 的档位中取"最保守"的更高阈值——
    # 医疗安全优先（宁可多转人工，不留自动放行的风险口子）
    ok = [r for r in rows if r["referral_rate"] <= 0.35]
    pool = ok if ok else rows
    min_miss = min(r["full_thickness_miss_rate"] for r in pool)
    best = max((r for r in pool if r["full_thickness_miss_rate"] == min_miss),
               key=lambda r: r["threshold"])
    return rows, best


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=os.path.join(config.DATA_DIR, "synthetic"))
    ap.add_argument("--ckpt", default=config.CHECKPOINT_PATH)
    ap.add_argument("--batch", type=int, default=32)
    args = ap.parse_args()

    device = "cuda" if torch.cuda.is_available() else "cpu"
    ckpt = torch.load(os.path.abspath(args.ckpt), map_location="cpu", weights_only=False)
    net = BurnNet(num_classes=3, pretrained=False)
    net.load_state_dict(ckpt["state_dict"])
    net.to(device)

    root = os.path.abspath(args.data)
    val_ld = DataLoader(BurnDataset(root, "val", ckpt["meta"].get("input_size", 224)),
                        batch_size=args.batch, num_workers=0)
    test_ld = DataLoader(BurnDataset(root, "test", ckpt["meta"].get("input_size", 224)),
                         batch_size=args.batch, num_workers=0)

    # ---------- 1) 收集 logits ----------
    l3, la, lb, y = collect_logits(net, val_ld, device)
    p3, pa, pb = softmax(l3), softmax(la), softmax(lb)
    raw = fuse(p3, pa, pb)
    raw_conf = raw.max(axis=1)
    raw_pred = raw.argmax(axis=1)

    # ---------- 2) 温度缩放（联合拟合融合输出） ----------
    temperature = fit_temperatures_fused(l3, la, lb, y)
    T3, TA, TB = temperature["head3"], temperature["head_a"], temperature["head_b"]
    print(f"最优温度参数：{temperature}")

    cal = fuse(softmax(l3 / T3), softmax(la / TA), softmax(lb / TB))
    cal_conf = cal.max(axis=1)
    cal_pred = cal.argmax(axis=1)

    ece_before, table_before = ece_score(raw_conf, (raw_pred == y).astype(float))
    ece_after, table_after = ece_score(cal_conf, (cal_pred == y).astype(float))
    print(f"ECE：校准前 {ece_before:.4f} -> 校准后 {ece_after:.4f}")

    # ---------- 3) 阈值敏感性 ----------
    rows, best_th = threshold_analysis(cal_conf, cal_pred, y)
    print(f"最优转人工阈值：{best_th['threshold']}（转人工率 {best_th['referral_rate']:.1%}，"
          f"三度漏诊率 {best_th['full_thickness_miss_rate']:.1%}）")

    # ---------- 4) 测试集最终指标 ----------
    l3t, lat, lbt, yt = collect_logits(net, test_ld, device)
    cal_t = fuse(softmax(l3t / T3), softmax(lat / TA), softmax(lbt / TB))
    conf_t = cal_t.max(axis=1)
    pred_t = cal_t.argmax(axis=1)
    acc = float((pred_t == yt).mean())
    per_class = {}
    for c in range(3):
        tp = ((pred_t == c) & (yt == c)).sum()
        fp = ((pred_t == c) & (yt != c)).sum()
        fn = ((pred_t != c) & (yt == c)).sum()
        prec = tp / max(tp + fp, 1)
        rec = tp / max(tp + fn, 1)
        per_class[config.CLASS_NAMES[c]] = {
            "precision": round(float(prec), 4), "recall": round(float(rec), 4),
            "f1": round(float(2 * prec * rec / max(prec + rec, 1e-9)), 4),
            "support": int((yt == c).sum()),
        }
    is2 = yt == 2
    refer_t = conf_t < best_th["threshold"]
    miss_t = float(((is2) & (pred_t != 2) & (~refer_t)).sum() / max(is2.sum(), 1))
    print(f"测试集（校准后）：acc={acc:.4f}  三度召回={per_class[config.CLASS_NAMES[2]]['recall']:.4f}  "
          f"漏诊率={miss_t:.4f}")

    result = {
        "temperature": temperature,
        "review_threshold": best_th["threshold"],
        "calibrated": True,
        "ece": {
            "before": round(ece_before, 4), "after": round(ece_after, 4),
            "bins_before": table_before, "bins_after": table_after,
        },
        "sensitivity": rows,
        "metrics": {
            "split": "test",
            "accuracy": round(acc, 4),
            "per_class": per_class,
            "referral_rate": round(float(refer_t.mean()), 4),
            "full_thickness_miss_rate": round(miss_t, 4),
        },
    }
    out_json = config.CALIBRATION_PATH
    os.makedirs(os.path.dirname(out_json), exist_ok=True)
    with open(out_json, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    ckpt["calibration"] = {k: result[k] for k in
                           ("temperature", "review_threshold", "calibrated", "ece", "metrics")}
    torch.save(ckpt, os.path.abspath(args.ckpt))
    print(f"校准结果已写入 {out_json}，并回写 checkpoint")

    # ---------- 5) 图表 ----------
    try:
        draw_plots(table_before, table_after, rows, yt, pred_t, cal_conf)
    except Exception as e:
        print("绘图失败（不影响结果文件）：", e)


def draw_plots(bins_before, bins_after, rows, y_true, y_pred, conf):
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib import font_manager

    for fp in [r"C:\Windows\Fonts\msyh.ttc", r"C:\Windows\Fonts\simhei.ttf"]:
        if os.path.exists(fp):
            font_manager.fontManager.addfont(fp)
            plt.rcParams["font.family"] = font_manager.FontProperties(fname=fp).get_name()
            break
    plt.rcParams["axes.unicode_minus"] = False
    os.makedirs(config.REPORT_DIR, exist_ok=True)

    # 可靠性图
    fig, ax = plt.subplots(figsize=(6.4, 5.2), dpi=140)
    for bins, label, color in [(bins_before, "校准前", "#b45309"), (bins_after, "校准后（温度缩放）", "#0f766e")]:
        xs = [b["conf"] for b in bins if b["n"] > 0]
        ys = [b["acc"] for b in bins if b["n"] > 0]
        ax.plot(xs, ys, "o-", label=label, color=color, ms=5)
    ax.plot([0, 1], [0, 1], "k--", lw=1, label="完美校准")
    ax.set_xlabel("预测置信度")
    ax.set_ylabel("经验准确率")
    ax.set_title("可靠性图（Reliability Diagram）")
    ax.legend()
    ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(os.path.join(config.REPORT_DIR, "reliability.png"))
    plt.close(fig)

    # 转人工率-漏诊率权衡曲线
    fig, ax = plt.subplots(figsize=(6.4, 5.2), dpi=140)
    ths = [r["threshold"] for r in rows]
    ax.plot([r["referral_rate"] * 100 for r in rows],
            [r["full_thickness_miss_rate"] * 100 for r in rows], "o-", color="#0f766e")
    for r in rows:
        ax.annotate(f"{r['threshold']:.0%}", (r["referral_rate"] * 100, r["full_thickness_miss_rate"] * 100),
                    textcoords="offset points", xytext=(6, 6), fontsize=9)
    ax.set_xlabel("转人工率 (%)")
    ax.set_ylabel("三度烧伤漏诊率 (%)")
    ax.set_title("转人工率 – 漏诊率权衡曲线（验证集）")
    ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(os.path.join(config.REPORT_DIR, "tradeoff.png"))
    plt.close(fig)

    # 混淆矩阵
    cm = np.zeros((3, 3), dtype=int)
    for t, p in zip(y_true, y_pred):
        cm[t, p] += 1
    fig, ax = plt.subplots(figsize=(5.6, 5.0), dpi=140)
    im = ax.imshow(cm, cmap="BuGn")
    ax.set_xticks(range(3), config.CLASS_NAMES, fontsize=10)
    ax.set_yticks(range(3), config.CLASS_NAMES, fontsize=10)
    for i in range(3):
        for j in range(3):
            ax.text(j, i, str(cm[i, j]), ha="center", va="center",
                    color="white" if cm[i, j] > cm.max() / 2 else "black", fontsize=12)
    ax.set_title("测试集混淆矩阵（校准后）")
    ax.set_xlabel("AI 判读")
    ax.set_ylabel("真实标签")
    fig.tight_layout()
    fig.savefig(os.path.join(config.REPORT_DIR, "confusion_matrix.png"))
    plt.close(fig)
    print("图表已输出到 reports/")


if __name__ == "__main__":
    main()
