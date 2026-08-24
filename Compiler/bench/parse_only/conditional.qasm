OPENQASM 2.0;
include "qelib1.inc";
qreg q[3];
creg c[3];
h q[0];
measure q[0] -> c[0];
if (c==1) x q[1];
if (c==3) cx q[1],q[2];
measure q -> c;
