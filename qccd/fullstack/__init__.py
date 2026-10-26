"""The full fault-tolerant stack on QCCD: FormalRV's layers above, concrete ions below.

    QParam (.prog) -> QParamPPM (.qppm) -> QParamQEC (.qec) -> QParamSys (.sysprog) -> QCCD hardware

FormalRV (a separate project, pinned and read-only from here) owns everything down to the
`.sysprog`, whose qubits are virtual and whose `route` is mechanism-free.  This package adds the
last arrow -- devirtualize the qubits and the system calls on a finite QCCD machine -- and keeps
every hardware instruction traceable to the line it came from at every layer above.

Modules:
    formalrv   the pinned FormalRV toolchain: run its stages and its VM
    sysprog    read a `.sysprog` listing and expand it, every event carrying its source line,
               loop trip and call frame (checked against FormalRV's VM counts)

Plan and decisions: docs/FULLSTACK_PLAN.md.
"""
