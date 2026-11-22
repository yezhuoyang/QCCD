"""QCCDCert's programs in the paper's syntax.

The compiler reads and writes JSON; the paper prints the same values in a compact notation:

    hardware Primer; site S[1]; site J[1]; site G[2];
    path p0:S->J->G channel 0 waveform 0 duration 2;
    calibration c4:MS(pi/2) @G channel 7 duration 4;
    assert cap:  forall s:Sites. Ntr(s) <= cap(s);
    transport q0:S->G[p0];
    pulse MS(pi/2) q0,q1 @G via c4;

These printers are displays (the paper's appendix says so too: "the listings are displays of
the compiler's values ... not input files").  Every value they print is the compiler's own;
the printers only choose the notation.  `rule_text` prints a rule program from its formula,
so a page never carries a hand-typed copy of a rule beside the rule the compiler read.
"""

from __future__ import annotations

# ---------------------------------------------------------------- exact angle expressions
_OPS = {"div": "/", "mul": "*", "add": "+", "sub": "-"}


def expr(e, top: bool = True) -> str:
    """An exact angle with the fewest parentheses (the compiler's own text prints all)."""
    if e == "pi":
        return "pi"
    if isinstance(e, (int, float)):
        return str(e)
    kind, body = next(iter(e.items()))
    if kind == "rational":
        n, d = body["numerator"], body["denominator"]
        return str(n) if d == 1 else f"{n}/{d}"
    if kind == "neg":
        return "-" + expr(body["argument"], False)
    if kind in _OPS:
        text = expr(body["left"], False) + _OPS[kind] + expr(body["right"], False)
        return text if top or kind in ("div", "mul") else f"({text})"
    if kind in ("real", "float"):
        return str(body)
    return f"{kind}({', '.join(expr(v, True) for v in (body.values() if isinstance(body, dict) else [body]))})"


def waveform(w: dict) -> str:
    kind, body = next(iter(w.items()))
    if kind == "r":
        return f"R({expr(body['theta'])},{expr(body['phi'])})"
    return f"MS({expr(body['theta'])})"


def pulse(p: dict) -> tuple[str, list[int]]:
    """(name with angles, ions) of one located pulse."""
    kind, body = next(iter(p.items()))
    if kind == "r":
        return f"R({expr(body['theta'])},{expr(body['phi'])})", [body["ion"]]
    return f"MS({expr(body['theta'])})", [body["control"], body["target"]]


# ---------------------------------------------------------------------- rule programs
_DOMAIN = {"sites": "Sites", "paths": "Paths", "channels": "Channels"}
_ATOM = {"start": "t", "duration": "delta", "finish": "end", "moveCount": "n_move",
         "pulseCount": "n_optical", "rCount": "n_R", "msCount": "n_MS",
         "readoutCount": "n_readout", "pumpCount": "n_pump", "latchCount": "n_latch"}
_OBS = {"capacity": "cap", "pulsesAt": "P", "pathUse": "M", "channelUse": "M",
        "channelWaves": "W"}
_PHASE = {"before": "N-", "after": "N+", "during": "Ntr"}


class Names:
    """Index -> name for the references a formula may hold."""

    def __init__(self, hardware: dict | None = None):
        base = (hardware or {}).get("device", {}).get("base", {})
        geo = base.get("geometry", {})
        self.sites = [s["name"] for s in geo.get("sites", [])]

    def site(self, i: int) -> str:
        return self.sites[i] if i < len(self.sites) else str(i)


def _atom_name(a: dict) -> str:
    if "base" in a:
        inner = a["base"]["expression"] if isinstance(a["base"], dict) and "expression" in a["base"] else a["base"]
        if isinstance(inner, str):
            return _ATOM.get(inner, inner)
        kind, body = next(iter(inner.items()))
        if kind == "const":
            return str(body)
        return f"{kind}({body})"
    if isinstance(a, dict) and len(a) == 1:
        k = next(iter(a))
        return _ATOM.get(k, k)
    return str(a)


def term(t: dict, names: Names) -> str:
    kind, body = next(iter(t.items()))
    if kind == "constant":
        return str(body["value"])
    if kind == "atom":
        e = body["expression"]
        return _atom_name(e) if isinstance(e, dict) else _ATOM.get(e, str(e))
    if kind == "observe":
        obs, ref = body["observation"], body["reference"]
        r = ref["var"]["name"] if "var" in ref else None
        if isinstance(obs, dict):  # occupancy with a phase
            fn = _PHASE[obs["occupancy"]["phase"]]
            r = r if r is not None else names.site(ref["id"]["value"])
        else:
            fn = _OBS[obs]
            if r is None:
                v = ref["id"]["value"]
                r = names.site(v) if obs in ("capacity", "pulsesAt") else str(v)
        return f"{fn}({r})"
    if kind in ("add", "sub"):
        return f"({term(body['lhs'], names)} {'+' if kind == 'add' else '-'} {term(body['rhs'], names)})"
    raise ValueError(f"unknown term {kind}")


def formula(f, names: Names, top: bool = True) -> str:
    if isinstance(f, str):
        return {"truth": "true", "falsity": "false"}[f]
    kind, body = next(iter(f.items()))
    if kind in ("truth", "falsity"):
        return {"truth": "true", "falsity": "false"}[kind]
    if kind == "le":
        lhs, rhs = body["lhs"], body["rhs"]
        if "constant" in lhs and "constant" not in rhs:  # k <= x  is printed  x >= k  (1 <= x: x > 0)
            k = lhs["constant"]["value"]
            return f"{term(rhs, names)} > {k - 1}" if k == 1 else f"{term(rhs, names)} >= {k}"
        return f"{term(lhs, names)} <= {term(rhs, names)}"
    if kind == "eq":
        return f"{term(body['lhs'], names)} = {term(body['rhs'], names)}"
    if kind in ("and", "or", "implies"):
        op = {"and": "and", "or": "or", "implies": "=>"}[kind]
        text = f"{formula(body['lhs'], names, False)} {op} {formula(body['rhs'], names, False)}"
        return text if top else f"({text})"
    if kind == "not":
        return f"not {formula(body['body'], names, False)}"
    if kind in ("all", "any"):
        q = "forall" if kind == "all" else "exists"
        text = f"{q} {body['name']}:{_DOMAIN[body['domain']]}. {formula(body['body'], names, True)}"
        return text if top else f"({text})"
    raise ValueError(f"unknown formula {kind}")


def rule_text(rule: dict, names: Names | None = None) -> str:
    """`assert name: formula;` -- one rule of a rule program, as the paper prints it."""
    return f"assert {rule['name']}: {formula(rule['formula'], names or Names())};"


def _lowered_nat(e, names: Names) -> str:
    kind, b = next(iter(e.items())) if isinstance(e, dict) else (e, None)
    if kind == "base":
        x = b["expression"]
        if isinstance(x, str):
            return _ATOM.get(x, x)
        k, v = next(iter(x.items()))
        if k == "const":
            return str(v["value"])
        if k == "occupancy":
            return f"{_PHASE[v['phase']]}({names.site(v['site'])})"
        if k == "pulsesAt":
            return f"P({names.site(v['site'])})"
        if k in ("add", "sub"):
            return f"({_lowered_nat({'base': {'expression': v['lhs']}}, names)} {'+' if k == 'add' else '-'} " \
                   f"{_lowered_nat({'base': {'expression': v['rhs']}}, names)})"
        return f"{k}({v})"
    if kind in ("pathUse", "channelUse", "channelWaves"):
        arg = next(iter(b.values())) if isinstance(b, dict) else b
        return f"{_OBS[kind]}({arg})"
    if kind in ("readoutCount", "pumpCount", "latchCount"):
        return _ATOM[kind]
    if kind in ("add", "sub"):
        return f"({_lowered_nat(b['lhs'], names)} {'+' if kind == 'add' else '-'} {_lowered_nat(b['rhs'], names)})"
    return str(e)


def lowered(f, names: Names, top: bool = True) -> str:
    """An elaborated rule as the compiler stores it in the device file: no quantifier, every
    site, path and channel named, capacities replaced by their declared numbers."""
    if isinstance(f, str):
        return {"truth": "true", "falsity": "false"}.get(f, f)
    kind, b = next(iter(f.items()))
    if kind in ("le", "eq"):
        return f"{_lowered_nat(b['lhs'], names)} {'<=' if kind == 'le' else '='} {_lowered_nat(b['rhs'], names)}"
    if kind in ("and", "or"):
        # print a right-nested chain of one connective flat, as one reads it
        text = f"{lowered(b['lhs'], names, False)} {kind} " + (
            lowered(b['rhs'], names, True) if next(iter(b['rhs'])) == kind else lowered(b['rhs'], names, False))
        return text if top else f"({text})"
    if kind == "implies":
        text = f"{lowered(b['premise'], names, False)} => {lowered(b['conclusion'], names, False)}"
        return text if top else f"({text})"
    if kind == "not":
        return f"not {lowered(b.get('body', b), names, False)}"
    return str(f)


def rules_program(rules: list[dict], hardware: dict | None = None) -> list[str]:
    names = Names(hardware)
    return [rule_text(r, names) for r in rules]


# ------------------------------------------------------------------ hardware programs
class Printer:
    """Prints one device's hardware program and every stage of a compilation for it."""

    def __init__(self, hardware: dict):
        dev = hardware["device"]
        base = dev["base"]
        self.hardware = hardware
        self.device = dev
        self.base = base
        self.sites = [s["name"] for s in base["geometry"]["sites"]]
        self.paths = base["geometry"]["paths"]
        self.path_duration = base["pathDurations"]
        self.calibrations = base["calibrations"]
        self.profiles = base.get("broadcasts", [])
        self.motions = (hardware.get("wiring") or {}).get("motions")

    def route(self, i: int) -> str:
        p = self.paths[i]
        return "->".join(self.sites[s] for s in [p["src"], *p["via"], p["dst"]])

    def hardware_program(self, compact: bool = True) -> list[str]:
        """The device as the paper prints it.  `compact` prints the declarations of the first
        gate site and one line naming the other sites that repeat them, as the paper does."""
        d, base = self.device, self.base
        g = base["geometry"]
        out = [f"hardware {base['name']}; " + " ".join(f"site {s['name']}[{s['capacity']}];" for s in g["sites"])]
        for i, p in enumerate(self.paths):
            motion = f"   // motion {' '.join(self.motions[i])}" if self.motions else ""
            out.append(f"path p{i}:{self.route(i)} channel {p['channel']} waveform {p['waveform']} "
                       f"duration {self.path_duration[i]};{motion}")
        for share in g.get("broadcasts", []):
            out.append(f"share channel {share['channel']} {'mandatory' if share['mandatory'] else 'optional'};")
        gate_sites = sorted({c["site"] for c in self.calibrations} | {r["site"] for r in d.get("readouts", [])})
        shown = set(gate_sites[:1]) if compact and gate_sites else set(gate_sites)
        out.append("optical channel " + ", ".join(map(str, base["opticalChannels"])) + ";")
        for i, c in enumerate(self.calibrations):
            if c["site"] in shown:
                out.append(f"calibration c{i}:{waveform(c['waveform'])} @{self.sites[c['site']]} "
                           f"channel {c['channel']} duration {c['duration']};")
        for i, b in enumerate(self.profiles):
            if set(b["sites"]) & shown:
                out.append(f"broadcast b{i}:R({expr(b['theta'])},{expr(b['phi'])}) sites "
                           f"{','.join(self.sites[s] for s in b['sites'])} channel {b['channel']} "
                           f"duration {b['duration']} limit {b['maxPerSite']};")
        for name, key in (("acquire a", "readouts"), ("pump p", "pumps")):
            for i, c in enumerate(d.get(key, [])):
                if c["site"] in shown:
                    out.append(f"{name}{i} @{self.sites[c['site']]} channel {c['channel']} duration {c['duration']};")
        rest = [s for s in gate_sites if s not in shown]
        if rest:
            out.append(f"// the same declarations at {', '.join(self.sites[s] for s in rest)}, "
                       f"on their own optical channels")
        if d.get("controller"):
            out.append(f"controller duration {d['controller']['duration']};")
        return out

    # ------------------------------------------------------------------ instructions
    @staticmethod
    def guard(tok: dict) -> str:
        return "when saved " if tok.get("guarded") else ""

    def duration(self, tok: dict) -> int | None:
        kind, body = next(iter(tok.items()))
        if kind == "transport":
            return max(self.path_duration[m["path"]] for m in body["moves"])
        if kind == "idle":
            return body["duration"]
        if kind == "latch":
            return self.device["controller"]["duration"]
        if kind == "pulse" and "calibration" in body:
            return self.calibrations[body["calibration"]]["duration"]
        if kind == "broadcast":
            return self.profiles[body["profile"]]["duration"]
        if kind == "readout" and "calibration" in body:
            return self.device["readouts"][body["calibration"]]["duration"]
        if kind == "pump" and "calibration" in body:
            return self.device["pumps"][body["calibration"]]["duration"]
        if kind == "barrier":
            return 0
        return None

    def token(self, tok: dict) -> str:
        """One instruction of any stage, in the paper's syntax."""
        kind, body = next(iter(tok.items()))
        if kind == "source":  # the bound program: a token and its candidate declarations
            text = self.token(tok["source"])
            prefix = {"pulse": "c", "measure": "a", "reset": "p"}.get(next(iter(tok["source"])), "")
            return text[:-1] + "   {" + ", ".join(f"{prefix}{k}" for k in tok["candidates"]) + "};"
        if kind == "gate":
            gate, g = next(iter(body["operation"]["gate"].items()))
            if gate == "cx":
                return f"{self.guard(body)}CX q{g['control']},q{g['target']};"
            return (f"{self.guard(body)}U({expr(g['theta'])},{expr(g['phi'])},{expr(g['lambda'])}) "
                    f"q{g.get('target', g.get('ion'))};")
        if kind == "transport":
            moves = ", ".join(f"q{m['ion']}:{self.sites[self.paths[m['path']]['src']]}->"
                              f"{self.sites[self.paths[m['path']]['dst']]}[p{m['path']}]" for m in body["moves"])
            return f"transport {moves};"
        if kind == "idle":
            return f"idle {body['duration']};"
        if kind == "latch":
            bits = ",".join(f"c{b}" for b in body["bits"])
            return f"latch saved <- ({bits}) == {body['value']};"
        if kind == "barrier":
            ops = body.get("boundary", {}).get("operands", [])
            return f"{self.guard(body)}barrier " + ",".join(f"q{i}" for i in ops) + ";"
        if kind == "pulse":
            name, ions = pulse(body["operation"]["pulse"])
            text = f"{self.guard(body)}pulse {name} " + ",".join(f"q{i}" for i in ions)
            if "calibration" not in body:
                return text + ";"
            c = self.calibrations[body["calibration"]]
            return f"{text} @{self.sites[c['site']]} via c{body['calibration']};"
        if kind == "broadcast":
            prof = self.profiles[body["profile"]]
            site = ",".join(self.sites[s] for s in prof["sites"])
            name, _ = pulse(body["pulses"][0]["pulse"])
            ions = ",".join(f"q{pulse(p['pulse'])[1][0]}" for p in body["pulses"])
            return f"{self.guard(body)}broadcast {name} {ions} @{site} via b{body['profile']};"
        if kind in ("measure", "readout"):
            text = f"{self.guard(body)}{kind} q{body['ion']} -> c{body['bit']}"
            if "calibration" not in body:
                return text + ";"
            c = self.device["readouts"][body["calibration"]]
            return f"{text} @{self.sites[c['site']]} via a{body['calibration']};"
        if kind in ("reset", "pump"):
            text = f"{self.guard(body)}{kind} q{body['ion']}"
            if "calibration" not in body:
                return text + ";"
            c = self.device["pumps"][body["calibration"]]
            return f"{text} @{self.sites[c['site']]} via p{body['calibration']};"
        raise ValueError(f"unknown instruction {kind}")

    def initial(self, placement: list[int]) -> str:
        return "init " + ",".join(f"q{i}@{self.sites[s]}" for i, s in enumerate(placement)) + ";"

    def program(self, stage: dict, times: list[int] | str | None = None) -> tuple[list[str], int]:
        """A stage as lines; `times="sequential"` or a list of starts adds `// t=.., d=..`."""
        lines = [self.initial(stage["initial"])] if "initial" in stage else []
        texts = [self.token(tok) for tok in stage["code"]]
        pad = max([39] + [len(t) + 1 for t in texts])  # the paper's column, wider when a line needs it
        clock = 0
        for i, tok in enumerate(stage["code"]):
            text, d = texts[i], self.duration(tok)
            if times == "sequential":
                text = f"{text:<{pad}s}// t={clock}, d={d}"
                clock += d or 0
            elif times:
                text = f"{text:<{pad}s}// t={times[i]}, d={d}"
            lines.append(text)
        return lines, clock

    def chain_lines(self, timed: dict, chain: dict) -> list[str]:
        """The chain-ordered timeline: the timed instructions with the inserted exchanges."""
        at, ex, rows = 0, 0, []
        for i, is_ex in enumerate(chain["isExchange"]):
            start, d = chain["starts"][i], chain["durations"][i]
            if is_ex:
                trap, a, b = chain["exchanges"][ex]
                ex += 1
                text = f"exchange q{a},q{b} @{self.sites[trap]};"
            else:
                text = self.token(timed["code"][at])
                at += 1
            rows.append((text, start, d))
        pad = max([39] + [len(t) + 1 for t, _, _ in rows])
        return [f"{t:<{pad}s}// t={s}, d={d}" for t, s, d in rows]
