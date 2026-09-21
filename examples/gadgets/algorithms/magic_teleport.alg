title Teleporting a magic state
# the factory distills a T state, the pharmacy encodes it into block t, and the
# surgery Bell pair carries it to block b: any input state arrives, up to the frame
inject t
prep a X
prep b X
zz a b -> m1
xx t a -> m2
read t Z -> m3
read a Z -> m4
decode              # every outcome so far becomes a result
se b 3
decode b
