# Goal 3 — best design per architecture family

Each family's best point by each measure (they can differ). Time per round under the `quantinuum` table; LER per round = 1-(1-p_Z)(1-p_X) under qccd-noise@2, 95% interval.

## bivariate bicycle code [[72,12,6]], 6 rounds, 144 ions

| family | sizes compiled | fastest | ms / round | lowest LER | LER / round [95%] |
|---|---|---|---|---|---|
| Grid | 0 of 12 | — | — | — | — |
| WISE | 0 of 12 | — | — | — | — |
| C2LR | 12 of 12 | c2lr15x14 | 291.7 | c2lr15x14 | 1.86e-03 [1.6e-03, 2.1e-03] |
| Cyclone | 4 of 4 | cyclone75_j | 5588.5 | cyclone83_j | 7.41e-01 [6.9e-01, 7.8e-01] |
| Conveyor | 2 of 4 | conveyor96_96d_j | 405.6 | conveyor96_96d_j | 4.19e-04 [3.7e-04, 4.8e-04] |

## 6.6.6 colour code [[7,1,3]], 3 rounds, 10 ions

| family | sizes compiled | fastest | ms / round | lowest LER | LER / round [95%] |
|---|---|---|---|---|---|
| Grid | 3 of 3 | grid4x3 | 19.4 | grid4x3 | 7.35e-03 [6.4e-03, 8.4e-03] |
| WISE | 3 of 3 | wise4x3 | 19.4 | wise4x3 | 7.35e-03 [6.4e-03, 8.4e-03] |
| C2LR | 3 of 3 | c2lr4x3 | 30.9 | c2lr4x4 | 1.39e-02 [1.3e-02, 1.5e-02] |
| Cyclone | 3 of 3 | cyclone6_j | 19.2 | cyclone8_j | 7.62e-03 [6.7e-03, 8.7e-03] |
| Conveyor | 2 of 2 | conveyor9_3d_j | 48.2 | conveyor9_3d_j | 7.90e-03 [7.0e-03, 9.0e-03] |

## 6.6.6 colour code [[19,1,5]], 5 rounds, 28 ions

| family | sizes compiled | fastest | ms / round | lowest LER | LER / round [95%] |
|---|---|---|---|---|---|
| Grid | 6 of 6 | grid7x6 | 61.7 | grid6x5 | 3.86e-04 [3.4e-04, 4.4e-04] |
| WISE | 6 of 6 | wise7x6 | 61.7 | wise6x5 | 3.86e-04 [3.4e-04, 4.4e-04] |
| C2LR | 6 of 6 | c2lr7x7 | 41.6 | c2lr7x6 | 1.30e-03 [1.1e-03, 1.5e-03] |
| Cyclone | 4 of 4 | cyclone15_j | 335.0 | cyclone15_j | 1.02e-03 [8.9e-04, 1.2e-03] |
| Conveyor | 2 of 2 | conveyor24_12d_j | 286.2 | conveyor27_9d_j | 7.44e-04 [6.5e-04, 8.5e-04] |

## 6.6.6 colour code [[37,1,7]], 7 rounds, 55 ions

| family | sizes compiled | fastest | ms / round | lowest LER | LER / round [95%] |
|---|---|---|---|---|---|
| Grid | 7 of 7 | grid9x8 | 91.7 | grid7x7 | 1.20e-04 [1.0e-04, 1.4e-04] |
| WISE | 7 of 7 | wise9x8 | 91.7 | wise7x7 | 1.20e-04 [1.0e-04, 1.4e-04] |
| C2LR | 7 of 7 | c2lr9x9 | 100.9 | c2lr9x8 | 6.41e-04 [5.6e-04, 7.3e-04] |
| Cyclone | 4 of 4 | cyclone29_j | 1408.6 | cyclone29_j | 1.97e-02 [1.9e-02, 2.1e-02] |
| Conveyor | 4 of 4 | conveyor36_24d_j | 552.1 | conveyor36_24d_j | 8.38e-04 [7.4e-04, 9.5e-04] |

## hypergraph product code (Hamming [7,4,3]) [[58,16,3]], 3 rounds, 100 ions

| family | sizes compiled | fastest | ms / round | lowest LER | LER / round [95%] |
|---|---|---|---|---|---|
| Grid | 7 of 10 | grid12x12 | 206.7 | grid12x12 | 2.60e-02 [2.4e-02, 2.8e-02] |
| WISE | 7 of 10 | wise12x12 | 206.7 | wise12x12 | 2.60e-02 [2.4e-02, 2.8e-02] |
| C2LR | 10 of 10 | c2lr13x12 | 160.2 | c2lr12x11 | 3.63e-02 [3.4e-02, 3.9e-02] |
| Cyclone | 4 of 4 | cyclone52_j | 3453.5 | cyclone65_j | 9.54e-01 [9.2e-01, 9.7e-01] |
| Conveyor | 2 of 2 | conveyor84_56d_j | 853.8 | conveyor84_56d_j | 1.15e-01 [1.1e-01, 1.2e-01] |

## rotated surface code [[9,1,3]], 3 rounds, 17 ions

| family | sizes compiled | fastest | ms / round | lowest LER | LER / round [95%] |
|---|---|---|---|---|---|
| Grid | 4 of 4 | grid6x5 | 19.3 | grid6x5 | 2.17e-04 [1.9e-04, 2.5e-04] |
| WISE | 4 of 4 | wise6x5 | 19.3 | wise6x5 | 2.17e-04 [1.9e-04, 2.5e-04] |
| C2LR | 4 of 4 | c2lr5x4 | 8.5 | c2lr5x4 | 6.25e-04 [5.4e-04, 7.2e-04] |
| Cyclone | 4 of 4 | cyclone10_j | 46.3 | cyclone9_j | 2.58e-04 [2.3e-04, 3.0e-04] |
| Conveyor | 3 of 3 | conveyor22_11d_j | 53.9 | conveyor16_8d_j | 2.81e-04 [2.4e-04, 3.2e-04] |

## rotated surface code [[25,1,5]], 5 rounds, 49 ions

| family | sizes compiled | fastest | ms / round | lowest LER | LER / round [95%] |
|---|---|---|---|---|---|
| Grid | 7 of 7 | grid9x8 | 46.7 | grid8x8 | 2.67e-05 [2.3e-05, 3.1e-05] |
| WISE | 7 of 7 | wise9x8 | 46.7 | wise8x8 | 2.67e-05 [2.3e-05, 3.1e-05] |
| C2LR | 7 of 7 | c2lr7x7 | 35.1 | c2lr7x6 | 1.78e-04 [1.6e-04, 2.0e-04] |
| Cyclone | 4 of 4 | cyclone26_j | 304.1 | cyclone26_j | 2.80e-04 [2.4e-04, 3.2e-04] |
| Conveyor | 4 of 4 | conveyor32_32d_j | 112.7 | conveyor32_32d_j | 5.14e-05 [4.5e-05, 5.9e-05] |

## rotated surface code [[49,1,7]], 7 rounds, 97 ions

| family | sizes compiled | fastest | ms / round | lowest LER | LER / round [95%] |
|---|---|---|---|---|---|
| Grid | 10 of 10 | grid12x11 | 59.5 | grid12x11 | 4.14e-06 [3.2e-06, 5.4e-06] |
| WISE | 10 of 10 | wise12x11 | 59.5 | wise12x11 | 4.14e-06 [3.2e-06, 5.4e-06] |
| C2LR | 10 of 10 | c2lr12x12 | 55.8 | c2lr11x11 | 6.38e-05 [5.6e-05, 7.3e-05] |
| Cyclone | 4 of 4 | cyclone51_j | 1117.4 | cyclone73_j | 1.45e-02 [1.4e-02, 1.5e-02] |
| Conveyor | 4 of 4 | conveyor64_64d_j | 259.5 | conveyor64_64d_j | 3.76e-05 [3.3e-05, 4.3e-05] |

