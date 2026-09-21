title GHZ state on three blocks
# |+00> -> CNOT q0->q1, CNOT q0->q2: all three Z readouts agree
prep q0 X
prep q1 Z
prep q2 Z
cx q0 q1
cx q0 q2
se q0 1
decode q0 q1 q2
read q0 Z -> a
read q1 Z -> b
read q2 Z -> c
decode
