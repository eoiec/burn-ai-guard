"""ResNet50 两阶段训练（申请书 4.2 节的工程实现）。

阶段一：冻结 conv1~layer4，仅训练三个分类头（特征缓存，CPU 上也可快速完成）。
阶段二：解冻全部层，低学习率整体微调（带在线数据增强）。

用法：
    python ml/train.py --data data/synthetic --epochs1 12 --epochs2 0   # 演示
    python ml/train.py --data data/skin_burn --epochs1 20 --epochs2 30  # 真实数据
"""
import argparse
import datetime
import json
import os
import sys
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import torch  # noqa: E402
import torch.nn as nn  # noqa: E402
from torch.utils.data import DataLoader  # noqa: E402

from ml.data import BurnDataset, class_weights_for  # noqa: E402
from server import config  # noqa: E402
from server.net import BurnNet  # noqa: E402


def evaluate(net, loader, device):
    net.eval()
    labels, preds = [], []
    with torch.no_grad():
        for x, y in loader:
            l3, la, lb = net(x.to(device))
            p3 = torch.softmax(l3, 1)
            pa = torch.softmax(la, 1)
            pb = torch.softmax(lb, 1)
            a = 0.5 * (p3[:, 0] + pa[:, 0])
            fused = torch.stack([a, (1 - a) * pb[:, 0], (1 - a) * pb[:, 1]], 1)
            preds += fused.argmax(1).cpu().tolist()
            labels += list(y.numpy())
    labels = np.array(labels)
    preds = np.array(preds)
    acc = float((labels == preds).mean())
    per_class = {}
    for c in range(3):
        tp = ((preds == c) & (labels == c)).sum()
        fp = ((preds == c) & (labels != c)).sum()
        fn = ((preds != c) & (labels == c)).sum()
        prec = tp / max(tp + fp, 1)
        rec = tp / max(tp + fn, 1)
        f1 = 2 * prec * rec / max(prec + rec, 1e-9)
        per_class[config.CLASS_NAMES[c]] = {
            "precision": round(float(prec), 4),
            "recall": round(float(rec), 4),
            "f1": round(float(f1), 4),
            "support": int((labels == c).sum()),
        }
    return acc, per_class, labels, preds


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=os.path.join("..", "data", "synthetic"))
    ap.add_argument("--epochs1", type=int, default=12)
    ap.add_argument("--epochs2", type=int, default=2)
    ap.add_argument("--batch", type=int, default=32)
    ap.add_argument("--size", type=int, default=224)
    ap.add_argument("--lr1", type=float, default=1e-3)
    ap.add_argument("--lr2", type=float, default=1e-5)
    ap.add_argument("--out", default=config.CHECKPOINT_PATH)
    ap.add_argument("--threads", type=int, default=max(2, (os.cpu_count() or 4) - 1))
    args = ap.parse_args()

    torch.set_num_threads(args.threads)
    device = "cuda" if torch.cuda.is_available() else "cpu"
    print(f"设备：{device}  线程：{args.threads}")

    root = os.path.abspath(args.data)
    train_ds = BurnDataset(root, "train", args.size, augment=args.epochs2 > 0)
    val_ds = BurnDataset(root, "val", args.size)
    test_ds = BurnDataset(root, "test", args.size)
    print(f"训练集 {len(train_ds)} / 验证集 {len(val_ds)} / 测试集 {len(test_ds)}")
    print(f"类别分布（训练）：{train_ds.class_counts()}")

    train_ld = DataLoader(train_ds, batch_size=args.batch, shuffle=True, num_workers=0)
    val_ld = DataLoader(val_ds, batch_size=args.batch, num_workers=0)
    test_ld = DataLoader(test_ds, batch_size=args.batch, num_workers=0)

    net = BurnNet(num_classes=3, pretrained=True).to(device)
    labels_all = [lab for _, lab in train_ds.items]
    w = torch.tensor(class_weights_for(labels_all), device=device)
    ce3 = nn.CrossEntropyLoss(weight=w)
    ce2 = nn.CrossEntropyLoss()

    opt = torch.optim.AdamW
    history = {"stage1": [], "stage2": []}
    best_val = -1.0

    def save_best(acc_val):
        """只在验证准确率创新高时覆盖 checkpoint，避免后一阶段反而退化。"""
        os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
        torch.save({
            "state_dict": net.state_dict(),
            "meta": {
                "trained_on": os.path.basename(root),
                "date": datetime.date.today().isoformat(),
                "classes": config.CLASS_LABELS,
                "epochs": {"stage1": args.epochs1, "stage2": args.epochs2},
                "input_size": args.size,
            },
            "calibration": {},
        }, args.out)
        print(f"  ✅ 新最优 val_acc={acc_val:.4f}，checkpoint 已更新")

    def head_targets(y):
        ya = (y != 0).long()
        mask_b = y != 0
        yb = torch.where(y == 1, 0, 1)
        return ya, yb, mask_b

    def head_loss(f, y):
        l3, la, lb = net.head3(f), net.head_a(f), net.head_b(f)
        ya, yb, mask_b = head_targets(y)
        loss = ce3(l3, y) + 0.5 * ce2(la, ya) + 0.5 * ce2(lb[mask_b], yb[mask_b])
        return loss, l3, la, lb

    # ---------------- 阶段一：冻结主干，缓存特征训练分类头 ----------------
    if args.epochs1 > 0:
        print("\n[阶段一] 冻结 ResNet50 主干，训练分类头（特征已缓存，快速迭代）")
        net.set_backbone_trainable(False)
        net.eval()
        feats, ys = [], []
        t0 = time.time()
        with torch.no_grad():
            for x, y in train_ld:
                feats.append(net.features(x.to(device)).cpu())
                ys.append(y)
        feats = torch.cat(feats)
        ys = torch.cat(ys)
        print(f"  特征缓存完成：{tuple(feats.shape)}，耗时 {time.time() - t0:.1f}s")

        head_opt = opt([p for p in net.parameters() if p.requires_grad], lr=args.lr1)
        n = len(ys)
        for ep in range(args.epochs1):
            net.train()
            perm = torch.randperm(n)
            tot = 0.0
            for i in range(0, n, args.batch):
                idx = perm[i:i + args.batch]
                f, y = feats[idx].to(device), ys[idx].to(device)
                loss, _, _, _ = head_loss(f, y)
                head_opt.zero_grad()
                loss.backward()
                head_opt.step()
                tot += float(loss.detach()) * len(idx)
            acc, per_class, _, _ = evaluate(net, val_ld, device)
            history["stage1"].append({"epoch": ep + 1, "loss": round(tot / n, 4), "val_acc": round(acc, 4)})
            print(f"  epoch {ep + 1:02d}  loss={tot / n:.4f}  val_acc={acc:.4f}")
            if acc > best_val:
                best_val = acc
                save_best(acc)

    # ---------------- 阶段二：全网络微调 ----------------
    if args.epochs2 > 0:
        print("\n[阶段二] 解冻全部层，低学习率微调（在线数据增强）")
        net.set_backbone_trainable(True)
        full_opt = opt(net.parameters(), lr=args.lr2)
        for ep in range(args.epochs2):
            net.train()
            tot = 0.0
            for x, y in train_ld:
                x, y = x.to(device), y.to(device)
                f = net.features(x)
                loss, _, _, _ = head_loss(f, y)
                full_opt.zero_grad()
                loss.backward()
                full_opt.step()
                tot += float(loss.detach()) * len(y)
            acc, per_class, _, _ = evaluate(net, val_ld, device)
            history["stage2"].append({"epoch": ep + 1, "loss": round(tot / len(train_ds), 4), "val_acc": round(acc, 4)})
            print(f"  epoch {ep + 1:02d}  loss={tot / len(train_ds):.4f}  val_acc={acc:.4f}")
            if acc > best_val:
                best_val = acc
                save_best(acc)
            else:
                print("  （未超过阶段一最优，不覆盖 checkpoint）")

    # ---------------- 最终评估 ----------------
    if args.epochs1 == 0 and args.epochs2 == 0:
        os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
        torch.save({"state_dict": net.state_dict(),
                    "meta": {"trained_on": os.path.basename(root), "date": datetime.date.today().isoformat(),
                             "classes": config.CLASS_LABELS, "input_size": args.size},
                    "calibration": {}}, args.out)
    acc, per_class, y_true, y_pred = evaluate(net, test_ld, device)
    print(f"\n最终模型测试集准确率（校准前）：{acc:.4f}")
    for k, v in per_class.items():
        print(f"  {k}: {v}")

    with open(os.path.join(config.REPORT_DIR, "train_history.json"), "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)
    print(f"模型文件 -> {args.out}")


if __name__ == "__main__":
    main()
