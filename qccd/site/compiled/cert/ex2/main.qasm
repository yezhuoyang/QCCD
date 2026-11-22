OPENQASM 2.0;
qreg q[3];
creg c[1];
CX q[0],q[2];
CX q[1],q[2];
measure q[2] -> c[0];
if(c==1) reset q[2];
