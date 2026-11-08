"""一键构建演示模型：合成数据集 -> 两阶段训练 -> 温度缩放校准 -> 阈值分析。

用法：python ml/build_demo_model.py [--n 400] [--epochs1 10] [--epochs2 0]
真实数据到位后改用：
    python ml/train.py --data data/skin_burn --epochs1 20 --epochs2 30
    python ml/calibrate.py --data data/skin_burn
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE, "data", "synthetic")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=400, help="每类样本数")
    ap.add_argument("--epochs1", type=int, default=10)
    ap.add_argument("--epochs2", type=int, default=0, help="CPU 演示默认关闭全网络微调")
    ap.add_argument("--force", action="store_true", help="重新生成数据集")
    args = ap.parse_args()

    if args.force or not os.path.isdir(os.path.join(DATA_DIR, "train")):
        print("== 步骤 1/3：生成合成演示数据集 ==")
        from ml import synth

        counts = synth.build(DATA_DIR, n_per_class=args.n, size=256)
        print(counts)
    else:
        print("== 步骤 1/3：数据集已存在，跳过（--force 可重建） ==")

    print("\n== 步骤 2/3：两阶段训练 ==")
    from ml import train as train_mod

    sys.argv = ["train.py", "--data", DATA_DIR,
                "--epochs1", str(args.epochs1), "--epochs2", str(args.epochs2)]
    train_mod.main()

    print("\n== 步骤 3/3：温度缩放校准与阈值分析 ==")
    from ml import calibrate as cal_mod

    sys.argv = ["calibrate.py", "--data", DATA_DIR]
    cal_mod.main()

    print("\n完成。启动服务：python -m server.app")


if __name__ == "__main__":
    main()
