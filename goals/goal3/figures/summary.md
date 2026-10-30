# Goal 3 — best design per architecture family

Each family's best point by each measure (they can differ). Time per round under the `quantinuum` table; LER per round = 1-(1-p_Z)(1-p_X) under qccd-noise@2, 95% interval.

## 6.6.6 colour code [[7,1,3]], 3 rounds, 10 ions

| family | sizes compiled | fastest | ms / round | lowest LER | LER / round [95%] |
|---|---|---|---|---|---|
| Grid | 3 of 3 | grid4x3 | 19.3 | grid4x3 | 7.70e-03 [6.8e-03, 8.8e-03] |
| WISE | 3 of 3 | wise4x3 | 19.3 | wise4x3 | 7.70e-03 [6.8e-03, 8.8e-03] |
| C2LR | 0 of 3 | — | — | — | — |
| Cyclone | 3 of 3 | cyclone6 | 26.1 | cyclone6 | 8.22e-03 [7.3e-03, 9.3e-03] |
| Conveyor | 2 of 2 | conveyor8_4d | 33.9 | conveyor8_4d | 7.78e-03 [6.8e-03, 8.8e-03] |

## 6.6.6 colour code [[19,1,5]], 5 rounds, 28 ions

| family | sizes compiled | fastest | ms / round | lowest LER | LER / round [95%] |
|---|---|---|---|---|---|
| Grid | 6 of 6 | grid7x6 | 61.3 | grid6x5 | 3.65e-04 [3.2e-04, 4.2e-04] |
| WISE | 6 of 6 | wise7x6 | 61.3 | wise6x5 | 3.65e-04 [3.2e-04, 4.2e-04] |
| C2LR | 0 of 6 | — | — | — | — |
| Cyclone | 4 of 4 | cyclone15 | 301.4 | cyclone15 | 8.21e-04 [7.2e-04, 9.4e-04] |
| Conveyor | 2 of 2 | conveyor24_12d | 186.8 | conveyor27_9d | 8.04e-04 [7.0e-04, 9.2e-04] |

## 6.6.6 colour code [[37,1,7]], 7 rounds, 55 ions

| family | sizes compiled | fastest | ms / round | lowest LER | LER / round [95%] |
|---|---|---|---|---|---|
| Grid | 7 of 7 | grid9x8 | 89.7 | grid9x8 | 1.13e-04 [9.9e-05, 1.3e-04] |
| WISE | 7 of 7 | wise9x8 | 89.7 | wise9x8 | 1.13e-04 [9.9e-05, 1.3e-04] |
| C2LR | 0 of 7 | — | — | — | — |
| Cyclone | 4 of 4 | cyclone29 | 1361.0 | cyclone29 | 1.71e-02 [1.6e-02, 1.8e-02] |
| Conveyor | 4 of 4 | conveyor36_24d | 387.8 | conveyor36_24d | 8.21e-04 [7.2e-04, 9.4e-04] |

## rotated surface code [[9,1,3]], 3 rounds, 17 ions

| family | sizes compiled | fastest | ms / round | lowest LER | LER / round [95%] |
|---|---|---|---|---|---|
| Grid | 4 of 4 | grid4x4 | 19.6 | grid5x5 | 2.00e-04 [1.7e-04, 2.3e-04] |
| WISE | 4 of 4 | wise4x4 | 19.6 | wise5x5 | 2.00e-04 [1.7e-04, 2.3e-04] |
| C2LR | 0 of 4 | — | — | — | — |
| Cyclone | 4 of 4 | cyclone10 | 37.5 | cyclone9 | 2.35e-04 [2.0e-04, 2.7e-04] |
| Conveyor | 3 of 3 | conveyor12_8d | 38.8 | conveyor22_11d | 2.61e-04 [2.3e-04, 3.0e-04] |

## rotated surface code [[25,1,5]], 5 rounds, 49 ions

| family | sizes compiled | fastest | ms / round | lowest LER | LER / round [95%] |
|---|---|---|---|---|---|
| Grid | 7 of 7 | grid9x8 | 46.7 | grid9x8 | 2.52e-05 [2.1e-05, 3.0e-05] |
| WISE | 7 of 7 | wise9x8 | 46.7 | wise9x8 | 2.52e-05 [2.1e-05, 3.0e-05] |
| C2LR | 0 of 7 | — | — | — | — |
| Cyclone | 4 of 4 | cyclone26 | 291.4 | cyclone26 | 2.54e-04 [2.2e-04, 2.9e-04] |
| Conveyor | 4 of 4 | conveyor32_32d | 106.3 | conveyor32_32d | 5.26e-05 [4.6e-05, 6.1e-05] |

## rotated surface code [[49,1,7]], 7 rounds, 97 ions

| family | sizes compiled | fastest | ms / round | lowest LER | LER / round [95%] |
|---|---|---|---|---|---|
| Grid | 7 of 10 | grid10x9 | 68.6 | grid10x9 | 3.57e-06 [2.5e-06, 5.2e-06] |
| WISE | 7 of 10 | wise10x9 | 68.6 | wise10x9 | 3.57e-06 [2.5e-06, 5.2e-06] |
| C2LR | 0 of 10 | — | — | — | — |
| Cyclone | 4 of 4 | cyclone51 | 1083.7 | cyclone51 | 1.25e-02 [1.2e-02, 1.3e-02] |
| Conveyor | 4 of 4 | conveyor64_64d | 267.8 | conveyor64_64d | 4.70e-05 [4.1e-05, 5.4e-05] |

