"""QCCDCert: the verified compiler at the core of this project's compilation.

QCCDCert ("QCCDCert: Formally Verified Compilation for QCCD Co-Design", Ye, Palsberg, Sun
and Häffner) is a QCCD compiler written and proved in Lean 4.  Its inputs are a *hardware
program* (sites with capacities, paths with the sites they pass, transport channels and
waveforms, calibrations that bind one exact native operation to a site and an optical
channel), a *rule program* (named assertions over one event frame) and an OpenQASM 2
source.  One theorem, `verified_compilation`, covers every device and every rule program:

    compile(H, R, P, κ) ⇓ (K, T)  ⟹  Meaning ∧ Legality ∧ Time

This package is the boundary between that compiler and the rest of the repository:

* `toolchain`  finds the QCCDCert build (``QCCD_CERT``), runs its tools, fingerprints it;
* `syntax`     prints hardware programs, rule programs and target code in the paper's syntax
               (displays: the compiler reads and writes JSON);
* `frames`     the event frames F = (H, pos⁻, pos⁺, u, t, δ) of a compiled execution and the
               counts a rule observes on each (recomputed for display; the compiler decides
               the rules in Lean);
* `compile`    one compilation: elaborate the rules, compile, time, schedule, order chains;
* `examples`   the three worked examples of the paper's tutorial, with the paper's inputs;
* `machine`    a studio machine as a QCCDCert hardware program (the translation is printed);
* `tsir`       QCCDCert target code as a TSIR programme, so the one design-page template
               (`qccd.viz.render.render_html`) can play it.

The compiler of this site is QCCDCert *extended*, never a second compiler beside it
(`extensions` lists what is added): the machine translation and the studio's rules as
QCCDCert rules (closed loops as mandatory broadcast channels), this project's router as a
proposal to QCCDCert's checked routing stage (`planner`) -- all inside the theorem -- and
cooling after it, outside the theorem, which the studio's replay checks.
"""

from .toolchain import Toolchain, ToolchainMissing, find  # noqa: F401
