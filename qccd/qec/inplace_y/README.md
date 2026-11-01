# Gidney's Y-basis memory circuits

`y_memory_d{3,5,7,9,11}.stim` are Craig Gidney's circuits from *Inplace Access to the Surface
Code Y Basis*, Quantum 8, 1310 (2024), arXiv:2302.07395. They come from the paper's data,
zenodo.org/records/7487893, `circuits.zip`, licensed CC BY 4.0. The source file of each is named
in its first lines. The only change: the noise channels are removed (`stim.Circuit.without_noise`).

Each circuit is a Y-basis memory experiment on a distance-d rotated surface patch:
1. The patch is initialised in the Y basis in place: a patch with twisted boundaries, then one
   transition round into the standard patch.
2. Standard rounds follow.
3. The patch is measured in the Y basis in place: the reverse of step 1.

Each file has (d - 1) / 2 padding rounds. Its graphlike circuit distance is d, and with fewer
than (d - 3) / 2 padding rounds it is not.

`qccd.qec.experiments.y_memory` reads them as our experiments. Transposed, Gidney's patch is the
standard patch of `qccd.gadget.surface` (Z̄ along a row, X̄ down a column). Every two-qubit gate
is a CZ between a data qubit and a measure qubit beside it, so the tiled compiler plays them as
they are.
