# Validation Protocol — Path to TRL 5

**Goal:** demonstrate the inspection system in a *relevant environment* (TRL 5): the integrated pipeline, tested on representative real parts, with realistic imaging and line pace. This document defines how; it does not claim the result. Until the evidence below exists, the system is at best TRL 3–4 (components proven on synthetic/public data and in software tests).

## 1. Current evidence vs. TRL 5 requirement
| Needed for TRL 5 | Status |
|---|---|
| Software pipeline integrated end-to-end (acquisition → detector → decision → DB → dashboard) | Built; unit/API tested; replay-tested on synthetic data |
| Detector validated on **real** images of **Iran Yasa** products | **Missing** — only synthetic + public non-matching data |
| Imaging hardware (2 × 2D, 1 × 3D, lighting, trigger) integrated | **Missing** — simulated/replayed only |
| Run at line pace in a representative environment | **Missing** |
| Acceptance metrics measured with statistical confidence | **Missing** |

## 2. Phases and exit criteria
**Phase A — Offline replay HIL (can run now).** Replay a held-out labelled set through the real pipeline with `python -m ai.validation.hil_replay`. Verifies software behaviour, latency on the target compute, error handling. *Exit:* all checks in §4 pass on the replay set **and** the set contains real images (synthetic-only passes do not count).

**Phase B — Lab bench with real parts.** Install camera(s)/lighting on a bench; capture ≥ the sample sizes in §3 from golden and defective parts; calibrate (FR-1-4); build reference template (FR-1-5). *Exit:* detector fine-tuned on the training portion passes §4 on the **blind test set**.

**Phase C — Shadow mode on the line.** System runs on the live line for ≥ 72 h at line pace, **not** controlling rejects; QC inspects every NG/Review and a random ≥ 5 % sample of OK parts. *Exit:* §4 criteria hold on shadow-mode data; uptime ≥ 99 % over 72 h (SRS §8.5).

**Phase D — Supervised pilot.** Decisions drive operator hand-off under supervision for ≥ 4 weeks. TRL 5 is declared when Phases A–C evidence is reviewed and signed off (§7). (Phase D contributes towards TRL 6.)

## 3. Data and sample sizes
- **Blind test set:** real parts only, collected separately from any training/validation image, never used for tuning, thresholds, or model selection. Split by **physical part / production lot**, not by image, to avoid leakage between augmented or repeated captures.
- **Defect-positive images:** to show false-negative rate ≤ 1 % with 95 % confidence by the "rule of three", need **≥ 299 defective parts with zero misses** (or more positives if any miss occurs: e.g. ≤ 1 miss needs ≈ 473). Count critical classes (crack, foreign particle, incomplete fill) separately: ≥ 299 each if the ≤ 1 % claim is made per class, otherwise ≥ 299 pooled and report per-class recall with its confidence interval.
- **Defect-negative images:** ≥ 1,000 good parts across ≥ 3 production lots / shifts, for false-reject rate.
- **Report confidence intervals** (Wilson, 95 %) for every rate; a point estimate alone is not evidence.
- Label QA: two independent QC labelers, disagreements adjudicated by a senior QC engineer; record Cohen's κ.
- Class coverage: all 8 SRS classes; any class with < 50 real examples is reported as *not validated* (few-shot, FR-2-3).

## 4. Acceptance criteria (from SRS §2-3, §5, §8)
| # | Metric | Target | Measured by |
|---|---|---|---|
| 1 | mAP@0.5 | ≥ 0.90 | `yolo val` on blind test set |
| 2 | Recall, critical defects (image-level flagged) | ≥ 0.95 | `hil_replay` |
| 3 | False-negative rate (defective part passed as OK) | ≤ 1 % | `hil_replay` |
| 4 | Precision / recall (object level, conf ≥ 0.7) | ≥ 0.92 / ≥ 0.90 | `hil_replay` |
| 5 | Inference latency p95 per part | ≤ 100 ms | `hil_replay` on **target compute** |
| 6 | Throughput | ≥ 30 parts/min | `hil_replay --rate 0`, then `--rate 30` |
| 7 | Uptime over 72 h | ≥ 99 % | Phase C logs + `/metrics` |
| 8 | Dashboard load | ≤ 2 s | browser timing under load |
| 9 | Sensor time sync | ≤ 1 ms | hardware timestamp audit (Phase B) |

A criterion with no measurement ("N/A") is **not passed**.
Note: `hil_replay` counts *Review* as flagged (a human stops the part). Report separately the Review rate — a system that sends everything to Review trivially has 0 FN but is not useful; target Review rate agreed with QC (suggested ≤ 5 %).

## 5. Test cases (beyond metrics)
| ID | Case | Expected |
|---|---|---|
| T1 | Lighting ± 20 %, colour-temperature shift | Metrics within 2 pts of nominal |
| T2 | Part misalignment / rotation (FR-1-8) | Alarm raised or detection unaffected |
| T3 | Camera disconnect / corrupt frame | Error logged, line loop continues, uptime accounted |
| T4 | Backend/DB outage 60 s | Frames buffered or dropped with count; recovery without manual action |
| T5 | New product variant | Reference template rebuilt; metrics re-validated before release |
| T6 | Model/threshold change | Full §4 re-run on blind set before deployment (no silent updates) |
| T7 | RBAC (operator cannot review/export) | Covered by automated API tests |
| T8 | 72 h soak | No memory growth > 10 %, no crash |

## 6. Records and traceability
Keep for every run: model hash, commit SHA, dataset manifest (file list + hash), threshold settings, hardware/software versions, raw `hil_report.json`, operator, date. Every NG/Review decision stores timestamp, part id, line, shift (FR-3-1) and the reviewer's verdict; reviewed labels are the source for retraining and must not leak into the blind set.

## 7. Sign-off checklist (TRL 5 exit)
- [ ] Blind real-data test set frozen and hashed
- [ ] §4 criteria 1–7 pass with confidence intervals reported
- [ ] Phase C 72 h shadow run complete, QC-verified sample reported
- [ ] T1–T8 executed, deviations recorded
- [ ] Hardware (cameras, 3D sensor, lighting, PLC trigger) integrated and documented
- [ ] Reviewed by QC lead + engineering lead; signed and dated

## 8. Known limitations at time of writing
Training so far uses synthetic images, whose defects are procedurally drawn; high synthetic scores say little about real performance. Public datasets (O-rings, commutators) differ from Iran Yasa products and serve only for pre-training/sanity checks.
