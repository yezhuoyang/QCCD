"""Logic verification: what a gadget's ions compute, checked exactly.

The hardware verifier proves a leaf's program is something a QCCD machine can run.  This
package proves the program computes what the gadget promises.  Four pieces, all pure
Python (the site builds with the standard library alone):

    circuit     the quantum circuit a TSIR program executes, read off its gate, measure
                and reset instructions in order (transport and cooling are not gates)
    tableau     a stabilizer tableau whose signs are symbolic in the measurement record,
                so one run covers every outcome; stabilizer flows are read off a Choi state
    faults      Pauli-frame propagation of every single circuit fault at once, and the
                search for the fewest faults that flip a logical observable unseen
    statevec    a sparse state vector for the few non-Clifford circuits (the T factory)

and two that use them:

    spec        a gadget op's specification as stabilizer flows, and the check that the
                flows hold and pin the channel down completely
    experiment  the reference experiment that measures a gadget's fault distance: ideal
                encoders in front, the gadget with every fault location live, ideal
                decoders behind
"""
