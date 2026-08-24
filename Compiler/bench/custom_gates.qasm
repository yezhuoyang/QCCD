OPENQASM 2.0;
include "qelib1.inc";
gate mycx a,b { cx a,b; }
gate rot(theta) a { rz(theta) a; ry(theta/2) a; }
gate bell a,b { h a; cx a,b; }
qreg q[4];
creg c[4];
bell q[0],q[1];
mycx q[1],q[2];
rot(pi/3) q[3];
rot(-pi/6) q[0];
bell q[2],q[3];
measure q -> c;
