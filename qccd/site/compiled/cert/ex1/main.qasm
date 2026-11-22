OPENQASM 2.0;
qreg q[2];
creg c[1];
CX q[0],q[1];
measure q[1] -> c[0];
