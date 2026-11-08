"""烧伤深度三分类网络：ResNet50 主干 + 三头（三分类 / 浅度二分类 / 深二度-三度二分类）。

三头设计对应申请书 3.1.2 提出的"级联分类策略"：先区分浅度与其他烧伤，再区分深二度与三度，
推理时把三个头的输出现概率级联融合，逐步收敛分类空间。
"""
import torch
import torch.nn as nn


class BurnNet(nn.Module):
    def __init__(self, num_classes: int = 3, dropout: float = 0.3, pretrained: bool = False):
        super().__init__()
        from torchvision import models

        weights = models.ResNet50_Weights.IMAGENET1K_V2 if pretrained else None
        backbone = models.resnet50(weights=weights)
        feat_dim = backbone.fc.in_features
        backbone.fc = nn.Identity()
        self.backbone = backbone
        self.head3 = nn.Sequential(nn.Dropout(dropout), nn.Linear(feat_dim, num_classes))
        self.head_a = nn.Sequential(nn.Dropout(dropout), nn.Linear(feat_dim, 2))  # 浅度 vs 非浅度
        self.head_b = nn.Sequential(nn.Dropout(dropout), nn.Linear(feat_dim, 2))  # 深二度 vs 三度

    def forward(self, x):
        f = self.backbone(x)
        return self.head3(f), self.head_a(f), self.head_b(f)

    def set_backbone_trainable(self, flag: bool):
        for p in self.backbone.parameters():
            p.requires_grad = flag

    def features(self, x):
        return self.backbone(x)


def fuse_probs(p3, pa, pb):
    """级联融合：先由 p3 与 pa 加权得到 P(浅度)，余量按 pb 分配给深二度/三度。

    参数均为 numpy 数组，形状 (N, C)。返回 (N,3)。
    """
    a = 0.5 * (p3[:, 0] + pa[:, 0])
    out = [a, (1.0 - a) * pb[:, 0], (1.0 - a) * pb[:, 1]]
    import numpy as np

    out = np.stack(out, axis=1)
    out = np.clip(out, 1e-9, 1.0)
    return out / out.sum(axis=1, keepdims=True)
