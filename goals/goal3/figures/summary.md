# Goal 3 — best design per architecture family

Each family's best point by each measure (they can differ). Time per round under the `helios` table; LER per round = 1-(1-p_Z)(1-p_X) under qccd-noise@3, 95% interval.

## bivariate bicycle code [[72,12,6]], 6 rounds, 144 ions

| family | sizes compiled | fastest | ms / round | lowest LER | LER / round [95%] |
|---|---|---|---|---|---|
| C2LR | 12 of 12 | c2lr15x14 | 417.0 | c2lr15x14 | 1.44e-03 [1.3e-03, 1.6e-03] |
| WISE | 12 of 12 | wise_h3_8x7 | 807.3 | wise_h3_8x7 | 8.37e-03 [7.7e-03, 9.1e-03] |
| Cyclone | 6 of 6 | cyclone16_n10 | 165.2 | cyclone72_n2 | 8.43e-04 [7.3e-04, 9.7e-04] |

## 6.6.6 colour code [[7,1,3]], 3 rounds, 10 ions

| family | sizes compiled | fastest | ms / round | lowest LER | LER / round [95%] |
|---|---|---|---|---|---|
| C2LR | 3 of 3 | c2lr4x3 | 63.1 | c2lr4x4 | 8.88e-03 [7.9e-03, 1.0e-02] |
| WISE | 7 of 7 | wise_h5_2x2 | 79.8 | wise_h7_2x2 | 8.81e-03 [7.8e-03, 9.9e-03] |
| Cyclone | 4 of 4 | cyclone4_n3 | 5.4 | cyclone6_n3 | 8.13e-03 [7.2e-03, 9.2e-03] |

## 6.6.6 colour code [[19,1,5]], 5 rounds, 28 ions

| family | sizes compiled | fastest | ms / round | lowest LER | LER / round [95%] |
|---|---|---|---|---|---|
| C2LR | 6 of 6 | c2lr7x7 | 73.0 | c2lr7x7 | 4.80e-04 [4.2e-04, 5.5e-04] |
| WISE | 10 of 10 | wise_h3_4x4 | 127.7 | wise_h7_4x3 | 5.46e-04 [4.8e-04, 6.2e-04] |
| Cyclone | 6 of 6 | cyclone2_n15 | 30.5 | cyclone4_n8 | 4.76e-04 [4.2e-04, 5.4e-04] |

## 6.6.6 colour code [[37,1,7]], 7 rounds, 55 ions

| family | sizes compiled | fastest | ms / round | lowest LER | LER / round [95%] |
|---|---|---|---|---|---|
| C2LR | 7 of 7 | c2lr9x9 | 165.1 | c2lr9x8 | 2.44e-04 [2.1e-04, 2.8e-04] |
| WISE | 12 of 12 | wise_h3_7x7 | 206.5 | wise_h3_5x5 | 2.76e-04 [2.4e-04, 3.2e-04] |
| Cyclone | 6 of 6 | cyclone6_n10 | 55.0 | cyclone6_n10 | 2.23e-04 [1.9e-04, 2.6e-04] |

## hypergraph product code (Hamming [7,4,3]) [[58,16,3]], 3 rounds, 100 ions

| family | sizes compiled | fastest | ms / round | lowest LER | LER / round [95%] |
|---|---|---|---|---|---|
| C2LR | 10 of 10 | c2lr13x12 | 250.4 | c2lr11x10 | 4.23e-02 [4.0e-02, 4.5e-02] |
| WISE | 12 of 12 | wise_h3_7x6 | 375.3 | wise_h3_7x6 | 5.41e-02 [5.2e-02, 5.7e-02] |
| Cyclone | 6 of 6 | cyclone12_n9 | 151.4 | cyclone12_n9 | 3.44e-02 [3.2e-02, 3.7e-02] |

## rotated surface code [[9,1,3]], 3 rounds, 17 ions

| family | sizes compiled | fastest | ms / round | lowest LER | LER / round [95%] |
|---|---|---|---|---|---|
| C2LR | 4 of 4 | c2lr5x4 | 18.2 | c2lr4x4 | 2.83e-04 [2.5e-04, 3.3e-04] |
| WISE | 8 of 8 | wise_h3_3x3 | 35.6 | wise_h3_4x4 | 2.76e-04 [2.4e-04, 3.2e-04] |
| Cyclone | 5 of 5 | cyclone2_n9 | 9.2 | cyclone4_n5 | 2.83e-04 [2.5e-04, 3.3e-04] |

## rotated surface code [[25,1,5]], 5 rounds, 49 ions

| family | sizes compiled | fastest | ms / round | lowest LER | LER / round [95%] |
|---|---|---|---|---|---|
| C2LR | 7 of 7 | c2lr8x8 | 53.7 | c2lr9x9 | 7.27e-05 [6.3e-05, 8.4e-05] |
| WISE | 12 of 12 | wise_h3_6x6 | 71.9 | wise_h3_6x6 | 7.22e-05 [6.3e-05, 8.3e-05] |
| Cyclone | 6 of 6 | cyclone4_n13 | 31.3 | cyclone16_n4 | 9.53e-05 [8.3e-05, 1.1e-04] |

## rotated surface code [[49,1,7]], 7 rounds, 97 ions

| family | sizes compiled | fastest | ms / round | lowest LER | LER / round [95%] |
|---|---|---|---|---|---|
| C2LR | 10 of 10 | c2lr12x12 | 78.5 | c2lr9x9 | 2.67e-05 [2.3e-05, 3.1e-05] |
| WISE | 12 of 12 | wise_h3_10x9 | 140.2 | wise_h3_10x9 | 3.68e-05 [3.2e-05, 4.2e-05] |
| Cyclone | 6 of 6 | cyclone10_n10 | 73.2 | cyclone18_n6 | 5.34e-05 [4.7e-05, 6.1e-05] |

