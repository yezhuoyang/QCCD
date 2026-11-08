"""The furniture the five Learn pages share (2026-11-07, from a reader's comments on
/compilation/: "hard to follow", "goes straight into symbols", "add a summary, a
conclusion, and a hover that explains a word").

Five pages tell one story -- Physics (the machine), Language (how it is told what to
do), Rules (what it may do), Compilation (from a circuit to a programme), Evaluation
(how a design is judged) -- and each page now carries:

  * the STORY strip under its title, saying where in that story it stands;
  * an "On this page" box: what the reader is about to learn, before any detail;
  * a QUESTION before the technical part of a section, so the detail answers something;
  * "In this section" boxes at the head of the long sections, BRIDGE sentences between
    them, and a "What to remember" box at the end, with the way on to the next page;
  * the GLOSSARY: the first mention of an important word in each section is a link.
    Hovering it shows a card with the word explained in a sentence or two; clicking it
    goes to where the word is defined, on this page or another.  One table below holds
    every word, its sentence and its definition's address, so the five pages cannot
    explain a word five ways;
  * RUNBOXES: an animation is a poster of the device with a Play button, and the Studio
    page is loaded and played when the reader presses it.  A page used to load nineteen
    full Studio pages at once; now it loads the one the reader asked for, and that one
    plays as soon as it is pressed.

Nothing here restructures a page's content DOM beyond adding these elements: the comment
layer anchors a note by a DOM path, and a wrapper around existing prose would orphan
every note already pinned.  The glossary marks are inline `<a>` elements inside the
paragraph they annotate.
"""

from __future__ import annotations

import html
import json
import re

# ------------------------------------------------------------------------- the story

#: The Learn pages in reading order: (key, path, title, the one-line role in the story).
STORY = (
    ("physics", "physics/", "Physics", "the machine: ions, traps, lasers, heat"),
    ("language", "language/", "Language", "how a programme tells the machine what to do"),
    ("rules", "rules/", "Rules", "what the machine may and may not do"),
    ("compilation", "compilation/", "Compilation", "from a circuit to a verified programme"),
    ("evaluation", "evaluation/", "Evaluation", "how a design is judged, in numbers"),
)


def strip(active: str, depth: int = 1) -> str:
    """The story strip: the five pages in order, the current one marked."""
    root = "../" * depth
    out = ['<nav class="story" aria-label="the Learn pages, in order">']
    for i, (key, path, title, role) in enumerate(STORY, 1):
        cur = ' aria-current="page"' if key == active else ""
        out.append(f'<a href="{root}{path}"{cur}><span class="n">{i}</span><span class="w"><b>{title}</b>'
                   f'<small>{html.escape(role)}</small></span></a>')
    out.append("</nav>")
    return "".join(out)


def prev_next(active: str) -> tuple[tuple | None, tuple | None]:
    keys = [s[0] for s in STORY]
    i = keys.index(active)
    return (STORY[i - 1] if i > 0 else None), (STORY[i + 1] if i + 1 < len(STORY) else None)


# ------------------------------------------------------------------------- the boxes

def summary(items: list[str], title: str = "On this page", lead: str = "") -> str:
    """The box a page or a long section opens with: what the reader is about to learn."""
    lis = "".join(f"<li>{x}</li>" for x in items)
    lead = f"<p>{lead}</p>" if lead else ""
    return f'<div class="sum"><p class="sumh">{html.escape(title)}</p>{lead}<ul>{lis}</ul></div>'


def section(text: str) -> str:
    """One line at the head of a section: what it is for and what it will show."""
    return f'<div class="sum sec"><b>In this section</b> {text}</div>'


def ask(question: str, answer: str = "") -> str:
    """A question before the detail, so the detail answers something.  `answer` is the
    one-sentence answer the section then unpacks."""
    a = f'<p class="a">{answer}</p>' if answer else ""
    return f'<div class="ask"><p class="q">{question}</p>{a}</div>'


def bridge(text: str) -> str:
    """The sentence between two sections: what was just shown, and why the next follows."""
    return f'<p class="bridge">{text}</p>'


def recap(items: list[str], active: str, depth: int = 1, title: str = "What to remember",
          more: str = "") -> str:
    """The box a page ends with: the points to keep, and the way on."""
    lis = "".join(f"<li>{x}</li>" for x in items)
    prev, nxt = prev_next(active)
    root = "../" * depth
    ways = []
    if nxt:
        ways.append(f'<a class="go" href="{root}{nxt[1]}"><small>Next in the story</small><b>{nxt[2]}: {html.escape(nxt[3])}</b></a>')
    if prev:
        ways.append(f'<a class="go back" href="{root}{prev[1]}"><small>Before this</small><b>{prev[2]}: {html.escape(prev[3])}</b></a>')
    more = f'<p class="more">{more}</p>' if more else ""
    return (f'<div class="recap"><p class="sumh">{html.escape(title)}</p><ol>{lis}</ol>{more}'
            f'<div class="ways">{"".join(ways)}</div></div>')


# ------------------------------------------------------------------------- the runbox

def runbox(src: str, poster_svg: str, title: str, cls: str = "", what: str = "") -> str:
    """An animation as a poster with a Play button.  `src` is the Studio page (without
    its hash); pressing Play loads it in embed mode and plays the programme once
    (`#embed&step=1&go`, see HASH_JS), after which Step, Reset and the slider are the
    reader's.  `poster_svg` is the device with its ions where the programme starts."""
    what = what or "the programme, played once; then step it or scrub it"
    return (f'<div class="runbox {cls}" data-src="{html.escape(src)}" data-title="{html.escape(title)}">'
            f'<div class="poster" role="button" tabindex="0" aria-label="Play: {html.escape(title)}">'
            f'<div class="pic">{poster_svg}</div>'
            f'<span class="pp"><i></i>Play</span><span class="pw">{html.escape(what)}</span></div></div>')


# ------------------------------------------------------------------------- the glossary

#: Every word the Learn pages explain on hover.  `t` is the title the card shows, `d` the
#: explanation (plain text, one or two sentences), `u` where the word is defined, as a
#: site-relative address, and `a` the spellings that are marked in prose (the first one
#: is the canonical spelling).  Matching is whole-word; a spelling with a capital letter
#: is matched exactly, the others case-insensitively, with an optional plural "s".
GLOSSARY: dict[str, dict] = {
    # --- the machine (Physics)
    "ion": {"t": "Ion", "u": "physics/#trapping-a-single-ion", "a": ["ion"],
            "d": "A charged atom held in a trap by electric fields. On this website one ion carries one qubit (a second species beside it carries none and is there to be cooled)."},
    "qubit": {"t": "Qubit", "u": "physics/#the-qubit-inside-the-ion", "a": ["qubit"],
              "d": "Two internal states of an ion, used as the 0 and 1 of a quantum bit. A circuit names qubits; the compiler decides which ion carries each one."},
    "trap": {"t": "Trap (trapping site)", "u": "physics/#the-qccd-idea", "a": ["trapping site", "trap site", "trap", "site"],
             "d": "A well in the electric potential where one or a few ions rest. Ions are moved between traps; a two-qubit gate needs both ions in one trap. Also called a site; in the formal sections the set of traps is T."},
    "junction": {"t": "Junction", "u": "physics/#the-qccd-idea", "a": ["junction"],
                 "d": "A node where three or more rails meet. An ion passes through a junction but never rests on one (rule R2), and the crossing costs time and heat."},
    "rail": {"t": "Rail (segment)", "u": "physics/#the-qccd-idea", "a": ["rail", "segment"],
             "d": "The stretch of trap axis an ion is shuttled along between two nodes. In the device graph the rails are the edges, E."},
    "loop": {"t": "Loop", "u": "physics/#the-qccd-idea", "a": ["transport loop", "loop"],
             "d": "A closed path of rails and sites that the control system can turn as a whole, carrying every ion on it one slot round in one instruction."},
    "zone": {"t": "Zone type", "u": "physics/#the-qccd-idea", "a": ["zone type", "zone"],
             "d": "The label a site carries saying how many ions fit there and whether gates, measurement or cooling may happen in it."},
    "dock": {"t": "Dock (spur)", "u": "physics/#transport-primitives-and-what-each-one-costs", "a": ["dock", "spur"],
             "d": "A gate-capable trap hanging off a loop by one short rail, the spur. An ion riding the loop is docked into it for a gate and undocked afterwards."},
    "hop": {"t": "Hop", "u": "compilation/#def-trap-graph", "a": ["hop"],
            "d": "One machine cycle of transport: an ion carried from a trap to a neighbouring trap, through any junctions on the way without stopping. The compiler plans in hops, and every hop heats the ion."},
    "shuttle": {"t": "Shuttle", "u": "physics/#transport-primitives-and-what-each-one-costs", "a": ["shuttle", "shuttling"],
                "d": "The basic transport primitive: the well holding an ion slides along the rail to the next site. The cheapest move there is, about 5 microseconds and a tenth of a quantum of heat."},
    "split": {"t": "Split and merge", "u": "physics/#transport-primitives-and-what-each-one-costs", "a": ["split", "merge"],
              "d": "Pinching one well holding two ions into two wells, or joining two into one. Far hotter than a shuttle, because the confinement along the axis nearly vanishes on the way."},
    "primitive": {"t": "Transport primitive", "u": "physics/#transport-primitives-and-what-each-one-costs", "a": ["primitive"],
                  "d": "One of the elementary moves the hardware can make (shuttle, split, merge, junction crossing, swap, rotation), each with a measured time and a measured heat."},
    "nbar": {"t": "n̄, the motional quanta", "u": "physics/#trapping-a-single-ion", "a": ["n̄", "n&#772;", "motional quanta", "quanta"],
             "d": "The mean number of vibrational quanta an ion holds in its trap: how hot it is. Every move adds some, cooling removes them, and a two-qubit gate on a hot ion is worse. Almost every cost on this website is counted in these."},
    "heating": {"t": "Heating", "u": "physics/#where-the-noise-comes-from", "a": ["heating"],
                "d": "The rise of an ion's motional quanta: by every transport primitive it undergoes, and slowly by electric-field noise from the electrodes even when it rests (anomalous heating)."},
    "cooling": {"t": "Cooling", "u": "physics/#motion-as-the-currency", "a": ["cooling", "cool"],
                "d": "Taking an ion's motional quanta back out with laser light. On this machine a cool is one global 300 microsecond operation that cools every ion at once, through the second species beside each qubit ion."},
    "ms": {"t": "MS gate (Mølmer–Sørensen)", "u": "physics/#gates-and-why-they-need-cold-ions", "a": ["Mølmer–Sørensen", "MS gate", "MS pulse", "MS"],
           "d": "The two-qubit gate of a trapped-ion machine: one laser pulse reaching two ions in the same trap entangles them through their shared motion. It is the only way two qubits interact here, and it wants the ions cold."},
    "gate zone": {"t": "Gate zone", "u": "physics/#gates-and-why-they-need-cold-ions", "a": ["gate zone", "gate-capable"],
                  "d": "A trap the laser beams can reach, so a gate may happen there. Not every trap is one: on a ring with docks, only the docks are."},
    "lamb-dicke": {"t": "Lamb–Dicke regime", "u": "physics/#gates-and-why-they-need-cold-ions", "a": ["Lamb–Dicke"],
                   "d": "The regime in which the ion's motion is small against the laser wavelength, where the gate works cleanly. It is why gates need cold ions: the condition fails as the motional quanta grow."},
    "spam": {"t": "SPAM", "u": "physics/#the-qubit-inside-the-ion", "a": ["SPAM"],
             "d": "State preparation and measurement: resetting a qubit with optical pumping and reading it out by fluorescence. Both have their own error, charged once per use."},
    "paul trap": {"t": "Paul trap", "u": "physics/#trapping-a-single-ion", "a": ["Paul trap"],
                  "d": "A trap that holds a charged particle with an oscillating electric field, since no static field can. The ion sits where the oscillating field vanishes."},
    "qccd": {"t": "QCCD", "u": "physics/#the-qccd-idea", "a": ["quantum charge-coupled device", "QCCD"],
             "d": "The quantum charge-coupled device: many small traps joined by rails, with ions moved between them so that any two qubits can be brought together. Short chains keep gates fast; transport is the price."},
    "broadcast": {"t": "Broadcast wiring", "u": "physics/#the-control-system", "a": ["broadcast wiring", "broadcast-wired", "broadcast"],
                  "d": "Electrodes playing the same role on every trap are driven by one shared signal, so the control stays small as the machine grows. The price: only one kind of motion can happen in a cycle (rule R22)."},
    "dac": {"t": "DAC", "u": "physics/#the-control-system", "a": ["DAC"],
            "d": "A digital-to-analogue converter: the circuit that drives one electrode's voltage. Counting DACs is how the hardware cost of a design's control is measured."},
    "control plane": {"t": "Control plane", "u": "physics/#the-control-system", "a": ["control plane"],
                      "d": "The wiring between the DACs and the electrodes: which signals exist, which sites each one reaches, and the switches that let a site opt out."},
    "waveform": {"t": "Waveform", "u": "compilation/#algo-route", "a": ["waveform"],
                 "d": "The voltage sequence played on the electrodes to make one motion. On a broadcast-wired machine one cycle plays one waveform, so every ion moving in that cycle makes the same motion (rule R22)."},
    "c2lr": {"t": "C2LR (swap-or-stay)", "u": "physics/#swap-or-stay-transport-without-routes", "a": ["swap-or-stay", "C2LR"],
             "d": "A lattice in which nothing is routed: one shared waveform plays at every junction and each crystal chooses only to shift toward its junction or stay. Quantinuum's design; its rules are C1 to C10."},
    "anomalous": {"t": "Anomalous heating", "u": "physics/#where-the-noise-comes-from", "a": ["anomalous heating"],
                  "d": "The slow warming of an ion that nothing moves, from electric-field noise off the electrode surfaces. Charged to every ion for every microsecond it waits."},
    "dephasing": {"t": "Dephasing (memory error)", "u": "physics/#where-the-noise-comes-from", "a": ["dephasing", "memory error"],
                  "d": "The loss of a waiting qubit's phase. It grows with time, which is how a slow programme turns into a worse logical error rate."},
    "noise model": {"t": "Noise model", "u": "physics/#how-this-website-simulates-noise", "a": ["noise model"],
                    "d": "A text whose clauses say which Pauli error goes where, with what probability, read off the replay of a programme. It turns a programme into a noisy stim circuit."},
    "replay": {"t": "Replay", "u": "physics/#how-this-website-simulates-noise", "a": ["replay", "replayed", "replaying"],
               "d": "Running a programme on the device document instruction by instruction, keeping every ion's position, heat and the clock. Everything the rules and the metrics read is read off the replay."},
    "stim": {"t": "stim circuit", "u": "evaluation/#circuit", "a": ["stim circuit", "stim"],
             "d": "The stabilizer circuit, in the stim simulator's format, that a programme becomes once its gates are translated and the noise model's clauses are applied. Sampling it gives the logical error rate."},
    "decoder": {"t": "Decoder", "u": "physics/#how-this-website-simulates-noise", "a": ["decoder", "decoding"],
                "d": "The classical algorithm that reads the measured syndromes of a code and guesses which error happened. A shot it corrects wrongly is a logical error."},
    "ler": {"t": "Logical error rate", "u": "evaluation/#ler", "a": ["logical error rate", "LER"],
            "d": "The fraction of sampled shots in which the decoder's correction leaves a logical qubit wrong, reported with a 95 % interval. What the Goal 1 board ranks."},
    "error budget": {"t": "Error budget", "u": "physics/#how-this-website-simulates-noise", "a": ["error budget"],
                     "d": "Each noise channel's expected number of faults, added over every place it writes. It says where a design's faults come from, beside its logical error rate."},
    "doppler": {"t": "Doppler cooling", "u": "physics/#motion-as-the-currency", "a": ["Doppler cooling", "Doppler"],
                "d": "Cooling by scattering photons from a laser tuned just below resonance; fast, global, and limited to about ten quanta. Sideband cooling then goes to the ground state."},
    "sympathetic": {"t": "Sympathetic cooling", "u": "physics/#motion-as-the-currency", "a": ["sympathetically", "sympathetic"],
                    "d": "Cooling a qubit ion through a second ion of another species in the same well, which shares its motion, so the qubit's state is never touched by the cooling light."},
    "secular": {"t": "Secular frequency", "u": "physics/#trapping-a-single-ion", "a": ["secular frequency"],
                "d": "The frequency at which an ion oscillates in its well, a few megahertz. The motional quanta are counted in units of this oscillator."},
    # --- the language
    "programme": {"t": "Programme", "u": "language/#semantics", "a": ["hardware programme", "programme"],
                  "d": "A list of instructions for the device, one per machine cycle: moves, gates, cooling, measurements. Written in the hardware language, produced by the compiler or by hand, judged by the rules."},
    "instruction": {"t": "Instruction (cycle)", "u": "language/#semantics", "a": ["instruction", "machine cycle", "cycle"],
                    "d": "One step of a programme, and one cycle of the machine: everything in it happens together, and the next starts when it ends. The clock, the heat and the rules are all read per cycle."},
    "statement": {"t": "Statement", "u": "language/#syntax", "a": ["statement"],
                  "d": "One line of the hardware language, a call such as p.move(\"d0\", \"C1\", \"C2\"). The seven core statements each become one instruction; the shorthands stand for lists of them."},
    "ir": {"t": "The control IR", "u": "language/#ir", "a": ["control IR", "IR"],
           "d": "The JSON the compiler emits and the verifier replays: one record per instruction. The language's text and the compiler's output meet there."},
    "move": {"t": "p.move", "u": "language/#move", "a": ["p.move"],
             "d": "The shorthand for one ion's hop from a site to a neighbouring site; it stands for one simd instruction with a single participant."},
    "rotate": {"t": "Rotation (p.rotate)", "u": "language/#rotate", "a": ["rigid rotation", "rotation", "p.rotate"],
               "d": "Turning a whole loop by some number of slots in one instruction: every ion on the loop moves together, which is one waveform and no empty slot needed."},
    "init": {"t": "p.init", "u": "language/#init", "a": ["p.init"],
             "d": "The first statement of every programme: which ion starts in which site. The compiler's certificate records the same map."},
    # --- the rules
    "rule": {"t": "Rule", "u": "rules/#language", "a": ["rule"],
             "d": "One assertion about the device and what a programme does on it, written in a small logical language and decided by the checker on the replay. A programme that fails any rule of its set is refused."},
    "rule set": {"t": "Rule set", "u": "rules/#sets", "a": ["rule set", "ruleset"],
                 "d": "The rules a machine assumes, with their numbers. The standard set is this website's machine; a paper's set says what that paper's model assumed. The compiler takes one as input."},
    "verifier": {"t": "Verifier (checker)", "u": "rules/#language", "a": ["verifier", "rule checker", "checker"],
                 "d": "The code that replays a programme and decides every rule of a rule set on it. It shares no code with the compiler, so a compiler bug shows as a failed rule."},
    "R1": {"t": "Rule R1: capacity", "u": "rules/#R1", "a": ["R1"], "d": "A site never holds more ions than its capacity, at any instant."},
    "R2": {"t": "Rule R2: junctions", "u": "rules/#R2", "a": ["R2"], "d": "An ion passes through a junction but never rests on one, and one ion transits a junction per cycle."},
    "R3": {"t": "Rule R3: segment capacity", "u": "rules/#R3", "a": ["R3"], "d": "A rail carries at most its capacity of ions in one cycle."},
    "R4": {"t": "Rule R4: one movement class", "u": "rules/#R4", "a": ["R4"], "d": "Under broadcast wiring at most one movement class is active per cycle, and a site wired to it takes part unless switched out."},
    "R4d": {"t": "Rule R4d: drivable", "u": "rules/#R4d", "a": ["R4d"], "d": "Every cycle's motion can actually be driven by the channels the device declares."},
    "R5": {"t": "Rule R5: no head-on exchange", "u": "rules/#R5", "a": ["R5"], "d": "Two ions never swap places along one rail in one cycle."},
    "R6": {"t": "Rule R6: gates in gate zones", "u": "rules/#R6", "a": ["R6", "R6b"], "d": "A gate happens only in a trap that can gate, and a two-qubit gate only on two ions in the same such trap."},
    "R7": {"t": "Rule R7: the gate budget", "u": "rules/#R7", "a": ["R7", "R7c"],
           "d": "At a two-qubit gate both ions must be below the motional budget (1.0 quanta). It is why cooling is inserted before gates, never after everything."},
    "R8": {"t": "Rule R8: one action per ion", "u": "rules/#R8", "a": ["R8"], "d": "An ion does one thing per cycle."},
    "R10": {"t": "Rule R10: the programme computes the circuit", "u": "compilation/#algo-correct", "a": ["R10"],
            "d": "The compiled programme carries out the input circuit: every gate happens where its certificate claims (decided by a proved checker, O1), and the pulses that fire compose to the circuit's gates (computed, O2)."},
    "R11": {"t": "Rule R11: one direction per loop", "u": "rules/#R11", "a": ["R11"], "d": "A loop turns one way in a cycle."},
    "R12": {"t": "Rule R12: one gate per trap per cycle", "u": "rules/#R12", "a": ["R12"], "d": "A trap hosts at most one gate in a cycle."},
    "R13": {"t": "Rule R13: chain length", "u": "rules/#R13", "a": ["R13"], "d": "A gate chain stays short: two-qubit gates degrade sharply beyond about fifteen ions in a trap."},
    "R15": {"t": "Rule R15: additive heating", "u": "rules/#R15", "a": ["R15"], "d": "Heat from consecutive moves is added, which is an upper bound: a phase-aware schedule could cancel some of it."},
    "R16": {"t": "Rule R16: gate error grows with heat", "u": "rules/#R16", "a": ["R16"], "d": "The two-qubit gate's error is linear in the quanta the ions bring to it."},
    "R17": {"t": "Rule R17: anomalous heating", "u": "rules/#R17", "a": ["R17"], "d": "Every ion warms with elapsed time, moving or not."},
    "R18": {"t": "Rule R18: a bend is not a junction", "u": "rules/#R18", "a": ["R18"], "d": "A corner where two rails meet is ordinary transport; only a node of degree three or more costs a junction crossing."},
    "R19": {"t": "Rule R19: junction degree", "u": "rules/#R19", "a": ["R19"], "d": "At most four rails meet at a node on a planar chip."},
    "R20": {"t": "Rule R20: rail angle", "u": "rules/#R20", "a": ["R20"], "d": "Two rails leaving a node are at least 60 degrees apart, room for their electrodes."},
    "R21": {"t": "Rule R21: planar rails", "u": "rules/#R21", "a": ["R21"], "d": "A rail meets only the nodes it ends at, and two rails cross only at a node they share."},
    "R22": {"t": "Rule R22: one waveform per cycle", "u": "rules/#R22", "a": ["R22"],
            "d": "Every ion moving in a cycle makes the same motion, hop by hop, because broadcast wiring plays one waveform at a time. The rule that shapes the router most."},
    # --- compilation
    "compiler": {"t": "The compiler", "u": "compilation/#design", "a": ["compiler"],
                 "d": "The program that takes a circuit and a device and writes the programme the device runs, plus a certificate of what it did. Treated as untrusted: everything it writes is checked afterwards."},
    "circuit": {"t": "Circuit", "u": "compilation/#def-circuit", "a": ["quantum circuit", "circuit"],
                "d": "The input: a list of gates on named qubits, in program order, written in OpenQASM. Two gates must keep their order only if they share a qubit."},
    "qasm": {"t": "OpenQASM", "u": "compilation/#input", "a": ["OpenQASM 2.0", "OpenQASM", "QASM"],
             "d": "The standard text format for quantum circuits, and the compiler's input language: qreg, h, cx, measure."},
    "pulse": {"t": "R pulse", "u": "compilation/#input", "a": ["R pulse", "single-ion pulse", "beam"],
              "d": "One resonant laser pulse on one ion, R(θ, φ): a rotation by θ about an axis in the X–Y plane. With VZ and MS it is one of the machine's three operations."},
    "vz": {"t": "VZ, the frame update", "u": "compilation/#input", "a": ["frame update", "VZ"],
           "d": "A rotation about Z. On a machine with per-ion laser phase it is free bookkeeping; here, with shared beams, the clock charges it as three pulses. The programme still writes it as one VZ."},
    "u3": {"t": "u3", "u": "compilation/#input", "a": ["u3"],
           "d": "OpenQASM's general single-qubit gate, u3(θ, φ, λ). Every named single-qubit gate is a u3 with fixed angles, and every u3 is one R pulse and one VZ."},
    "identities": {"t": "The pulse identities", "u": "compilation/#thm-identities", "a": ["pulse identities", "identities", "identity"],
                   "d": "Two proved equations: any u3 is one R pulse and one VZ, and a CNOT is one MS pulse between four R pulses. They are why the compiler's pulse table can be right."},
    "lowering": {"t": "Lowering", "u": "compilation/#algo-lower", "a": ["lowering", "lowered"],
                 "d": "Rewriting a gate on three or more qubits (a Toffoli) into one- and two-qubit gates before anything else, since the router only ever brings two ions together."},
    "dag": {"t": "Dependency graph", "u": "compilation/#def-circuit", "a": ["dependency graph", "dependency DAG", "DAG"],
            "d": "An arrow from each operation to the next one on each of its qubits. An operation may run only after everything with a path of arrows into it; operations with no path between them are independent."},
    "interaction graph": {"t": "Interaction graph", "u": "compilation/#def-circuit", "a": ["interaction graph"],
                          "d": "The qubits as nodes, with an edge weighted by how many two-qubit gates each pair shares. It forgets the order and keeps only who must meet whom, which is what placement needs."},
    "placement": {"t": "Placement", "u": "compilation/#algo-layout", "a": ["placement", "placer", "layout"],
                  "d": "Choosing which ion carries each qubit and which trap it starts in, so that qubits that gate together start close together. Scored by gates times hops; an NP-hard assignment problem, solved heuristically."},
    "routing": {"t": "Routing", "u": "compilation/#algo-route", "a": ["routing", "router", "routed"],
                "d": "Finding each moving ion a path through the traps, one hop per cycle with waits allowed, so that no rule is broken at any cycle. Multi-agent path finding, solved by prioritised A* with a reservation table."},
    "layer": {"t": "Layer", "u": "compilation/#def-asap", "a": ["layer", "layering"],
              "d": "A set of operations of the circuit that depend on nothing in each other, so they can run at the same time. Operations are put in the earliest layer they can be (ASAP), and layers run one after another."},
    "round": {"t": "Round emission", "u": "compilation/#algo-layers", "a": ["round emission", "round"],
              "d": "Within a layer, the first pulse of every gate is emitted together, then the second of every gate, and so on. Each gate is in its own trap, so a layer of many gates takes as long as one gate."},
    "meeting trap": {"t": "Meeting trap", "u": "compilation/#def-meeting-trap", "a": ["meeting trap", "site selection"],
                     "d": "The trap chosen for a two-qubit gate: it can gate, is not already taken by another gate of the layer, has room for both ions, and is nearest to the pair counting both journeys."},
    "undock": {"t": "Undocking (going home)", "u": "compilation/#def-undock", "a": ["undocking", "undock"],
               "d": "After a layer, an ion that gated and is not needed by the very next layer is sent back to where it started, keeping the few gate-capable traps free."},
    "conveyor": {"t": "The conveyor pass", "u": "compilation/#algo-rotate", "a": ["conveyor pass", "conveyor"],
                 "d": "The compiler's second way of routing, for a loop with docks: the loop is turned like a conveyor belt to bring each rider beside its dock. It works exactly when every gate pairs a rider with a docked ion."},
    "certificate": {"t": "Certificate", "u": "compilation/#def-programme", "a": ["certificate"],
                    "d": "What the compiler hands over beside the programme: which ion is which qubit, where each starts, every move with its cycle, and one witness per gate. The checks test every claim in it."},
    "witness": {"t": "Witness", "u": "compilation/#def-programme", "a": ["witness"],
                "d": "The compiler's claim about one gate of the circuit: it happens at instruction k, in trap T, on ions a and b. The proved checker replays the moves and tests whether that is so."},
    "implements": {"t": "Implements", "u": "compilation/#def-implements", "a": ["Implements"],
                   "d": "The ten conditions a certificate must meet for the programme to carry out the circuit: nothing left out, no ion teleports, every gate witnessed once where its ions really are, in the circuit's order. The checker decides exactly this."},
    "proved checker": {"t": "The proved checker (Lean)", "u": "compilation/#thm-checker", "a": ["proved checker", "machine-checked proof", "Lean"],
                       "d": "The program that decides Implements, together with a proof in the Lean proof assistant that whenever it answers yes, the definition really holds. It reads the device from its document, never from the compiler."},
    "o1": {"t": "O1: every gate happens where claimed", "u": "compilation/#algo-correct", "a": ["O1"],
           "d": "The first half of R10, decided by the proved checker: every witness names the right ions and finds them together in a trap that can gate, every move is a real hop, in the circuit's order."},
    "o2": {"t": "O2: the pulses compute the circuit", "u": "compilation/#check-semantics", "a": ["O2"],
           "d": "The second half of R10, computed: the pulses are read back out of the programme and composed; the result must equal the circuit, as a stabilizer tableau for a Clifford circuit, as exact matrices otherwise."},
    "clifford": {"t": "Clifford circuit", "u": "compilation/#check-semantics", "a": ["Clifford"],
                 "d": "A circuit of H, S, CNOT, the Pauli gates and measurements. Its effect can be tracked as a stabilizer tableau in polynomial time, so circuits of any size are checked; a T gate or an arbitrary angle falls outside."},
    "tableau": {"t": "Stabilizer tableau", "u": "compilation/#check-semantics", "a": ["stabilizer tableau", "tableau"],
                "d": "A table of Pauli operators that describes the state of a Clifford circuit, growing only with the square of the number of qubits. Two Clifford circuits with the same tableau compute the same thing."},
    "cooling pass": {"t": "Cooling insertion", "u": "compilation/#algo-cool", "a": ["cooling insertion", "cooling pass"],
                     "d": "The pass that replays the programme with the heating model and inserts one global cool just before any two-qubit gate whose ions would be over the budget (R7). One pass suffices."},
    "qap": {"t": "Quadratic assignment (QAP)", "u": "compilation/#algo-layout", "a": ["quadratic assignment", "QAP"],
            "d": "The problem of assigning items to places to minimise a sum of (flow between items) times (distance between places). Placement is one, and it is NP-hard, so the compiler uses heuristics."},
    "mapf": {"t": "Multi-agent path finding", "u": "compilation/#algo-route", "a": ["multi-agent path finding", "MAPF"],
             "d": "Finding collision-free paths for many agents moving at once on a graph. Routing a layer is one, with capacities and the one-waveform rule added; also NP-hard."},
    "fiedler": {"t": "Fiedler vector", "u": "compilation/#algo-layout", "a": ["Fiedler vector", "Fiedler"],
                "d": "The eigenvector of the second-smallest eigenvalue of a graph's Laplacian. Sorting the qubits by it lays them on a line so that heavily connected pairs land next to each other."},
    "astar": {"t": "A* search", "u": "compilation/#algo-route", "a": ["A*"],
              "d": "Shortest-path search guided by a heuristic that never overestimates (here: the hop distance to the goal). The router runs it over states (cycle, trap) with wait and hop as the actions."},
    "makespan": {"t": "Makespan", "u": "compilation/#algo-route", "a": ["makespan"],
                 "d": "The number of cycles until the last mover of a layer arrives: what the router minimises."},
    "eccentricity": {"t": "Eccentricity", "u": "compilation/#def-trap-graph", "a": ["eccentricity"],
                     "d": "A trap's distance, in hops, to the trap farthest from it. The most central trap has the smallest eccentricity, and the placer seeds there."},
    "asap": {"t": "ASAP layering", "u": "compilation/#def-asap", "a": ["ASAP"],
             "d": "As soon as possible: every operation goes into the layer right after the latest operation it depends on."},
    "notation": {"t": "The notation", "u": "compilation/#notation", "a": ["notation"],
                 "d": "The symbols the formal sections use: A the device, T the traps, J the junctions, E the rails, cap the capacity, gate and meas the traps where a gate or a measurement may happen, L the loops."},
    # --- evaluation
    "metric": {"t": "Metric", "u": "evaluation/#language", "a": ["metric"],
               "d": "A named number an evaluation computes from the replay: runtime, heat, DAC count, size. Every number on the leaderboard is one, and can be recomputed from the three texts."},
    "requirement": {"t": "Requirement", "u": "evaluation/#language", "a": ["requirement"],
                    "d": "An assertion a design must meet to be ranked, such as a hardware budget. Decided, like a rule, and named when it fails."},
    "channel": {"t": "Noise channel", "u": "evaluation/#noise", "a": ["noise channel", "channel"],
                "d": "One clause of a noise model: after (or before) a kind of operation, this Pauli error, on these ions, with this probability read off the replay."},
    "evaluation": {"t": "Evaluation", "u": "evaluation/#texts", "a": ["evaluation"],
                   "d": "A text in the evaluation language: metrics, requirements and noise channels. Read over the replay of a programme on a device, it yields every number a design is judged by and the noisy circuit it becomes."},
    "goal1": {"t": "Goal 1", "u": "board/goal1/", "a": ["Goal 1"],
              "d": "The leaderboard that ranks every memory experiment under 200 ions by logical error rate per round and logical qubit, under the cited noise model qccd-noise@2."},
    "budget": {"t": "Budget", "u": "evaluation/#hardware", "a": ["hardware budget", "budget"],
               "d": "A limit a design must stay under: the motional budget at a gate (R7), or the hardware budget of DACs, junctions and area a task declares."},
}

#: Where terms with no `u` of their own would go; every entry above has one.
_ALIAS_RX: list[tuple[str, "re.Pattern[str]"]] = []


def _compile() -> list[tuple[str, "re.Pattern[str]"]]:
    if _ALIAS_RX:
        return _ALIAS_RX
    pairs = []
    for key, g in GLOSSARY.items():
        for a in g["a"]:
            pairs.append((key, a))
    # longest spelling first, so "trap graph" is not claimed by "trap"
    pairs.sort(key=lambda p: -len(p[1]))
    for key, a in pairs:
        exact = any(c.isupper() for c in a) or not a[0].isalpha()
        body = re.escape(a)
        if a[-1].isalpha():
            body += r"(?:s|es)?"
        rx = re.compile(r"(?<![\w̄&#-])" + body + r"(?![\w̄-])", 0 if exact else re.I)
        _ALIAS_RX.append((key, rx))
    return _ALIAS_RX


#: Tags whose text is never marked: code, headings, links, drawings, maths, controls.
_SKIP_TAGS = {"code", "pre", "a", "h1", "h2", "h3", "h4", "svg", "script", "style", "button", "select",
              "option", "textarea", "title", "summary", "var", "kbd", "samp", "nav", "label", "input"}
#: Elements whose class puts them off limits (the studio's own hover spans, the formulas).
_SKIP_CLASS = re.compile(r'class="[^"]*\b(?:h|eq|gl|alg|assert|ev|stim|grammar|ident|kv|pulses|story|sumh|pp|pw)\b')
_TAG = re.compile(r"<(/?)([A-Za-z][A-Za-z0-9]*)([^>]*)>")
_VOID = {"br", "img", "hr", "input", "meta", "link", "path", "circle", "rect", "line", "use", "wbr", "col"}


def mark(body: str, here: str, depth: int, once_per: str = "section") -> tuple[str, list[str]]:
    """Mark the first mention of each glossary word in each section of `body` as a link
    to its definition.  `here` is this page's site path (the link to a word defined on
    this page is a bare fragment); `depth` is the page's depth below the site root.
    Returns the marked body and the keys used, for the card data the page carries."""
    rxs = _compile()
    root = "../" * depth
    out: list[str] = []
    stack: list[tuple[str, bool]] = []     # (tag, skipping)
    skipping = 0
    cards = 0                              # inside a <section> card (a rule, a statement, a gate)
    seen: set[str] = set()
    used: list[str] = []
    pos = 0
    for m in _TAG.finditer(body):
        text = body[pos:m.start()]
        if text and not skipping and text.strip():
            out.append(_mark_text(text, rxs, seen, used, here, root))
        else:
            out.append(text)
        closing, tag, attrs = m.group(1), m.group(2).lower(), m.group(3)
        out.append(m.group(0))
        pos = m.end()
        if tag in _VOID or attrs.endswith("/"):
            continue
        if closing:
            # pop to the matching tag; a stray close tag pops nothing
            for i in range(len(stack) - 1, -1, -1):
                if stack[i][0] == tag:
                    for t, s in stack[i:]:
                        if s:
                            skipping -= 1
                        if t == "section":
                            cards -= 1
                    del stack[i:]
                    break
            continue
        # a new section starts the count again: every h2, and an h3 that heads a part of the
        # text rather than one card of a list (a rule, a statement, a gate: those are
        # <section> elements, and sixty cards each marking "trap" once is noise, not help)
        if tag == "h2" or (tag == "h3" and cards == 0):
            seen = set()
        if tag == "section":
            cards += 1
        skip = tag in _SKIP_TAGS or bool(_SKIP_CLASS.search(attrs))
        stack.append((tag, skip))
        if skip:
            skipping += 1
    out.append(body[pos:])
    return "".join(out), used


def _mark_text(text: str, rxs, seen: set[str], used: list[str], here: str, root: str) -> str:
    # text nodes carry entities; a match never crosses one because `&` and `;` are not
    # word characters, except the n-bar spelled as an entity, which is listed as such.
    # A mark already made is opaque to the later words: the first version searched the
    # whole text again and marked "rules" inside the href of the mark before it
    parts: list[tuple[str, bool]] = [(text, False)]        # (text, already marked)
    for key, rx in rxs:
        if key in seen:
            continue
        for i, (seg, marked) in enumerate(parts):
            if marked:
                continue
            m = rx.search(seg)
            if not m:
                continue
            g = GLOSSARY[key]
            u = g["u"]
            href = "#" + u.split("#", 1)[1] if u.startswith(here) and "#" in u else root + u
            seen.add(key)
            if key not in used:
                used.append(key)
            parts[i:i + 1] = [(seg[:m.start()], False),
                              (f'<a class="gl" data-gl="{key}" href="{href}">' + m.group(0) + "</a>", True),
                              (seg[m.end():], False)]
            break
    return "".join(seg for seg, _ in parts)


def card_data(used: list[str], here: str, depth: int) -> str:
    """The `<script type="application/json">` the hover card reads: title, text and where
    the definition is, for the words this page marks."""
    root = "../" * depth
    data = {}
    for key in used:
        g = GLOSSARY[key]
        u = g["u"]
        page = next((s[2] for s in STORY if u.startswith(s[1])), "the Leaderboard" if u.startswith("board/") else "")
        here_page = u.startswith(here)
        href = "#" + u.split("#", 1)[1] if here_page and "#" in u else root + u
        data[key] = {"t": g["t"], "d": g["d"], "u": href,
                     "w": ("on this page" if here_page else f"on the {page} page") if page else ""}
    blob = json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    return f'<script type="application/json" id="glossary">{blob}</script>'


def apply(body: str, here: str, depth: int) -> str:
    """Mark a page's body and append its card data."""
    marked, used = mark(body, here, depth)
    return marked + card_data(used, here, depth)


# ------------------------------------------------------------------------- style + script

CSS = r"""
/* the Learn pages' shared furniture (qccd/site/learn.py) */
.story{display:flex;gap:0;margin:4px 0 26px;padding:0;border:1px solid var(--line);border-radius:12px;overflow:hidden;
 background:var(--card);font-family:var(--sans)}
.story a{flex:1 1 0;display:flex;gap:9px;align-items:center;padding:9px 12px 9px 10px;color:var(--ink2);text-decoration:none;
 border-right:1px solid var(--line);min-width:0;position:relative}
.story a:last-child{border-right:0} .story a:hover{background:#fff;text-decoration:none;color:var(--ink)}
.story a .n{flex:0 0 22px;width:22px;height:22px;border-radius:11px;background:#e4e2db;color:#56545e;font-size:11.5px;font-weight:700;
 display:flex;align-items:center;justify-content:center}
.story a .w{display:flex;flex-direction:column;min-width:0;line-height:1.25}
.story a b{font-size:13px;font-weight:600;color:inherit} .story a small{font-size:11px;color:var(--ink3);line-height:1.2}
.story a[aria-current="page"]{background:#eef3fb;color:#1c2a4a} .story a[aria-current="page"] .n{background:#2a78d6;color:#fff}
.story a[aria-current="page"] small{color:#3b5b8c}
@media (max-width:900px){.story{flex-wrap:wrap}.story a{flex:1 1 45%;border-bottom:1px solid var(--line)}.story a small{display:none}}
.sum{background:#f3f6fb;border:1px solid #d6e2f4;border-left:4px solid #2a78d6;border-radius:12px;padding:14px 20px 12px;margin:18px 0 22px;
 font-family:var(--sans);font-size:14.5px;line-height:1.55;max-width:100ch;color:#1f2b44}
.sum .sumh,.recap .sumh{margin:0 0 6px;font-size:11.5px;letter-spacing:.12em;text-transform:uppercase;font-weight:700;color:#2a78d6}
.sum p{margin:0 0 6px;color:#1f2b44} .sum ul{margin:4px 0 2px;padding-left:20px} .sum li{margin:0 0 5px;color:#1f2b44}
.sum.sec{padding:10px 16px;margin:10px 0 14px;font-size:14px;border-radius:10px} .sum.sec b{color:#2a78d6;font-size:11.5px;letter-spacing:.1em;text-transform:uppercase;margin-right:8px}
.ask{display:grid;grid-template-columns:46px 1fr;gap:4px 14px;align-items:start;margin:20px 0 20px;max-width:100ch}
.ask:before{content:"?";grid-column:1;grid-row:1 / span 2;width:46px;height:46px;border-radius:23px;background:#1c2a4a;color:#fff;font:700 26px/46px var(--serif);text-align:center}
.ask .q,.ask .a{grid-column:2;max-width:none}
.ask .q{margin:0 0 4px;font-size:19px;line-height:1.45;color:#1a2540;font-weight:600;font-family:var(--serif)}
.ask .a{margin:0;color:var(--ink2);font-size:15.5px}
.bridge{font-family:var(--serif);font-style:italic;color:#3b4660;border-left:3px solid #e4e2db;padding:4px 0 4px 16px;margin:26px 0 8px;max-width:80ch;font-size:16.5px}
.recap{background:var(--card);border:1px solid var(--line);border-top:4px solid #1c2a4a;border-radius:12px;padding:18px 22px 14px;margin:40px 0 10px;font-family:var(--sans);font-size:14.5px;line-height:1.55}
.recap .sumh{color:#1c2a4a} .recap ol{margin:4px 0 10px;padding-left:22px} .recap li{margin:0 0 7px;color:var(--ink)} .recap .more{color:var(--ink2);margin:0 0 10px}
.recap .ways{display:flex;flex-wrap:wrap;gap:10px;margin-top:8px}
.recap .go{flex:1 1 260px;display:flex;flex-direction:column;gap:2px;padding:10px 14px;border:1px solid #d6e2f4;border-radius:10px;background:#f3f6fb;color:#1c2a4a;text-decoration:none}
.recap .go:hover{border-color:#2a78d6;text-decoration:none} .recap .go small{font-size:11px;letter-spacing:.1em;text-transform:uppercase;color:#2a78d6;font-weight:700}
.recap .go b{font-weight:600;font-size:14px} .recap .go.back{background:#fff;border-color:var(--line)} .recap .go.back small{color:var(--ink3)}
.recap .go.back:hover{border-color:#8a8892}
/* the glossary: a word with a dotted underline explains itself on hover and goes to its definition on click */
a.gl{color:inherit;text-decoration:none;border-bottom:1px dotted #7f9dcf;cursor:help;background:none}
a.gl:hover,a.gl:focus{border-bottom:1px solid #2a78d6;color:#1c2a4a;text-decoration:none;outline:none;background:#eef3fb;border-radius:2px}
#glcard{position:fixed;z-index:90;width:min(360px,calc(100vw - 32px));background:#fff;border:1px solid #cfceca;border-radius:10px;
 box-shadow:0 8px 28px rgba(0,0,0,.14);padding:12px 14px 10px;font:13.5px/1.5 var(--sans,ui-sans-serif,system-ui,sans-serif);color:#15141a;display:none}
#glcard[data-open="1"]{display:block} #glcard b{display:block;font-size:14px;color:#1c2a4a;margin:0 0 3px}
#glcard p{margin:0 0 8px;color:#2a2a32} #glcard a{display:inline-block;font-size:12.5px;color:#1f5bb5;text-decoration:none;font-weight:600}
#glcard a:hover{text-decoration:underline} #glcard small{color:#8a8892;font-size:11.5px;margin-left:6px}
/* a runbox is a poster until it is pressed */
.runbox .poster{position:relative;height:300px;border:1px solid var(--line);border-radius:8px;background:#fff;overflow:hidden;cursor:pointer;
 display:flex;align-items:center;justify-content:center;font-family:var(--sans)}
.runbox.tall .poster{height:360px} .gr .runbox.tall .poster{height:330px} .verb .runbox .poster{height:420px}
.fig .runbox .poster,.fig .runbox iframe.live{height:420px}   /* the physics runs: room for the stage and its labels */
.runbox .poster .pic{position:absolute;inset:0;display:flex;align-items:center;justify-content:center;padding:14px 16px 44px}
.runbox .poster .pic svg{max-width:100%;max-height:100%;width:auto;height:auto;opacity:.92}
.runbox .poster .pp{position:relative;z-index:1;display:inline-flex;align-items:center;gap:10px;padding:10px 22px 10px 16px;border-radius:26px;
 background:#1c2a4a;color:#fff;font-weight:700;font-size:15px;box-shadow:0 6px 20px rgba(28,42,74,.35);transition:transform .12s}
.runbox .poster .pp i{width:0;height:0;border-left:13px solid #fff;border-top:8px solid transparent;border-bottom:8px solid transparent;margin-left:2px}
.runbox .poster:hover .pp,.runbox .poster:focus .pp{transform:scale(1.06);background:#2a78d6}
.runbox .poster:focus{outline:2px solid #bcd4f2;outline-offset:1px}
.runbox .poster .pw{position:absolute;left:0;right:0;bottom:0;padding:7px 12px;font-size:12px;color:#56545e;background:linear-gradient(180deg,rgba(255,255,255,0),#fff 40%);text-align:center}
.runbox .poster.loading .pp{background:#8a8892;transform:none} .runbox .poster.loading .pp:after{content:"\2026"}
.runbox iframe.live{height:300px} .runbox.tall iframe.live{height:360px}
@media (max-width:760px){.ask{grid-template-columns:36px 1fr}.ask:before{width:36px;height:36px;border-radius:18px;font-size:20px;line-height:36px}.ask .q{font-size:17px}}
"""

JS = r"""
<script>
(function(){
  if(typeof document === 'undefined' || !document.querySelector) return;
  // ---- the glossary card -----------------------------------------------------------------
  var blob = document.getElementById('glossary'), DATA = {};
  try { DATA = blob ? JSON.parse(blob.textContent) : {}; } catch(e){ DATA = {}; }
  var card = null, timer = null, over = null, shownFor = null;
  function ensure(){
    if(card) return card;
    card = document.createElement('div'); card.id = 'glcard'; card.setAttribute('role', 'tooltip');
    card.addEventListener('mouseenter', function(){ over = 'card'; if(timer){ clearTimeout(timer); timer = null; } });
    card.addEventListener('mouseleave', function(){ over = null; later(); });
    document.body.appendChild(card);
    return card;
  }
  function place(a){
    var r = a.getBoundingClientRect(), c = ensure(), w = Math.min(360, window.innerWidth - 32);
    var x = Math.max(16, Math.min(r.left, window.innerWidth - w - 16));
    c.style.left = x + 'px'; c.style.top = '0px'; c.style.width = w + 'px';
    c.setAttribute('data-open', '1');
    var h = c.offsetHeight, below = r.bottom + 8, above = r.top - 8 - h;
    c.style.top = ((below + h <= window.innerHeight - 8 || above < 8) ? below : above) + 'px';
  }
  function show(a){
    var g = DATA[a.getAttribute('data-gl')]; if(!g) return;
    var c = ensure();
    c.textContent = '';
    var b = document.createElement('b'); b.textContent = g.t; c.appendChild(b);
    var p = document.createElement('p'); p.textContent = g.d; c.appendChild(p);
    var l = document.createElement('a'); l.href = g.u; l.textContent = 'Go to the definition →'; c.appendChild(l);
    if(g.w){ var s = document.createElement('small'); s.textContent = g.w; c.appendChild(s); }
    shownFor = a; place(a);
  }
  function hide(){ if(card) card.setAttribute('data-open', '0'); shownFor = null; }
  function later(){ if(timer) clearTimeout(timer); timer = setTimeout(function(){ if(!over) hide(); }, 220); }
  var terms = document.querySelectorAll('a.gl');
  Array.prototype.forEach.call(terms, function(a){
    a.addEventListener('mouseenter', function(){ over = a; if(timer){ clearTimeout(timer); timer = null; } setTimeout(function(){ if(over === a) show(a); }, 90); });
    a.addEventListener('mouseleave', function(){ if(over === a) over = null; later(); });
    a.addEventListener('focus', function(){ show(a); });
    a.addEventListener('blur', function(){ later(); });
    // on a touch screen the first tap explains and the second one goes
    a.addEventListener('touchend', function(ev){ if(shownFor !== a){ ev.preventDefault(); over = a; show(a); } }, { passive: false });
  });
  document.addEventListener('keydown', function(ev){ if(ev.key === 'Escape') hide(); });
  document.addEventListener('scroll', function(){ if(shownFor) place(shownFor); }, { passive: true });
  // ---- runboxes: a poster until pressed, then the Studio page, playing ---------------------
  Array.prototype.forEach.call(document.querySelectorAll('.runbox[data-src]'), function(box){
    var poster = box.querySelector('.poster'); if(!poster) return;
    function go(){
      if(box.getAttribute('data-live')) return;
      box.setAttribute('data-live', '1'); poster.classList.add('loading');
      var f = document.createElement('iframe');
      f.className = 'live'; f.title = box.getAttribute('data-title') || 'the programme, on its page';
      f.src = box.getAttribute('data-src') + '#embed&step=1&go';
      var gone = function(){ if(poster.parentNode) poster.parentNode.removeChild(poster); };
      f.addEventListener('load', gone);
      setTimeout(gone, 6000);            // a page whose load event never comes still takes the poster away
      box.appendChild(f);
      try { f.focus(); } catch(e){}
    }
    poster.addEventListener('click', go);
    poster.addEventListener('keydown', function(ev){ if(ev.key === 'Enter' || ev.key === ' '){ ev.preventDefault(); go(); } });
  });
})();
</script>
"""
