# Real-data sources (candidates — mark as downloaded only after the file is actually in place)

| Source | Relevance | Licence | Status |
|---|---|---|---|
| Roboflow Universe "oringdefect" (o-ring-defect-detection) | Rubber O-rings; crack, cutting, inner-stamped defects | CC BY 4.0 | not downloaded |
| Roboflow Universe "o-ring data" (o-ring-defect-detection/o-ring-data-6ypfi) | Rubber O-rings | CC BY 4.0 | not downloaded |
| Roboflow Universe "Everint_Rubber_dataset_00" | Rubber defects | check page | not downloaded |
| KolektorSDD2 (ViCoS) | Real industrial surface defects (commutators) — transfer/pre-training only | research use, see page | not downloaded |

None of these match the factory's own parts or its 8 defect classes exactly; class mapping to the SRS taxonomy is manual.

Roboflow Universe pages return HTTP 403 to automated fetches and downloads need a Roboflow account/API key, so the three Roboflow sets must be exported by the user into `data/real/<name>/` (YOLOv8 format).
