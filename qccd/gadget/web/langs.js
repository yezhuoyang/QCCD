// THE LANGUAGES OF THE FULL STACK, as the page colours and explains them.
//
// Every program the page shows -- FormalRV's .prog, .qppm and .qec, the gadget program
// (.alg) the town runs, and the TSIR modules and leaf programs under it -- is drawn in its own
// column, one line a row, with its words coloured by what they do and a note on each word a
// reader can hover.  The colours are shared across the layers, so the same kind of step has the
// same colour in every column: a preparation is cyan in the .qppm, the .qec, the .alg and the
// TSIR alike, a Pauli-product measurement indigo, a decode violet, a gate green, a frame update
// amber.  The statement words are drawn as the studio draws a hardware instruction: a coloured
// tag (qccd/viz/render.py, `.ln .op`), and the TSIR verbs in the studio's own colours.
//
// `paint(lang, text)` is the only entry: one line of source in, its HTML out (escaped, every
// word that has a note carrying `data-w`); `note(lang, word)` is the note.  Nothing here reads
// the page: the tables are data, so a test can run them in Node.
(function (root) {
  "use strict";

  // kinds: a tag ("t-<kind>") for a statement word, coloured text for the rest
  const TAG = { prep: 1, ppm: 1, meas: 1, gate: 1, decode: 1, frame: 1, move: 1, cool: 1, reset: 1, run: 1 };

  // -- shared notes ---------------------------------------------------------------------
  const SPEC = {
    spec: ["kw", "Specification", "What the program or gadget above must do: the conditions it needs, the state it starts from and ends in, its outcomes and its resource counts. FormalRV proves the body meets it."],
    requires: ["kw", "Requires", "The conditions on the parameters under which the specification holds."],
    assumes: ["kw", "Assumes", "The input state the specification starts from, written between {{ }}."],
    ensures: ["kw", "Ensures", "The output state the body produces from the assumed input."],
    outcomes: ["kw", "Outcomes", "What the measured bits satisfy: none, or a probability bound such as Pr[ … ] ≥ …"],
    resource: ["kw", "Resource", "The resources the specification promises: Toffoli, T, Clifford, rotation and measurement counts, depth, width, operations and qubits."],
    define: ["kw", "Definition", "A named function of the parameters, used in sizes, conditions and resource counts. The compiler evaluates it; the machine never does."],
    gadget: ["kw", "Gadget", "A sub-program with its own parameters, called like a gate. FormalRV proves each gadget against its own spec, once, for every call."],
    program: ["kw", "Program", "The program: its name, its registers and its body, indented below."],
    params: ["kw", "Parameters", "The program's parameters. Sizes, loop bounds and constants are written in them; the run fixes their values."],
    witness: ["kw", "Witness", "The parameter values this run is compiled at: the program is generic, the run is one instance of it."],
    code: ["kw", "Code", "The error-correcting code every logical qubit is encoded in: here the surface code at distance d."],
    map: ["kw", "Qubit map", "Where a call's own qubit indices land in the caller's: `@ id()` passes them through, `@ shift(b)` adds b."],
    use: ["kw", "Use", "Brings in the gadgets and definitions of another .prog file."],
    for: ["kw", "Loop", "A loop over a range the parameters fix (for 0 ≤ i < n). The compiler unrolls it."],
    headfirst: ["kw", "Head-first loop", "An ascending loop that FormalRV sequences head first: its own loop form, the same indices, lo to hi."],
    if: ["kw", "Condition", "A choice made from the parameters, by the compiler: in a definition, or `if not cst = 0:` around a block."],
    then: ["kw", "then", "The value when the condition holds."],
    else: ["kw", "else", "The value when it does not."],
    not: ["kw", "not", "Negation."],
    when: ["kw", "Classical condition", "The indented body runs only when the measured bit (or record) it names is 1: a classically controlled block."],
    ctrl: ["kw", "Controlled block", "The indented body runs controlled on the named qubit: every gate in it gains that qubit as a control."],
    Pr: ["kw", "Probability", "Pr[ e ]: the probability that the measured bits satisfy e."],
    dim: ["name", "dim", "The number of logical qubits the machine offers: every register must fit below it."],
    pi: ["name", "π", "The constant π."],
    sqrt: ["name", "sqrt", "Square root."],
    ilog2: ["name", "ilog2", "Integer base-2 logarithm."],
    none: ["kw", "none", "Nothing: no outcome a caller reads."],
    toffoli: ["fld", "Toffoli count", "How many Toffoli gates the body uses."],
    t: ["fld", "T count", "How many T gates the body uses."],
    clifford: ["fld", "Clifford count", "How many Clifford gates the body uses."],
    rot: ["fld", "Rotation count", "How many arbitrary-angle rotations the body uses."],
    meas: ["fld", "Measurement count", "How many measurements the body makes."],
    depth: ["fld", "Depth", "The body's circuit depth."],
    toffdepth: ["fld", "Toffoli depth", "The body's depth in Toffoli gates."],
    width: ["fld", "Width", "How many qubits the body works on at once."],
    ops: ["fld", "Operations", "How many operations the body has in all."],
    qubits: ["fld", "Qubits", "How many qubits the body touches."],
  };
  // fields of the resource record are words only after `{` or `,` and before `:`
  const FIELDS = new Set(["toffoli", "t", "clifford", "rot", "meas", "depth", "toffdepth", "width", "ops", "qubits"]);

  const PAULI = {
    X: ["px", "Pauli X", "The X factor of a Pauli product: on this qubit (or block) the measurement, or the correction, is X."],
    Y: ["py", "Pauli Y", "The Y factor of a Pauli product."],
    Z: ["pz", "Pauli Z", "The Z factor of a Pauli product: on this qubit (or block) the measurement, or the correction, is Z."],
  };
  const SYM = {
    "∀": "for all: the statement holds for every value in the range",
    "∃": "there exists",
    "∧": "and", "∨": "or", "→": "implies", "¬": "not",
    "≤": "at most", "≥": "at least", "≠": "not equal",
    "⊗": "tensor product: the named qubits' states side by side",
    "⊕": "exclusive or: addition mod 2",
    "Σ": "sum over a range (Σ i < n . e)",
    "Π": "product over a range",
    "↦": "writes the outcome to",
    "->": "the result goes to",
    ":=": "is defined as",
    "@": "placed at: a call's qubit map, or the place an instruction is pinned to",
    "{{": "a state assertion begins", "}}": "a state assertion ends",
  };
  const STATES = {
    "|Y+>": "|Y+⟩, the +1 eigenstate of Pauli Y: the resource a CNOT or an S takes by measurement.",
    "|+>": "|+⟩, the +1 eigenstate of Pauli X.",
    "|0>": "|0⟩, the computational-basis zero state.",
    "|T>": "|T⟩ = T|+⟩, the magic state a T gate consumes; the factory distils it.",
  };

  const PROG = Object.assign({}, SPEC, {
    measure: ["meas", "Measure", "Measures a qubit in the Z basis; `↦ c[i]` writes the bit to classical bit i."],
    H: ["gate", "Hadamard", "H on one qubit: swaps the X and Z bases."],
    X: ["gate", "Pauli X", "X on one qubit: a NOT."],
    Y: ["gate", "Pauli Y", "Y on one qubit."],
    Z: ["gate", "Pauli Z", "Z on one qubit: a phase flip."],
    S: ["gate", "S", "The phase gate √Z."],
    Sdg: ["gate", "S†", "The inverse phase gate."],
    T: ["gate", "T", "The π/8 gate √S: not Clifford, so it costs a magic state."],
    Tdg: ["gate", "T†", "The inverse of T."],
    CX: ["gate", "CNOT", "CX(control, target): flips the target when the control is 1."],
    CZ: ["gate", "CZ", "Controlled Z on two qubits."],
    CCX: ["gate", "Toffoli", "CCX(a, b, target): flips the target when both controls are 1."],
    Rz: ["gate", "Z rotation", "Rz(θ) on one qubit: a rotation about Z by an angle, not Clifford in general."],
    SWAP: ["gate", "SWAP", "Exchanges two qubits."],
  });

  const QPPM = Object.assign({}, SPEC, PAULI, {
    prepare: ["prep", "Prepare", "Prepares a fresh ancilla in the named state (|Y+⟩, |+⟩, |0⟩, …) and names it (`-> anc[k]`)."],
    measure: ["ppm", "Pauli-product measurement", "Measures the product of the listed Paulis (Z q[0] X q[1] Z anc[0]) and writes its ±1 outcome to m[k]. Every gate of the program becomes such measurements."],
    frame: ["frame", "Pauli frame", "A Pauli correction kept in software: no qubit is touched; it is tracked and folded into later outcomes."],
    ppmrot: ["gate", "Rotation by measurement", "ppmrot(φ) P = exp(−iφP): a rotation of the Pauli product P, done by repeat-until-success with magic states."],
    with: ["kw", "with", "The resources the rotation may use: its magic states, ancillas and bits."],
    magic: ["kw", "magic", "Magic states: the T states a rotation's synthesis consumes."],
    states: ["kw", "states", "(magic states)"],
    anc: ["name", "anc", "The ancilla qubits the PPM program adds to the logical ones."],
    q: ["name", "q", "The program's logical qubits."],
    m: ["name", "m", "Measurement outcomes, in order."],
    c: ["name", "c", "The program's classical bits."],
  });

  const QEC = Object.assign({}, SPEC, PAULI, {
    codes: ["kw", "Codes", "The codes this program uses: each one's stabilizers and logical operators, and the merged codes lattice surgery makes of them."],
    stab: ["kw", "Stabilizer", "A stabilizer generator of the code: a Pauli product every code word is +1 for."],
    logX: ["kw", "Logical X̄", "The code's logical X: a Pauli product that flips the encoded qubit while commuting with every stabilizer."],
    logZ: ["kw", "Logical Z̄", "The code's logical Z."],
    canonical_product: ["kw", "Merged code", "The code formed when a product of logical Paulis of several blocks is measured: lattice surgery's merged patch."],
    alloc: ["prep", "Allocate", "Takes code blocks or ancilla qubits from a register range [lo,hi)."],
    prepare: ["prep", "Prepare", "Prepares the named blocks or ancillas in a state (|+⟩, |Y+⟩, …)."],
    magic: ["prep", "Magic state", "`N magic |T> -> LogicalAncilla[j]`: a logical T state from the factory, delivered into that block."],
    merge: ["ppm", "Merge (lattice surgery)", "Measures a product of logical Paulis by merging the blocks into one patch through ancillas; `checks` names the new checks, `proved` that FormalRV proved the factors commute."],
    split: ["ppm", "Split", "Ends a merge: the merged patch is split back into its blocks."],
    extract: ["meas", "Syndrome extraction", "Runs r rounds of stabilizer measurement on a block with the named ancillas, writing the syndrome bits."],
    decode: ["decode", "Decode", "Hands r rounds of syndrome (or record) bits to the decoder, which infers the errors and the frame update."],
    frame: ["frame", "Pauli frame", "A logical Pauli correction kept in software; `frame on c[…] P` applies it when those record bits say so."],
    skip: ["kw", "skip", "An empty statement: nothing happens here."],
    with: ["kw", "with", "The ancilla qubits a merge measures through."],
    checks: ["kw", "checks", "The ancillas that check the merged patch's new stabilizers."],
    commuting: ["kw", "commuting", "The factors commute, so their product can be measured."],
    proved: ["kw", "proved", "FormalRV proved it: the factors commute."],
    on: ["kw", "on", "The record bits the operation writes, or reads."],
    using: ["kw", "using", "The ancilla qubits the extraction uses."],
    rounds: ["kw", "rounds", "How many rounds of syndrome measurement."],
    LogicData: ["name", "LogicData", "The program's logical data blocks."],
    LogicalAncilla: ["name", "LogicalAncilla", "Logical ancilla blocks: the |Y+⟩ and magic-state resources."],
    ancilla: ["name", "ancilla", "Physical ancilla qubits."],
    syndrome: ["name", "syndrome", "Syndrome bits, for the decoder."],
    c: ["name", "c", "Classical records."],
    Surface: ["name", "Surface", "The surface code."],
    rv_database: ["name", "rv_database", "Where this call's data blocks start: the caller's offset."],
    rv_ancillabase: ["name", "rv_ancillabase", "Where this call's ancillas start."],
    rv_logicalancillabase: ["name", "rv_logicalancillabase", "Where this call's logical ancillas start."],
    rv_synbase: ["name", "rv_synbase", "Where this call's syndrome bits start."],
    rv_recbase: ["name", "rv_recbase", "Where this call's records start."],
  });
  delete QEC.measure;

  // FormalRV's system program (.sysprog): each QEC operation as the Clifford circuit it is, with
  // its timing -- `@1643000 ns  CNOT ancilla[abase + 1], data[dbase]` -- the statement word after
  // the time, or after `->` for what is done with bits
  const SYS = Object.assign({}, PAULI, {
    machine: ["kw", "Machine profile", "The machine FormalRV's VM estimates against: its qubits, decoders, factories, bandwidths and latencies."],
    resource: ["kw", "Resource", "What this program or gadget uses, or what a contract allows it: qubits, gates, measurements, routes, decodes, runtime."],
    spec: ["kw", "System contract", "What a system gadget may use: qubits, runtime, gates, factories, decoders and bandwidth."],
    gadget: ["kw", "System gadget", "One QEC operation as a Clifford circuit with its timing; `spans` says how long it takes."],
    spans: ["kw", "spans", "How long the gadget takes, start to end."],
    program: ["kw", "Program", "The whole system program: its registers, then every operation at its time."],
    for: ["kw", "Loop", "A loop over a range; `every T` starts its iterations T apart."],
    every: ["kw", "every", "The time between the starts of two iterations."],
    call: ["kw", "Call", "Runs a system gadget here, with the offsets of its data, ancillas and bits."],
    patch: ["kw", "patch", "A whole code patch, not single qubits."],
    with: ["kw", "with", "The workspace qubits the request may use."],
    request_ancilla: ["prep", "Request ancillas", "Takes ancilla qubits (or a whole patch) for an operation."],
    release_ancilla: ["reset", "Release ancillas", "Gives ancilla qubits back once the operation is done."],
    request_magic: ["prep", "Request a magic state", "Asks a factory for a state (`state=`) in these qubits, with the named workspace."],
    route: ["move", "Route", "Brings qubits together so a two-qubit gate can act on them: on QCCD, ion transport."],
    H: ["gate", "Hadamard", "H on each named qubit."],
    S: ["gate", "S", "The phase gate √Z."],
    CNOT: ["gate", "CNOT", "CNOT(control, target)."],
    CX: ["gate", "CNOT", "CX(control, target)."],
    CZ: ["gate", "CZ", "Controlled Z."],
    measure: ["meas", "Measure", "Measures qubits in the named basis (`basis=`); the bits go to the register after ->."],
    decode_syndrome: ["decode", "Decode", "Hands syndrome (or record) bits of r rounds to a decoder."],
    pauli_frame_update: ["frame", "Pauli frame update", "Updates the Pauli frame from the named bits: no qubit is touched."],
    basis: ["kw", "basis", "The basis the qubits are measured in."],
    count: ["kw", "count", "How many qubits."],
    rounds: ["kw", "rounds", "How many syndrome rounds the bits cover."],
    state: ["kw", "state", "The state the factory is asked for."],
    data: ["name", "data", "The data qubits of the code blocks."],
    ancilla: ["name", "ancilla", "Ancilla qubits."],
    syndrome: ["name", "syndrome", "Syndrome bits, for the decoder."],
    c: ["name", "c", "Classical records."],
  });

  const ALG = {
    title: ["kw", "Title", "The program's title, shown at the top of the page."],
    floorplan: ["kw", "Floorplan", "Names the machine the program runs on."],
    include: ["kw", "Include", "Brings in a library of gates (\"standard\": T, T†, …)."],
    gate: ["kw", "Gate definition", "Defines a gate from instructions: its parameters are blocks, its body is indented. A call expands to the body with fresh local names."],
    implements: ["kw", "implements", "The gate the body is meant to be; the contract checker proves it, branch by branch."],
    if: ["kw", "Guard", "The instruction runs only when the condition holds. The place is reserved either way, so the schedule stays fixed: a pre-compiled branch."],
    prep: ["prep", "Prepare a block", "A fresh d = 3 surface-code block in |0̄⟩ (Z), |+̄⟩ (X) or |Ȳ⟩ (Y, by injection), or a rotation state, made at a prep place."],
    inject: ["prep", "Inject a magic state", "A block holding the factory's magic state, delivered by the injection place."],
    se: ["meas", "Syndrome rounds", "Syndrome rounds on a block (1 or 3), at a clinic."],
    cx: ["gate", "Transversal CNOT", "A CNOT between two blocks, ion by ion, at a workshop; the first block controls."],
    zz: ["ppm", "Z̄Z̄ by lattice surgery", "Measures Z̄Z̄ of two blocks at a bridge; the outcome is named after ->."],
    xx: ["ppm", "X̄X̄ by lattice surgery", "Measures X̄X̄ of two blocks at a bridge."],
    ppm: ["ppm", "Pauli-product measurement", "One X̄, Ȳ or Z̄ from each of 1–4 blocks, measured through a probe ion at a bench; the outcome is named after ->."],
    read: ["meas", "Readout", "Destructive readout of a block in Z or X, at a post office; the outcome is named after ->."],
    s: ["gate", "S̄", "Logical S by consuming a |Ȳ⟩ block."],
    sx: ["gate", "√X̄", "Logical √X by consuming a |Ȳ⟩ block, through X̄X̄ surgery."],
    x: ["frame", "Logical X̄ (software)", "A logical Pauli kept in the classical memory: no ion is touched."],
    y: ["frame", "Logical Ȳ (software)", "A logical Pauli kept in the classical memory: no ion is touched."],
    z: ["frame", "Logical Z̄ (software)", "A logical Pauli kept in the classical memory: no ion is touched."],
    store: ["move", "Store", "Rests the block in a logical zone."],
    decode: ["decode", "Decode", "Sends the syndromes the named blocks have left to the decoder. A guard may read an outcome only once its window is decoded."],
  };
  Object.assign(ALG, PAULI);
  // in a gadget program the statement words are lower case and stand at the head of a line
  // (or after a guard's colon); `x q0` is an instruction, an `x` elsewhere is not
  const ALG_HEAD = new Set(["prep", "inject", "se", "cx", "zz", "xx", "ppm", "read", "s", "sx", "x", "y", "z", "store", "decode"]);

  const TSIR = {
    tsir: ["kw", "TSIR", "A header: a modules file (`tsir modules 1`) or a leaf program's name (`tsir master.op+cooling`)."],
    modules: ["kw", "modules", "The run as modules: the architecture, the program, a file per gadget and a file per leaf."],
    architecture: ["kw", "Architecture", "The floor this run is laid out on, instanced: its places, roads and wires."],
    place: ["kw", "Place", "An instance of a place master at a position on the floor."],
    road: ["kw", "Road", "A channel ions are carried along; its gadget is chan<L>.carry, the conveyor every road is priced from."],
    wire: ["kw", "Wire", "A classical wire from one place's port to another, with its latency in µs."],
    let: ["kw", "let", "A named value of this module."],
    program: ["kw", "Program", "The program's own body: its rows."],
    gadget: ["kw", "Gadget", "A composite gadget: one gate in one context, written once and called."],
    about: ["kw", "About", "What this gadget is: its name, title and source language."],
    uses: ["kw", "Uses", "The gadget modules this program calls."],
    ins: ["kw", "Logical instruction", "A line of the gadget program; every row under it belongs to it."],
    call: ["kw", "Call", "A composite gadget called here, with the blocks it is given."],
    leaf: ["kw", "Leaf gadget", "One op of one place master, and its TSIR: every hardware instruction below is a line `id verb …`."],
    source: ["kw", "Source", "The protocol this leaf compiles, line by line."],
    arch: ["kw", "arch", "The architecture the leaf program is written for."],
    id_seq: ["kw", "id_seq", "The highest instruction id handed out: ids are identities, not positions."],
    meta: ["kw", "meta", "The program's own metadata (JSON)."],
    metrics: ["kw", "metrics", "The totals the program claims (JSON); the replay recomputes them."],
    block: ["kw", "block", "A logical block of the run and its ions."],
    window: ["kw", "window", "A syndrome window: bits a place measured, waiting for a decode."],
    frame: ["kw", "frame", "A Pauli-frame update the decoder sent to the classical memory."],
    outcome: ["kw", "outcome", "A logical outcome and the window it is decoded from."],
    channel: ["kw", "channel", "A road or wire between two places."],
    order: ["kw", "order", "The order ions stand in."],
    control: ["kw", "control", "The machine's control planes."],
    run: ["run", "Run", "A leaf gadget runs at a place: `run id t0 t1 master.op @place ions`. Its hardware instructions are the lines of that leaf's TSIR."],
    carry: ["move", "Carry", "Ions carried along a road by its conveyor; departures and takes are relative to its start."],
    msg: ["decode", "Message", "Bits on a classical wire: syndromes to the decoder, frame updates to the memory."],
    hold: ["meas", "Hold", "A resting block's refresh rounds while it waits in a memory."],
    // the hardware instructions, in the studio's own colours (render.py, classColour)
    init: ["reset", "Init", "Places the ions in their starting sites; after `|` each ion's motional quanta n̄."],
    simd: ["move", "Transport (SIMD)", "One concurrent transport cycle: every listed ion moves at once; the word after it says how (rotate, dock, port, conveyor, …)."],
    gate_: ["gate", "Gate", "Laser pulses on ions at a gate zone: a pair a:b for a two-qubit gate, single ions for one-qubit gates."],
    measure: ["meas", "Measure", "Fluorescence readout of the listed ions."],
    reset: ["reset", "Reset", "Optical pumping of the listed ions back to |0⟩."],
    cool: ["cool", "Cool", "Sympathetic cooling of the listed ions; `cool` alone cools every ion."],
    barrier: ["kw", "Barrier", "Every instruction before it ends before any after it starts."],
    decode: ["decode", "Decode", "The decoder's job on the listed ions' syndromes."],
    // after `simd`: how the ions move; after `gate`: which gate
    rotate_cw: ["mv", "Rotate clockwise", "The ring's ions all move k hops clockwise: one rigid template, ~L ±k."],
    rotate_ccw: ["mv", "Rotate counter-clockwise", "The ring's ions all move k hops counter-clockwise."],
    dock: ["mv", "Dock", "An ion moves from the ring into a dock site, where gates are allowed."],
    undock: ["mv", "Undock", "An ion moves from a dock site back onto the ring."],
    port_in: ["mv", "Port in", "An ion comes in through a door port."],
    port_out: ["mv", "Port out", "An ion leaves through a door port."],
    conveyor: ["mv", "Conveyor", "One step of a road's conveyor: every ion on it moves one site."],
    shuttle: ["mv", "Shuttle", "An ion shuttled along a path of sites."],
    sort_merge: ["mv", "Merge", "Two chains merged into one well."],
    sort_split: ["mv", "Split", "A chain split in two."],
    CX: ["gn", "CNOT", "A two-qubit gate on the pair a:b."],
    MS: ["gn", "Mølmer–Sørensen", "The native two-qubit gate of trapped ions."],
    H: ["gn", "Hadamard", "One-qubit H."],
    R: ["gn", "R(θ, φ)", "A one-qubit rotation by a laser pulse."],
    VZ: ["gn", "Virtual Z", "A Z rotation done in software, by shifting later pulses' phases: it takes no time."],
  };
  const AFTER_VERB = new Set(["mv", "gn"]);
  // the words after `;` are an instruction's metadata
  const TSIR_META = {
    op: "the place op this instruction belongs to",
    round: "the syndrome round",
    factor: "the Pauli factor it serves",
    door: "the door the ions pass",
    kind: "what kind of move it is",
    hops: "how many hops the ring turns",
    call: "the call within the op",
    checks: "the checks this measurement contributes to",
    gid: "the instruction's global id",
  };

  const LANGS = {
    prog: { words: PROG, comment: /^(\/\/|--)/ },
    qppm: { words: QPPM, comment: /^(\/\/|--)/ },
    qec: { words: QEC, comment: /^(\/\/|--)/, anywhere: true },
    sys: { words: SYS, comment: /^(\/\/|--)/, anywhere: true,
           sym: { "@": "starts at this time, from the start of its gadget", "->": "the bits go to" } },
    alg: { words: ALG, comment: /^#/, head: ALG_HEAD },
    tsir: { words: TSIR, comment: /^\/\//, meta: TSIR_META },
  };

  const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  // one line, in tokens: a comment, a string, a ket, a number, a word, an operator, the rest
  const RX = /("(?:[^"\\]|\\.)*")|(\|[A-Za-z0-9+\-]{1,3}>|\|[^|⟩\n]{1,48}⟩)|(\b\d+(?:\.\d+)?(?:e[-+]?\d+)?\b)|([A-Za-z_][A-Za-z0-9_]*)|(->|:=|\{\{|\}\}|[∀∃∧∨→¬≤≥≠⊗⊕Σ↦Π@])/g;

  function span(cls, text, w) {
    return `<span class="${cls}"${w !== undefined ? ` data-w="${esc(w)}"` : ""}>${esc(text)}</span>`;
  }

  // the classes a word is drawn in
  function wordCls(kind, w) { return TAG[kind] ? `tg t-${kind}` : kind === "mv" ? `tk-mv mv-${w}` : `tk-${kind}`; }

  function paint(lang, text, opts) {
    const L = LANGS[lang];
    if (!L || !text) return esc(text || "");
    const trimmed = text.trimStart();
    if (L.comment.test(trimmed)) return span("tk-c", text);
    const calls = (opts && opts.calls) || null;
    // a trailing comment: `#` in a gadget program, `//` everywhere
    let body = text, tail = "";
    const cut = lang === "alg" ? text.indexOf("#") : text.indexOf("//");
    if (cut >= 0 && !/"[^"]*$/.test(text.slice(0, cut))) { body = text.slice(0, cut); tail = text.slice(cut); }
    // TSIR: the metadata after ` ; `
    let meta = "";
    if (lang === "tsir") {
      const k = body.indexOf(" ; ");
      if (k >= 0) { meta = body.slice(k); body = body.slice(0, k); }
    }
    let out = "", i = 0, m, first = true, afterNum = false;
    RX.lastIndex = 0;
    while ((m = RX.exec(body))) {
      if (m.index > i) out += esc(body.slice(i, m.index));
      const tok = m[0];
      const before = body.slice(0, m.index);
      if (m[1]) out += span("tk-s", tok);
      else if (m[2]) {
        const ascii = tok.endsWith(">");
        out += span("tk-st", tok, ascii && STATES[tok] ? "state:" + tok : "ket");
      } else if (m[3]) {
        out += span("tk-n", tok);
        // a leaf TSIR line starts with its instruction id: the verb follows it
        if (lang === "tsir" && first) afterNum = true;
        first = false;
      } else if (m[4]) {
        let w = tok, e = L.words[w];
        let head = first || (/:\s*$/.test(before) && /^\s*if\b/.test(body));
        if (lang === "tsir") {
          // a row's word stands first; a hardware instruction's verb after its id; a move's
          // class or a gate's name right after the verb.  Anywhere else a word is a name
          // (`chan9.carry` is a road's gadget, not a carry row).
          const afterVerb = /(^|\s)(simd|gate)\s+$/.test(before);
          if (afterNum && w === "gate") { e = L.words.gate_; w = "gate_"; }
          else if (first || afterNum) { if (e && AFTER_VERB.has(e[0])) e = null; }
          else if (afterVerb) { if (e && !AFTER_VERB.has(e[0])) e = null; }
          else e = null;
          head = true;
          afterNum = false;
        }
        if (e && L.head && L.head.has(w) && !head) e = null;
        // a resource field is a word before `:`, first in the record or after a comma
        if (e && e[0] === "fld" && !(FIELDS.has(w) && /^\s*:/.test(body.slice(m.index + tok.length)) &&
                                    /(^|[{,])\s*$/.test(before))) e = null;
        if (!e && lang === "qec" && /^merged\d+$/.test(w)) {
          out += span("tk-kw", tok, "merged");
        } else if (!e && calls && calls[w] !== undefined) {
          out += span("tk-call", tok, "call:" + w);
        } else if (e) {
          // a statement word that is not where a statement starts is drawn as text; in a
          // .qec a merge or a split stands after `=`, in a .sysprog every word after the
          // time, and they keep their tags
          const kind = TAG[e[0]] && !head && !L.anywhere ? "kw" : e[0];
          out += span(wordCls(kind, w), tok, w);
        } else out += esc(tok);
        first = false;
      } else if (m[5]) {
        out += span("tk-op", tok, "sym:" + tok);
        first = false;
      }
      i = m.index + tok.length;
    }
    if (i < body.length) out += esc(body.slice(i));
    if (meta) {
      // ` ; key=value key=value`: a value is a bare word, or JSON (a string may hold spaces)
      out += ` ${span("tk-c", ";")}` + meta.slice(2).replace(/([^\s=]+)=("(?:[^"\\]|\\.)*"|\S*)|(\s+)|(\S+)/g,
        (all, k, v, sp, other) => {
          if (sp !== undefined) return sp;
          if (other !== undefined) return esc(other);
          return span("tk-mk", k, L.meta[k] ? "meta:" + k : undefined) + "=" + span("tk-val", v);
        });
    }
    if (tail) out += span("tk-c", tail);
    return out;
  }

  // the note on a word, as [title, text], or null
  function note(lang, w, calls) {
    const L = LANGS[lang];
    if (!L || !w) return null;
    if (w.startsWith("sym:")) {
      const s = (L.sym || {})[w.slice(4)] || SYM[w.slice(4)];
      return s ? [w.slice(4), s] : null;
    }
    if (w.startsWith("state:")) return [w.slice(6), STATES[w.slice(6)]];
    if (w === "ket") return ["A basis state", "|v⟩_q[i]: the named qubit (or range of qubits) of register q holds the basis state v."];
    if (w === "merged") return ["Merged code", "A code declared under `codes:`: the patch lattice surgery makes when it measures a product of logical Paulis of that many blocks."];
    if (w.startsWith("meta:")) return [w.slice(5), L.meta[w.slice(5)]];
    if (w.startsWith("call:")) {
      const name = w.slice(5), at = calls && calls[name];
      return lang === "alg"
        ? [name, `A call of the gate ${name}, defined in this program${at ? " at line " + at : ""}: it expands to that gate's body, with fresh local names.`]
        : [name, `A call of the gadget ${name}${at ? ", defined at " + at : ""}: FormalRV proved it once against its own spec, for every call.`];
    }
    const e = L.words[w];
    return e ? [e[1], e[2]] : null;
  }

  // what a program defines and calls by name: a gadget program's gates (`gate NAME …:`),
  // FormalRV's gadgets (`gadget NAME…`), as name -> where (`where(i)`, default the line)
  function defined(lang, lines, where) {
    const rx = lang === "alg" ? /^\s*gate\s+([A-Za-z_]\w*)/ : /^\s*gadget\s+([A-Za-z_]\w*)/;
    const out = {};
    lines.forEach((s, i) => {
      const m = rx.exec(s);
      if (m && out[m[1]] === undefined) out[m[1]] = where ? where(i + 1) : i + 1;
    });
    return out;
  }

  const API = { paint, note, defined, LANGS, esc };
  if (typeof module !== "undefined" && module.exports) module.exports = API;
  else root.QCCD_LANGS = API;
})(typeof window !== "undefined" ? window : this);
