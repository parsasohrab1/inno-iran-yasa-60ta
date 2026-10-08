# data/

All datasets and trained weights live here. Contents are git-ignored (large); only the READMEs and `.gitkeep` files are tracked.

| Path | Content |
|---|---|
| `synthetic/` | Output of `ai.datagen.synthetic_data_generator --out data/synthetic` |
| `yolo/` | YOLO-format conversion (`ai.training.convert_to_yolo --src data/synthetic --dst data/yolo`) |
| `real/<source>/` | Public/real datasets, one folder each, with licence and origin recorded in `real/SOURCES.md` |
| `models/` | Trained weights / ONNX exports |

Real data must stay separate from synthetic data so validation numbers are reported on real images only.
