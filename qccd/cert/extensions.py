"""What this project adds on top of QCCDCert's target code, in one place.

The compiler of this site is QCCDCert plus extensions.  An extension reads QCCDCert's input or
output and never replaces one of its passes; each is named wherever its numbers appear, and the
page that shows them says which numbers Theorem `verified_compilation` covers.

Inside the theorem (they change what QCCDCert is given, and QCCDCert checks the result):

* the machine translation (`qccd.cert.machine`): a studio machine as a hardware program, with
  the studio's frame-level rules written as QCCDCert rules (`qccd.cert.driver`);
* the router (`qccd.cert.planner`): a proposal for QCCDCert's checked routing stage, used when
  QCCDCert's own transport search finds no route.

Outside the theorem (they change QCCDCert's output, and the studio's replay checks the result):

* `heat`: a state-preparation cool before the programme, then cooling inserted before any
  two-qubit gate whose ions would be over the motional budget (studio rules R7, R7c).  Heat
  is not in QCCDCert's model; the cooled programme is what the studio plays and judges.
"""

from __future__ import annotations


def heat(prog_json: dict, arch, model):
    """QCCDCert's target code (as TSIR) -> the same code with this site's cooling: one broadcast
    state-preparation cool after `init`, then `qccd.compile.insert_cooling`.  Returns
    (cooled TSIR, the cooling pass's result)."""
    from ..compile import insert_cooling
    from ..ir.tsir import TSIR
    doc = dict(prog_json)
    ins = list(doc["instructions"])
    if not any(i.get("type") == "cool" for i in ins):
        nxt = max(i["id"] for i in ins) + 1
        ins.insert(1, {"type": "cool", "id": nxt, "broadcast": True,
                       "meta": {"kind": "state_prep", "extension": "heat"}})
    doc["instructions"] = ins
    result = insert_cooling(TSIR.from_json(doc), arch, model)
    return result.program, result
