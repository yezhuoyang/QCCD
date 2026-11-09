# Gidney, Shutty and Jones's magic state cultivation circuits

From *Magic state cultivation: growing T states as cheap as CNOT gates*, Craig Gidney, Noah Shutty and Cody Jones,
arXiv:2409.17595 (2024). Their data and code are at zenodo.org/records/13777072: the circuits and statistics under
CC BY 4.0, the code under the Apache License 2.0.

- `inject_cultivate_d1=3.stim`: their `inject[unitary]+cultivate` circuit at d1 = 3 (15 qubits), from `circuits.zip`,
  with the noise channels removed (`stim.Circuit.without_noise`).
- `end2end_d1=3_d2=11_r1=1_r2=1.stim`: their end-to-end construction (injection, cultivation, escape into a grafted
  code of distance 11) with one growing round and one round of the grafted code, made by their generator
  (`code.zip`: `cultiv.make_end2end_cultivation_circuit(dcolor=3, dsurface=11, basis='Y', r_growing=1, r_end=1,
  inject_style='unitary')`), without noise. The same call at `dsurface=15, r_growing=3, r_end=5`, given their uniform
  noise at p = 0.0005, writes their record's circuit character for character.

Their circuits are Clifford twins: the T gate is an S, so stim and R10 can follow them. Their qubits stand on a square
grid and every CX joins grid neighbours, so on our tile lattice, turned by 45 degrees, a point of even x + y is a data
trap and a point of odd x + y a face trap (`qccd.fullstack.scale.cultivation`).
