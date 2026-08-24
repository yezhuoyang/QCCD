OPENQASM 2.0;
include "qelib1.inc";
// Four qubits, seven statements -- small enough to read every instruction the compiler
// emits for it, and still one of each thing it has to handle:
//   h        a Clifford rotation      -> VZ + R
//   cx       the entangler            -> R, MS, R, R  (the proved decomposition)
//   t        a non-Clifford phase     -> a bare frame update, no laser
//   rz       an arbitrary angle       -> the same, with a parameter
//   measure  readout
qreg q[4];
creg c[2];
h q[0];
cx q[0],q[1];
t q[2];
cx q[1],q[2];
rz(0.7854) q[3];
cx q[2],q[3];
measure q[3] -> c[0];
