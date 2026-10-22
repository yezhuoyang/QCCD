# Shor's algorithm, fault-tolerant: the order of 7 modulo 15.
#
# Order finding for a = 7, N = 15 by iterative phase estimation (Kitaev): one control block
# per bit of the phase, each prepared, used, corrected and read before the next -- the form
# Monz et al. ran on trapped ions (Science 351:1068, 2016).  U multiplies the work register
# by 7 modulo 15; its eigenphases are s/4, so three bits m3 m2 m1 of the phase 0.m3m2m1 hold
# s exactly: m1 = 0 always, and m3 m2 is s, uniformly 0, 1, 2 or 3.  An odd s gives the
# order r = 4, and gcd(7^2 - 1, 15) = 3, gcd(7^2 + 1, 15) = 5.
#
# Every logical qubit is a d = 3 surface-code block; every gate is a verified place or one
# of the standard gates (include "standard"), proved to be its contract in every branch.  The
# controlled multiplications are compiled for the values the register can hold when they
# act (1 before iteration 2; 1 or 4 before iteration 3), as every experimental Shor has
# been; the two controlled swaps of iteration 3 are two Toffolis, fourteen T gates from the
# factories.  The inverse quantum Fourier transform is semiclassical: each control is
# rotated back by the phase the earlier bits fix (S̄† when m1 or m2 says so, T̄† when m1
# does) before it is read in X.

title Shor's algorithm: the order of 7 modulo 15
include "standard"
floorplan "shor"

# the work register holds 1 = |0001⟩, b0 its least significant bit
prep b0 Z
x    b0
prep b1 Z
prep b2 Z
prep b3 Z

# iteration 1: U^4 = 1 (7^4 = 2401 = 1 mod 15), so the control picks up no phase
prep c1 X
read c1 X -> m1
decode c1

# iteration 2: U^2 multiplies by 4 (7^2 = 49 = 4 mod 15); on the register's 1 that is 1 -> 4
prep c2 X
cx   c2 b0
cx   c2 b2
if m1: sdg c2
read c2 X -> m2
decode c2

# iteration 3: U multiplies by 7, on 1 and 4 (where iteration 2 left the register):
# 1 -> 7, 4 -> 13.  Rotate the four bits right by one -- on these two values two controlled
# swaps do it -- then flip all four.
prep c3 X
cswap c3 b0 b3
cswap c3 b1 b2
cx   c3 b0
cx   c3 b1
cx   c3 b2
cx   c3 b3
if m2: sdg c3
if m1: tdg c3
read c3 X -> m3
decode
