"""A stim circuit written as text and parsed once.

`stim.Circuit.append` costs about 0.2 ms a call on a growing circuit, so building an
extracted program one operation at a time spent most of the extraction's time in appends
(0.3 s of 0.37 s for a three-round surface code).  Writing lines and handing them to
`stim.Circuit` once is two orders of magnitude cheaper and gives the same circuit.

`StimText.append(name, targets, arg)` has the signature the pulse emitters in
`qccd.qec.native` call, so they write into either a real `stim.Circuit` (the tests that
compare them with the bridge) or this.
"""

from __future__ import annotations

from typing import Iterable, Mapping, Sequence

__all__ = ["StimText", "declare"]


class StimText:
    def __init__(self) -> None:
        self.lines: list[str] = []

    def append(self, name: str, targets: Iterable, arg=None) -> None:
        t = " ".join(str(int(x)) for x in targets)
        if arg is None or (isinstance(arg, (list, tuple)) and not arg):
            self.lines.append(f"{name} {t}")
        elif isinstance(arg, (list, tuple)):
            self.lines.append(f"{name}({', '.join(repr(float(a)) for a in arg)}) {t}")
        else:
            self.lines.append(f"{name}({float(arg)!r}) {t}")

    def line(self, text: str) -> None:
        self.lines.append(text)

    def circuit(self):
        import stim

        return stim.Circuit("\n".join(self.lines))


def declare(c: StimText, detectors: Sequence[Sequence[int]],
            observables: Sequence[Sequence[int]], meas_of_bit: Mapping[int, int],
            n_meas: int) -> None:
    """DETECTOR / OBSERVABLE_INCLUDE over classical bits, as records of the measurements
    that wrote them.  A bit no measurement wrote is a `KeyError` naming it."""

    def rec(bits: Sequence[int]) -> str:
        missing = [b for b in bits if b not in meas_of_bit]
        if missing:
            raise KeyError(f"classical bit(s) {missing} are never measured")
        return " ".join(f"rec[{meas_of_bit[b] - n_meas}]" for b in bits)

    for det in detectors:
        c.line(f"DETECTOR {rec(det)}")
    for k, obs in enumerate(observables):
        c.line(f"OBSERVABLE_INCLUDE({k}) {rec(obs)}")
