title Bell pair by transversal CNOT
# |+0> -> CNOT -> |00>+|11>: the two Z readouts always agree
prep q0 X
prep q1 Z
cx q0 q1
se q0 1
se q1 1
read q0 Z -> a
read q1 Z -> b
