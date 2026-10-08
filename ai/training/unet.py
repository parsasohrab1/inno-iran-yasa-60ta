"""Small U-Net for defect segmentation (FR-2-2) + training loop.

python -m ai.training.unet --root data/synthetic --epochs 20
"""
import argparse

import torch
import torch.nn as nn
import torch.nn.functional as F
from torch.utils.data import DataLoader


def block(i, o):
    return nn.Sequential(
        nn.Conv2d(i, o, 3, padding=1), nn.BatchNorm2d(o), nn.ReLU(inplace=True),
        nn.Conv2d(o, o, 3, padding=1), nn.BatchNorm2d(o), nn.ReLU(inplace=True),
    )


class UNet(nn.Module):
    """Input: RGB (+ optional depth channel). Output: 1-channel defect logits, same HxW."""

    def __init__(self, in_ch=4, base=16):
        super().__init__()
        c = [base, base * 2, base * 4, base * 8]
        self.enc = nn.ModuleList([block(in_ch, c[0]), block(c[0], c[1]), block(c[1], c[2])])
        self.mid = block(c[2], c[3])
        self.up = nn.ModuleList([nn.ConvTranspose2d(c[3], c[2], 2, 2), nn.ConvTranspose2d(c[2], c[1], 2, 2),
                                 nn.ConvTranspose2d(c[1], c[0], 2, 2)])
        self.dec = nn.ModuleList([block(c[2] * 2, c[2]), block(c[1] * 2, c[1]), block(c[0] * 2, c[0])])
        self.head = nn.Conv2d(c[0], 1, 1)

    def forward(self, x):
        skips = []
        for e in self.enc:
            x = e(x)
            skips.append(x)
            x = F.max_pool2d(x, 2)
        x = self.mid(x)
        for up, dec, skip in zip(self.up, self.dec, reversed(skips)):
            x = dec(torch.cat([up(x), skip], dim=1))
        return self.head(x)


def dice_bce(logits, target):
    p = torch.sigmoid(logits)
    inter = (p * target).sum((1, 2, 3))
    dice = 1 - (2 * inter + 1) / (p.sum((1, 2, 3)) + target.sum((1, 2, 3)) + 1)
    return dice.mean() + F.binary_cross_entropy_with_logits(logits, target)


def main():
    from .dataset import RubberDefectDataset

    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default="data/synthetic")
    ap.add_argument("--epochs", type=int, default=20)
    ap.add_argument("--batch", type=int, default=8)
    ap.add_argument("--out", default="unet.pt")
    a = ap.parse_args()

    dev = "cuda" if torch.cuda.is_available() else "cpu"
    model = UNet().to(dev)
    opt = torch.optim.AdamW(model.parameters(), 1e-3)
    dl = DataLoader(RubberDefectDataset(a.root, "train"), a.batch, shuffle=True, num_workers=2)
    val = DataLoader(RubberDefectDataset(a.root, "val"), a.batch)

    def prep(b):
        return torch.cat([b["image"], b["depth"]], 1).to(dev), b["mask"].to(dev)

    for ep in range(a.epochs):
        model.train()
        for b in dl:
            x, y = prep(b)
            loss = dice_bce(model(x), y)
            opt.zero_grad()
            loss.backward()
            opt.step()
        model.eval()
        with torch.no_grad():
            vl = sum(dice_bce(model(prep(b)[0]), prep(b)[1]).item() for b in val) / max(1, len(val))
        print(f"epoch {ep + 1}/{a.epochs} train_loss={loss.item():.4f} val_loss={vl:.4f}")
        torch.save(model.state_dict(), a.out)


if __name__ == "__main__":
    main()
