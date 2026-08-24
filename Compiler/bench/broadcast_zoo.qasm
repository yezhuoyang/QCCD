OPENQASM 2.0;
include "qelib1.inc";
// several registers: bit numbering follows declaration order
qreg a[3];
qreg b[3];
qreg solo[1];
creg ca[3];
creg cb[3];
h a;                  // whole-register broadcast
cx a,b;               // pairwise, index by index
cx solo[0],a;         // one fixed bit against a register
rz(pi/2) b;
barrier a,b;          // ONE node over six qubits
reset a;
ccx a[0],b[1],solo[0];
measure a -> ca;
measure b[2] -> cb[0];
