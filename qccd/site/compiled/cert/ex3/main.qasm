OPENQASM 2.0;
qreg q[4];
creg c[2];
CX q[0],q[1];
CX q[2],q[3];
CX q[0],q[3];
measure q[0] -> c[0];
measure q[3] -> c[1];
