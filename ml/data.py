"""数据加载：目录结构 train/val/test/<class_key>/*.jpg（与真实数据集接入方式一致）。

真实数据（如 Skin Burn Dataset，YOLO 格式标注）转换脚本只需把图像按类别落到
对应目录即可复用本模块；标签映射规则（Ⅰ度+浅Ⅱ度 -> superficial 等）在
`remap_yolo_labels` 中实现。
"""
import os
import random
from collections import Counter

import numpy as np
from PIL import Image
from torch.utils.data import Dataset

from server import config

MEAN = np.array([0.485, 0.456, 0.406], dtype=np.float32)
STD = np.array([0.229, 0.224, 0.225], dtype=np.float32)

TRAIN_TRANSFORMS = None  # 延迟初始化，避免未装 torch 时 import 失败


def class_weights_for(labels) -> np.ndarray:
    """类权重 = 1/频次，归一化到权重之和 = 类别数。"""
    cnt = Counter(labels.tolist() if hasattr(labels, "tolist") else labels)
    n = len(labels)
    k = len(config.CLASS_KEYS)
    w = np.array([n / (k * cnt.get(i, 1)) for i in range(k)], dtype=np.float32)
    return w


class BurnDataset(Dataset):
    def __init__(self, root: str, split: str, size: int = 224, augment: bool = False):
        self.items = []
        d = os.path.join(root, split)
        for ci, key in enumerate(config.CLASS_KEYS):
            cd = os.path.join(d, key)
            if not os.path.isdir(cd):
                continue
            for fn in sorted(os.listdir(cd)):
                if fn.lower().endswith((".jpg", ".jpeg", ".png", ".webp")):
                    self.items.append((os.path.join(cd, fn), ci))
        self.size = size
        self.augment = augment
        if augment:
            from torchvision import transforms

            self.tf = transforms.Compose([
                transforms.RandomResizedCrop(size, scale=(0.6, 1.0), ratio=(0.8, 1.25)),
                transforms.RandomHorizontalFlip(),
                transforms.RandomRotation(15),
                transforms.ColorJitter(brightness=0.2, contrast=0.2, saturation=0.2),
                transforms.ToTensor(),
                transforms.Normalize(MEAN.tolist(), STD.tolist()),
            ])
        else:
            from torchvision import transforms

            self.tf = transforms.Compose([
                transforms.Resize((size, size)),
                transforms.ToTensor(),
                transforms.Normalize(MEAN.tolist(), STD.tolist()),
            ])

    def __len__(self):
        return len(self.items)

    def __getitem__(self, i):
        path, label = self.items[i]
        img = Image.open(path).convert("RGB")
        return self.tf(img), label

    def class_counts(self):
        cnt = Counter(label for _, label in self.items)
        return {config.CLASS_NAMES[k]: v for k, v in sorted(cnt.items())}


def stratified_split(root: str, ratios=(0.7, 0.15, 0.15), seed: int = 2026):
    """若数据根目录只有 <class_key>/ 文件夹，则按比例分层划分为 train/val/test。"""
    has_splits = os.path.isdir(os.path.join(root, "train"))
    if has_splits:
        return
    rng = random.Random(seed)
    for ci, key in enumerate(config.CLASS_KEYS):
        cd = os.path.join(root, key)
        if not os.path.isdir(cd):
            continue
        files = [f for f in os.listdir(cd) if f.lower().endswith((".jpg", ".jpeg", ".png"))]
        rng.shuffle(files)
        n = len(files)
        n_tr = int(n * ratios[0])
        n_va = int(n * ratios[1])
        parts = {"train": files[:n_tr], "val": files[n_tr:n_tr + n_va], "test": files[n_tr + n_va:]}
        for sp, fl in parts.items():
            dst = os.path.join(root, sp, key)
            os.makedirs(dst, exist_ok=True)
            for f in fl:
                os.replace(os.path.join(cd, f), os.path.join(dst, f))


def remap_yolo_labels(raw_class_names):
    """公开数据集原始标签（一度/二度/三度）-> 项目三分类体系的映射表。"""
    mapping = {}
    for name in raw_class_names:
        low = str(name).lower()
        if "1" in low or "first" in low or "一度" in low or " superficial" in low:
            mapping[name] = 0  # 浅度（Ⅰ度 + 浅Ⅱ度）
        elif "2" in low or "second" in low or "二度" in low:
            # 深浅二度在多数公开集不区分；默认并入深二度，歧义样本由医学顾问裁定
            mapping[name] = 1
        elif "3" in low or "third" in low or "三度" in low:
            mapping[name] = 2
        else:
            mapping[name] = None  # 歧义 -> 剔除或人工裁定
    return mapping
