# OP01 Pass B — image-asset verification report

_Generated 2026-05-17T18:26:11Z — read-only audit_

## Disk existence — `card_variants.image_path` vs filesystem

Asset roots checked: `D:\OPTCG_Images` (canonical), `D:\Miru_Assets` (thumbs).
Path fallback rules applied: flatten `OP01/base/` and `OP01/parallel/` to `OP01/`; try `.png/.jpg/.jpeg/.webp` extensions.

| Category         | On-disk |   Total | Missing |
| ---------------- | ------: | ------: | ------: |
| bandai_canonical |     182 |     218 |      36 |
| r_variant        |       0 |      17 |      17 |
| synthetic        |     113 |     113 |       0 |
| **TOTAL**        | **295** | **348** |  **53** |

### Missing on disk (53 rows)

| print_id    | category         | image_path                         |
| ----------- | ---------------- | ---------------------------------- |
| OP01-001_p2 | bandai_canonical | PCC25/parallel/OP01-001_p2.png     |
| OP01-004_p1 | bandai_canonical | ANN1EN/parallel/OP01-004_p1.png    |
| OP01-005_p1 | bandai_canonical | PCCFR/parallel/OP01-005_p1.png     |
| OP01-005_p2 | bandai_canonical | GC01/parallel/OP01-005_p2.png      |
| OP01-006_p1 | bandai_canonical | ANN1JP/parallel/OP01-006_p1.png    |
| OP01-006_r1 | r_variant        | PRB01/reprint/OP01-006_r1.png      |
| OP01-013_p2 | bandai_canonical | PCC25/parallel/OP01-013_p2.png     |
| OP01-013_p3 | bandai_canonical | GC01/parallel/OP01-013_p3.png      |
| OP01-013_p4 | bandai_canonical | ANN1EN/parallel/OP01-013_p4.png    |
| OP01-014_p1 | bandai_canonical | PCCFR/parallel/OP01-014_p1.png     |
| OP01-015_p1 | bandai_canonical | ORP2024V3/parallel/OP01-015_p1.png |
| OP01-016_p2 | bandai_canonical | PCC25/parallel/OP01-016_p2.png     |
| OP01-016_p3 | bandai_canonical | OP01/parallel/OP01-016_p3.png      |
| OP01-016_p4 | bandai_canonical | OP05/parallel/OP01-016_p4.png      |
| OP01-016_p5 | bandai_canonical | GC01/parallel/OP01-016_p5.png      |
| OP01-016_p7 | bandai_canonical | OP01/parallel/OP01-016_p7.png      |
| OP01-016_p8 | bandai_canonical | OP01/parallel/OP01-016_p8.png      |
| OP01-017_p1 | bandai_canonical | PCCFR/parallel/OP01-017_p1.png     |
| OP01-017_p2 | bandai_canonical | ANN1EN/parallel/OP01-017_p2.png    |
| OP01-021_p1 | bandai_canonical | PCCFR/parallel/OP01-021_p1.png     |
| OP01-021_p2 | bandai_canonical | TP02/parallel/OP01-021_p2.png      |
| OP01-021_p3 | bandai_canonical | GC01/parallel/OP01-021_p3.png      |
| OP01-022_p1 | bandai_canonical | PCC25/parallel/OP01-022_p1.png     |
| OP01-024_r1 | r_variant        | PRB01/reprint/OP01-024_r1.png      |
| OP01-025_p2 | bandai_canonical | OP01/parallel/OP01-025_p2.png      |
| OP01-025_p3 | bandai_canonical | ANN1EN/parallel/OP01-025_p3.png    |
| OP01-029_p4 | bandai_canonical | BCV1/parallel/OP01-029_p4.png      |
| OP01-029_r1 | r_variant        | PRB01/reprint/OP01-029_r1.png      |
| OP01-030_p1 | bandai_canonical | ANN2JP/parallel/OP01-030_p1.png    |
| OP01-033_p1 | bandai_canonical | TP02/parallel/OP01-033_p1.png      |
| OP01-033_p4 | bandai_canonical | PRB01/parallel/OP01-033_p4.png     |
| OP01-033_r1 | r_variant        | PRB01/reprint/OP01-033_r1.png      |
| OP01-035_p1 | bandai_canonical | TP04/parallel/OP01-035_p1.png      |
| OP01-039_r1 | r_variant        | PRB02/reprint/OP01-039_r1.png      |
| OP01-041_p1 | bandai_canonical | TP07/parallel/OP01-041_p1.png      |
| OP01-041_p3 | bandai_canonical | PRB01/parallel/OP01-041_p3.png     |
| OP01-041_p4 | bandai_canonical | PRB01/parallel/OP01-041_p4.png     |
| OP01-041_r1 | r_variant        | PRB01/reprint/OP01-041_r1.png      |
| OP01-047_r1 | r_variant        | PRB01/reprint/OP01-047_r1.png      |
| OP01-051_r1 | r_variant        | PRB01/reprint/OP01-051_r1.png      |
| OP01-052_p1 | bandai_canonical | EP02/parallel/OP01-052_p1.png      |
| OP01-052_r1 | r_variant        | PRB01/reprint/OP01-052_r1.png      |
| OP01-055_r1 | r_variant        | PRB02/reprint/OP01-055_r1.png      |
| OP01-057_p1 | bandai_canonical | BCV1/parallel/OP01-057_p1.png      |
| OP01-060_p2 | bandai_canonical | ST17/parallel/OP01-060_p2.png      |
| OP01-070_r1 | r_variant        | PRB01/reprint/OP01-070_r1.png      |
| OP01-073_r1 | r_variant        | ST17/reprint/OP01-073_r1.png       |
| OP01-078_r1 | r_variant        | PRB01/reprint/OP01-078_r1.png      |
| OP01-086_r1 | r_variant        | ST17/reprint/OP01-086_r1.png       |
| OP01-101_p1 | bandai_canonical | EP02/parallel/OP01-101_p1.png      |
| OP01-120_r1 | r_variant        | PRB01/reprint/OP01-120_r1.png      |
| OP01-120_r2 | r_variant        | PRB01/reprint/OP01-120_r2.png      |
| OP01-121_r1 | r_variant        | PRB01/reprint/OP01-121_r1.png      |

## Bandai CDN HEAD checks — 17 `_r1`/`_r2` rare-art URLs

| print_id    | HTTP status | content-length |
| ----------- | ----------: | -------------: |
| OP01-006_r1 |         200 |         187694 |
| OP01-024_r1 |         200 |         155845 |
| OP01-029_r1 |         200 |         149672 |
| OP01-033_r1 |         200 |         119071 |
| OP01-039_r1 |         200 |        1703606 |
| OP01-041_r1 |         200 |         137968 |
| OP01-047_r1 |         200 |         171127 |
| OP01-051_r1 |         200 |         154608 |
| OP01-052_r1 |         200 |         135973 |
| OP01-055_r1 |         200 |         999387 |
| OP01-070_r1 |         200 |         227947 |
| OP01-073_r1 |         200 |         156003 |
| OP01-078_r1 |         200 |         166747 |
| OP01-086_r1 |         200 |         173942 |
| OP01-120_r1 |         200 |         203229 |
| OP01-120_r2 |         200 |         186812 |
| OP01-121_r1 |         200 |         152009 |

**Summary:** 17/17 `_r` URLs returned HTTP 200.
