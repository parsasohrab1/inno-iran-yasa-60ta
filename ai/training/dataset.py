import os, json
import numpy as np
import cv2
import torch
from torch.utils.data import Dataset, DataLoader

CLASS_MAP = {0:"healthy",1:"crack",2:"bubble",3:"deformation",
             4:"foreign_particle",5:"discoloration",6:"flash",7:"incomplete_fill"}

class RubberDefectDataset(Dataset):
    def __init__(self, root, split="train", transform=None):
        self.root = os.path.join(root, split)
        self.files = [f for f in os.listdir(os.path.join(self.root, "images"))
                      if f.endswith(".png")]
        self.transform = transform

    def __len__(self):
        return len(self.files)

    def __getitem__(self, idx):
        fname = self.files[idx]
        base = fname.replace(".png", "")
        img = cv2.cvtColor(cv2.imread(os.path.join(self.root, "images", fname)),
                           cv2.COLOR_BGR2RGB)
        depth = cv2.imread(os.path.join(self.root, "depth", fname),
                           cv2.IMREAD_UNCHANGED).astype(np.float32) / 65535.0
        mask_def = cv2.imread(os.path.join(self.root, "masks", base + "_def.png"),
                              cv2.IMREAD_GRAYSCALE)
        with open(os.path.join(self.root, "labels", base + ".json"), encoding="utf-8") as f:
            label = json.load(f)

        img_t = torch.from_numpy(img).permute(2,0,1).float() / 255.0
        depth_t = torch.from_numpy(depth).unsqueeze(0)
        mask_t = torch.from_numpy(mask_def).unsqueeze(0).float() / 255.0

        return {
            "image": img_t,
            "depth": depth_t,
            "mask": mask_t,
            "class_id": torch.tensor(label["class_id"], dtype=torch.long),
            "bboxes": torch.tensor(label["bboxes"], dtype=torch.float32)
                      if label["bboxes"] else torch.zeros((0,4)),
        }

# Usage
ds = RubberDefectDataset("./synthetic_rubber_dataset", split="train")
dl = DataLoader(ds, batch_size=16, shuffle=True, num_workers=4)
print(f"Train samples: {len(ds)}")
