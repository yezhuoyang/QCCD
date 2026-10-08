"""Reproducing published QCCD results: their schedules, their rules, their clocks.

A published compiler states its machine model in prose and enforces it in code, and the two
are not always the same.  This package re-states each paper's model as data -- a device, a
rule profile and a duration law -- and checks the schedules the paper's own artifact emits
against it, so that "we reproduced their number" means: their schedule, replayed event by
event, is legal under the rules they say they assume, and its makespan is the number they
print.  The same checker then judges our schedules under the same rules, which is the only
way a "faster" claim means anything.

`timed`     the timed-schedule format and the checker (continuous time, per-resource)
`models`    duration and fidelity laws, one per paper family
`devices`   their machines, as port graphs and as architecture documents
`importers` their artifacts' outputs -> `TimedSchedule`

The lockstep verifier in `qccd.verify` is untouched: it encodes OUR control model (one
waveform per cycle, gates and transport never mixed in a cycle), under which most of these
schedules are illegal by construction.  That is a finding to report, not a reason to bend
either checker.
"""
