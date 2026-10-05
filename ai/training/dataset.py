"""PyTorch dataset over the synthetic (or real, same-layout) rubber defect dataset."""
import json
import os

import cv2
import numpy as np
import torch
from torch.utils.data import Dataset

CLASS_MAP = {0: "healthy", 1: "crack", 2: "bubble", 3: "deformation",
             4: "foreign_particle", 5: "discoloration", 6: "flash", 7: "incomplete_fill"}


class RubberDefectDataset(Dataset):
    def __init__(self, root, split="train"):
        self.root = os.path.join(root, split)
        self.files = sorted(f for f in os.listdir(os.path.join(self.root, "images")) if f.endswith(".png"))

    def __len__(self):
        return len(self.files)

    def __getitem__(self, idx):
        fname = self.files[idx]
        base = fname[:-4]
        img = cv2.cvtColor(cv2.imread(os.path.join(self.root, "images", fname)), cv2.COLOR_BGR2RGB)
        depth = cv2.imread(os.path.join(self.root, "depth", fname), cv2.IMREAD_UNCHANGED).astype(np.float32) / 65535.0
        mask_def = cv2.imread(os.path.join(self.root, "masks", base + "_def.png"), cv2.IMREAD_GRAYSCALE)
        with open(os.path.join(self.root, "labels", base + ".json"), encoding="utf-8") as f:
            label = json.load(f)

        return {
            "image": torch.from_numpy(img).permute(2, 0, 1).float() / 255.0,
            "depth": torch.from_numpy(depth).unsqueeze(0),
            "mask": torch.from_numpy(mask_def).unsqueeze(0).float() / 255.0,
            "class_id": torch.tensor(label["class_id"], dtype=torch.long),
        }
