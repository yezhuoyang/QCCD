# Corrections this machine keeps in software.  A logical Pauli is a line in the classical
# memory, not an operation on ions: the archive holds it and flips every later outcome it
# anticommutes with.  Both readouts must come out -1.
title Logical Paulis kept in the classical memory
prep q0 Z
x    q0
se   q0 1
read q0 Z -> m
decode q0
prep q1 X
z    q1
read q1 X -> n
decode q1
