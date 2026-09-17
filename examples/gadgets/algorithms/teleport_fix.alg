# Teleportation that hands back a known state, not a state plus a note.
#
# `teleport.alg` moves |+-bar> from t to b and the sign-off reports what b holds up to a
# recorded outcome: "1 -> X-bar[b] + m2".  That note has to be carried by everything
# downstream.  Here the machine applies the correction instead: the archive holds a Z-bar
# on b exactly when the X-bar X-bar surgery said so, and the final readout of b is then
# deterministic in every branch -- r = 0, whatever the Bell measurement happened to give.
#
# That is what classically controlled execution buys: the state is where it should be, and
# no bookkeeping travels with it.
title Teleportation whose correction is applied, not just recorded
prep t X
prep a X
prep b X
zz   a b  -> m1
xx   t a  -> m2
read t Z  -> m3
read a Z  -> m4
if m2: z b
read b X  -> r
