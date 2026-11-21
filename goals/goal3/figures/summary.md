# Goal 3 — best design per architecture family

Each family's best point by each measure (they can differ). Time per round under the `helios` table; LER per round = 1-(1-p_Z)(1-p_X) under qccd-noise@3, 95% interval.

## bivariate bicycle code [[72,12,6]], 6 rounds, 144 ions

| family | sizes compiled | fastest | ms / round | lowest LER | LER / round [95%] |
|---|---|---|---|---|---|
| C2LR | 12 of 12 | c2lr15x14 | 352.4 | c2lr15x14 | 1.35e-03 [1.2e-03, 1.5e-03] |
| WISE | 12 of 12 | wise_h3_8x7 | 1150.6 | wise_h3_8x7 | 2.81e-02 [2.7e-02, 3.0e-02] |
| Cyclone | 5 of 5 | cyclone10_n16 | 379.8 | cyclone6_n24 | 5.22e-01 [4.6e-01, 5.8e-01] |
| Conveyor | 2 of 4 | conveyor96_96d_j | 256.5 | conveyor96_96d_j | 7.58e-04 [6.6e-04, 8.7e-04] |

## 6.6.6 colour code [[7,1,3]], 3 rounds, 10 ions

| family | sizes compiled | fastest | ms / round | lowest LER | LER / round [95%] |
|---|---|---|---|---|---|
| C2LR | 3 of 3 | c2lr4x3 | 57.2 | c2lr4x3 | 8.10e-03 [7.1e-03, 9.2e-03] |
| WISE | 7 of 7 | wise_h5_2x2 | 81.7 | wise_h3_3x2 | 8.71e-03 [7.7e-03, 9.8e-03] |
| Cyclone | 4 of 4 | cyclone2_n6 | 19.0 | cyclone2_n6 | 1.17e-02 [1.1e-02, 1.3e-02] |
| Helios | 1 of 1 | helios98 | 74.5 | helios98 | 9.00e-03 [7.9e-03, 1.0e-02] |
| Conveyor | 2 of 2 | conveyor9_3d_j | 53.5 | conveyor9_3d_j | 8.03e-03 [7.0e-03, 9.2e-03] |

## 6.6.6 colour code [[19,1,5]], 5 rounds, 28 ions

| family | sizes compiled | fastest | ms / round | lowest LER | LER / round [95%] |
|---|---|---|---|---|---|
| C2LR | 6 of 6 | c2lr7x7 | 73.2 | c2lr7x7 | 4.56e-04 [4.0e-04, 5.2e-04] |
| WISE | 10 of 10 | wise_h3_4x3 | 148.1 | wise_h5_4x4 | 6.26e-04 [5.5e-04, 7.2e-04] |
| Cyclone | 6 of 6 | cyclone2_n15 | 58.3 | cyclone2_n15 | 6.40e-03 [5.7e-03, 7.1e-03] |
| Helios | 1 of 1 | helios98 | 252.4 | helios98 | 9.82e-04 [8.6e-04, 1.1e-03] |
| Conveyor | 2 of 2 | conveyor24_12d_j | 186.5 | conveyor27_9d_j | 6.46e-04 [5.6e-04, 7.4e-04] |

## 6.6.6 colour code [[37,1,7]], 7 rounds, 55 ions

| family | sizes compiled | fastest | ms / round | lowest LER | LER / round [95%] |
|---|---|---|---|---|---|
| C2LR | 7 of 7 | c2lr9x9 | 128.2 | c2lr9x8 | 2.38e-04 [2.1e-04, 2.7e-04] |
| WISE | 12 of 12 | wise_h3_7x7 | 268.8 | wise_h7_5x4 | 3.75e-04 [3.3e-04, 4.3e-04] |
| Cyclone | 6 of 6 | cyclone2_n28 | 136.7 | cyclone2_n28 | 5.21e-03 [4.7e-03, 5.8e-03] |
| Helios | 1 of 1 | helios98 | 710.0 | helios98 | 2.15e-03 [1.9e-03, 2.5e-03] |
| Conveyor | 4 of 4 | conveyor36_24d_j | 338.8 | conveyor36_24d_j | 3.47e-04 [3.0e-04, 4.0e-04] |

## hypergraph product code (Hamming [7,4,3]) [[58,16,3]], 3 rounds, 100 ions

| family | sizes compiled | fastest | ms / round | lowest LER | LER / round [95%] |
|---|---|---|---|---|---|
| C2LR | 10 of 10 | c2lr13x12 | 194.9 | c2lr9x8 | 4.00e-02 [3.8e-02, 4.2e-02] |
| WISE | 12 of 12 | wise_h3_9x9 | 492.1 | wise_h3_9x9 | 6.40e-02 [6.1e-02, 6.7e-02] |
| Cyclone | 6 of 6 | cyclone4_n26 | 352.8 | cyclone4_n26 | 4.93e-01 [4.8e-01, 5.0e-01] |
| Conveyor | 2 of 2 | conveyor84_56d_j | 466.3 | conveyor84_56d_j | 5.67e-02 [5.0e-02, 6.4e-02] |

## rotated surface code [[9,1,3]], 3 rounds, 17 ions

| family | sizes compiled | fastest | ms / round | lowest LER | LER / round [95%] |
|---|---|---|---|---|---|
| C2LR | 4 of 4 | c2lr5x4 | 18.2 | c2lr4x4 | 2.69e-04 [2.3e-04, 3.1e-04] |
| WISE | 8 of 8 | wise_h5_3x2 | 41.4 | wise_h3_4x3 | 2.79e-04 [2.4e-04, 3.2e-04] |
| Cyclone | 5 of 5 | cyclone2_n9 | 22.0 | cyclone2_n9 | 6.10e-03 [5.5e-03, 6.8e-03] |
| Helios | 1 of 1 | helios98 | 65.4 | helios98 | 4.14e-04 [3.6e-04, 4.8e-04] |
| Conveyor | 3 of 3 | conveyor22_11d_j | 44.6 | conveyor22_11d_j | 2.99e-04 [2.6e-04, 3.4e-04] |

## rotated surface code [[25,1,5]], 5 rounds, 49 ions

| family | sizes compiled | fastest | ms / round | lowest LER | LER / round [95%] |
|---|---|---|---|---|---|
| C2LR | 7 of 7 | c2lr7x7 | 41.0 | c2lr9x9 | 7.35e-05 [6.4e-05, 8.4e-05] |
| WISE | 12 of 12 | wise_h3_6x5 | 105.4 | wise_h3_6x5 | 1.09e-04 [9.5e-05, 1.2e-04] |
| Cyclone | 6 of 6 | cyclone4_n13 | 63.8 | cyclone2_n25 | 2.29e-03 [2.0e-03, 2.6e-03] |
| Helios | 1 of 1 | helios98 | 304.8 | helios98 | 4.80e-04 [4.2e-04, 5.5e-04] |
| Conveyor | 4 of 4 | conveyor32_32d_j | 86.0 | conveyor32_32d_j | 8.11e-05 [7.1e-05, 9.3e-05] |

## rotated surface code [[49,1,7]], 7 rounds, 97 ions

| family | sizes compiled | fastest | ms / round | lowest LER | LER / round [95%] |
|---|---|---|---|---|---|
| C2LR | 10 of 10 | c2lr12x12 | 59.3 | c2lr12x12 | 3.16e-05 [2.7e-05, 3.6e-05] |
| WISE | 12 of 12 | wise_h3_10x9 | 240.2 | wise_h3_10x9 | 8.22e-05 [7.2e-05, 9.4e-05] |
| Cyclone | 6 of 6 | cyclone6_n17 | 165.0 | cyclone4_n25 | 1.81e-02 [1.7e-02, 1.9e-02] |
| Helios | 0 of 1 | — | — | — | — |
| Conveyor | 4 of 4 | conveyor64_64d_j | 170.9 | conveyor64_64d_j | 5.37e-05 [4.7e-05, 6.2e-05] |

