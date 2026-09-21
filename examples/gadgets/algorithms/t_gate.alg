# A logical T by gate teleportation -- the reason classically controlled hardware exists.
#
# A distilled magic state is measured against the data block by Z-bar Z-bar surgery and
# then read out.  The outcomes decide the correction: the X readout asks for a Z-bar, which
# is a Pauli and can be kept in the archive as a frame, but the surgery outcome asks for an
# S-bar, which is NOT a Pauli -- no frame can hold it, so the S-bar gadget itself must run,
# and only when the bit says so.  That is a dynamic circuit: the hardware does something
# different depending on a measurement taken while it runs.
#
# Every one of the four outcome branches is signed off separately, and each must implement
# the same channel from the magic block's state to q0.
title A logical T by teleportation, with the S correction only when needed
prep   q0 X
inject t
zz     q0 t -> m
read   t X   -> k
decode             # both corrections read an outcome: decode them first
if m:  s q0        # the Clifford correction: the S-bar gadget runs only when m = 1
if k:  z q0        # the Pauli correction: a line in the archive, no ions touched
decode q0          # the S-bar gadget's own rounds
