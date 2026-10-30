# Goal 3 — best design per architecture family

Each family's best point by each measure (they can differ). Time per round under the `quantinuum` table; LER per round = 1-(1-p_Z)(1-p_X) under qccd-noise@2, 95% interval.

## 6.6.6 colour code [[7,1,3]], 3 rounds, 10 ions

| family | sizes compiled | fastest | ms / round | lowest LER | LER / round [95%] |
|---|---|---|---|---|---|
| Grid | 3 of 3 | grid4x3 | 19.4 | grid4x3 | 7.35e-03 [6.4e-03, 8.4e-03] |
| WISE | 3 of 3 | wise4x3 | 19.4 | wise4x3 | 7.35e-03 [6.4e-03, 8.4e-03] |
| C2LR | 3 of 3 | c2lr4x3 | 30.9 | c2lr4x4 | 1.39e-02 [1.3e-02, 1.5e-02] |
| Cyclone | 3 of 3 | cyclone6 | 17.5 | cyclone8 | 7.32e-03 [6.4e-03, 8.4e-03] |
| Conveyor | 2 of 2 | conveyor9_3d | 44.5 | conveyor9_3d | 8.27e-03 [7.3e-03, 9.4e-03] |

## 6.6.6 colour code [[19,1,5]], 5 rounds, 28 ions

| family | sizes compiled | fastest | ms / round | lowest LER | LER / round [95%] |
|---|---|---|---|---|---|
| Grid | 6 of 6 | grid7x6 | 61.7 | grid6x5 | 3.86e-04 [3.4e-04, 4.4e-04] |
| WISE | 6 of 6 | wise7x6 | 61.7 | wise6x5 | 3.86e-04 [3.4e-04, 4.4e-04] |
| C2LR | 6 of 6 | c2lr7x7 | 41.6 | c2lr7x6 | 1.30e-03 [1.1e-03, 1.5e-03] |
| Cyclone | 4 of 4 | cyclone15 | 313.9 | cyclone19 | 9.26e-04 [8.1e-04, 1.1e-03] |
| Conveyor | 2 of 2 | conveyor24_12d | 272.5 | conveyor24_12d | 7.24e-04 [6.3e-04, 8.3e-04] |

## 6.6.6 colour code [[37,1,7]], 7 rounds, 55 ions

| family | sizes compiled | fastest | ms / round | lowest LER | LER / round [95%] |
|---|---|---|---|---|---|
| Grid | 7 of 7 | grid9x8 | 91.7 | grid7x7 | 1.20e-04 [1.0e-04, 1.4e-04] |
| WISE | 7 of 7 | wise9x8 | 91.7 | wise7x7 | 1.20e-04 [1.0e-04, 1.4e-04] |
| C2LR | 7 of 7 | c2lr9x9 | 100.9 | c2lr9x8 | 6.41e-04 [5.6e-04, 7.3e-04] |
| Cyclone | 4 of 4 | cyclone29 | 1363.7 | cyclone29 | 1.70e-02 [1.6e-02, 1.8e-02] |
| Conveyor | 4 of 4 | conveyor36_24d | 528.3 | conveyor36_24d | 7.19e-04 [6.3e-04, 8.3e-04] |

## rotated surface code [[9,1,3]], 3 rounds, 17 ions

| family | sizes compiled | fastest | ms / round | lowest LER | LER / round [95%] |
|---|---|---|---|---|---|
| Grid | 4 of 4 | grid6x5 | 19.3 | grid6x5 | 2.17e-04 [1.9e-04, 2.5e-04] |
| WISE | 4 of 4 | wise6x5 | 19.3 | wise6x5 | 2.17e-04 [1.9e-04, 2.5e-04] |
| C2LR | 4 of 4 | c2lr5x4 | 8.5 | c2lr5x4 | 6.25e-04 [5.4e-04, 7.2e-04] |
| Cyclone | 4 of 4 | cyclone10 | 42.0 | cyclone9 | 2.60e-04 [2.3e-04, 3.0e-04] |
| Conveyor | 3 of 3 | conveyor22_11d | 50.1 | conveyor22_11d | 2.82e-04 [2.5e-04, 3.2e-04] |

## rotated surface code [[25,1,5]], 5 rounds, 49 ions

| family | sizes compiled | fastest | ms / round | lowest LER | LER / round [95%] |
|---|---|---|---|---|---|
| Grid | 7 of 7 | grid9x8 | 46.7 | grid8x8 | 2.67e-05 [2.3e-05, 3.1e-05] |
| WISE | 7 of 7 | wise9x8 | 46.7 | wise8x8 | 2.67e-05 [2.3e-05, 3.1e-05] |
| C2LR | 7 of 7 | c2lr7x7 | 35.1 | c2lr7x6 | 1.78e-04 [1.6e-04, 2.0e-04] |
| Cyclone | 4 of 4 | cyclone26 | 292.6 | cyclone26 | 2.29e-04 [2.0e-04, 2.6e-04] |
| Conveyor | 4 of 4 | conveyor32_32d | 105.7 | conveyor32_32d | 5.32e-05 [4.6e-05, 6.1e-05] |

## rotated surface code [[49,1,7]], 7 rounds, 97 ions

| family | sizes compiled | fastest | ms / round | lowest LER | LER / round [95%] |
|---|---|---|---|---|---|
| Grid | 10 of 10 | grid12x11 | 59.5 | grid12x11 | 4.14e-06 [3.2e-06, 5.4e-06] |
| WISE | 10 of 10 | wise12x11 | 59.5 | wise12x11 | 4.14e-06 [3.2e-06, 5.4e-06] |
| C2LR | 10 of 10 | c2lr12x12 | 55.8 | c2lr11x11 | 6.38e-05 [5.6e-05, 7.3e-05] |
| Cyclone | 4 of 4 | cyclone51 | 1093.6 | cyclone51 | 1.26e-02 [1.2e-02, 1.4e-02] |
| Conveyor | 4 of 4 | conveyor64_64d | 246.9 | conveyor64_64d | 3.76e-05 [3.3e-05, 4.3e-05] |

