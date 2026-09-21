title Bell pair by transversal CNOT
# |+0> -> CNOT -> |00>+|11>: the two Z readouts always agree
prep q0 X
prep q1 Z
cx q0 q1
se q0 1
se q1 1
decode q0 q1      # the rounds so far: each place sends its syndrome down its wire
read q0 Z -> a
read q1 Z -> b
decode            # and the readouts' own: now a and b are results
