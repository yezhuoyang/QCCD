title Teleportation by lattice surgery
# t in |+>; a Bell pair (a, b) by Z̄Z̄ surgery; X̄X̄ surgery on (t, a) and two Z
# readouts complete a Bell measurement: b holds the state of t, up to the recorded frame
prep t X
prep a X
prep b X
zz a b -> m1
xx t a -> m2
read t Z -> m3
read a Z -> m4
se b 1
