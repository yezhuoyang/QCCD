# Two logical S gates make a Z, and Z on |+> is |->: reading X must give -1 every time.
# Each S is the S-bar gadget: a |Y-bar> block is prepared at the pharmacy (an injection),
# measured against the data block by Z-bar Z-bar lattice surgery, and read out in X; the
# correction is Z-bar when the two outcomes disagree, and it is kept in the archive as a
# Pauli frame -- no ion is ever touched by it.
title Two logical S gates make a Z
prep q0 X
s    q0
se   q0 1
s    q0
decode q0      # both S gadgets' rounds, their |Y> blocks' included
read q0 X -> m
decode q0
