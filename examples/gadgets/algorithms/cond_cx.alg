# Classically controlled hardware, in its plainest form: "these ions do this in that gadget
# only if the condition is triggered".
#
# A logical block is prepared in |+-bar> and read out in Z: one honestly random bit.  The
# transversal-CNOT workshop is then told whether to fire.  The ions travel to the workshop
# either way and the place reserves the same slot either way -- the worst case, so the
# schedule stays static -- and only the pulses depend on the bit.
#
# What the two branches promise is different, and both are checked: with no CNOT, q0 is
# still |+-bar> so u = 0 and v is random; with the CNOT, the blocks are in a Bell state, so
# u and v are each random but u + v = 0.  A sign-off that ignored the guard could not get
# both right.
title A transversal CNOT that fires only when the bit says so
prep q0 X
prep q1 Z
prep a  X
read a  Z    -> m
decode a           # the guard below reads m: it must be decoded first
if m: cx q0 q1
read q0 X    -> u
read q1 X    -> v
decode
