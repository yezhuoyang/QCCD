import numpy as np
import stim
from qldpc.objects import Pauli, PauliXZ
import igraph as ig

# Builds the color code SM circuit described in arXiv:2603.28852 which
# avoids distance-reducing hook errors.

# The code for color code construction is adapted from: 
#   https://github.com/Classiq/classiq-library/blob/main/algorithms/error_correction/syndrome_extraction_optimization/color_code_stim_local/color_code.py
# The code for color code SM circuit construction is adapated from:
#   https://github.com/Classiq/classiq-library/blob/main/algorithms/error_correction/syndrome_extraction_optimization/benchmark_circuits.py

class ColorCode:
    d: int
    rounds: int

    def __init__(
        self,
        *,
        d: int,
        rounds: int,
    ):
        """
        Class for constructing a color code circuit

        Parameters
        ----------
        d : int >= 3
            Code distance.

        rounds : int >= 1
            Number of syndrome extraction rounds.
        """
        assert d > 1 and rounds >= 1
        self.d = d

        assert d % 2 == 1

        self.tanner_graph = ig.Graph()
        # Various qubit groups predefined for efficiency
        self.qubit_groups = {}

        self._create_tanner_graph()

        self.data_qubit_ids = list(self.qubit_groups["data"]["qid"])
        self.num_qubits = len(self.qubit_groups["data"])
        self.num_checks_x = len(self.qubit_groups["anc_Z"])
        self.num_checks_z = len(self.qubit_groups["anc_Z"])
        self.observable = np.zeros(self.num_qubits, dtype=np.uint8)
        self.observable[[dq["qid"] for dq in self.qubit_groups["data"] if dq["obs"]]] = 1
        

    def _create_tanner_graph(self) -> None:
        """Construct the Tanner graph for the chosen circuit type."""
        tanner_graph = self.tanner_graph

        d = self.d
        assert d % 2 == 1

        L = round(3 * (d - 1) / 2)
        for y in range(L + 1):
            if y % 3 == 0:
                anc_qubit_color = "g"
                anc_qubit_pos = 2
            elif y % 3 == 1:
                anc_qubit_color = "b"
                anc_qubit_pos = 0
            else:
                anc_qubit_color = "r"
                anc_qubit_pos = 1

            for x in range(2 * y, 4 * L - 2 * y + 1, 4):
                boundary = []
                if y == 0:
                    boundary.append("r")
                if x == 2 * y:
                    boundary.append("g")
                if x == 4 * L - 2 * y:
                    boundary.append("b")
                boundary = "".join(boundary)
                if not boundary:
                    boundary = None

                
                obs = boundary in ["r", "rg", "rb"]

                if round((x / 2 - y) / 2) % 3 != anc_qubit_pos:
                    tanner_graph.add_vertex(
                        name=f"{x}-{y}",
                        x=x,
                        y=y,
                        qid=tanner_graph.vcount(),
                        pauli=None,
                        color=None,
                        obs=obs,
                        boundary=boundary,
                    )
                else:
                    for pauli in ["Z", "X"]:
                        tanner_graph.add_vertex(
                            name=f"{x}-{y}-{pauli}",
                            x=x,
                            y=y,
                            qid=tanner_graph.vcount(),
                            pauli=pauli,
                            color=anc_qubit_color,
                            obs=False,
                            boundary=boundary,
                        )

        # Update qubit_groups
        data_qubits = tanner_graph.vs.select(pauli=None)
        anc_qubits = tanner_graph.vs.select(pauli_ne=None)
        anc_Z_qubits = anc_qubits.select(pauli="Z")
        anc_X_qubits = anc_qubits.select(pauli="X")
        anc_red_qubits = anc_qubits.select(color="r")
        anc_green_qubits = anc_qubits.select(color="g")
        anc_blue_qubits = anc_qubits.select(color="b")

        self.qubit_groups.update(
            {
                "data": data_qubits,
                "anc": anc_qubits,
                "anc_Z": anc_Z_qubits,
                "anc_X": anc_X_qubits,
                "anc_red": anc_red_qubits,
                "anc_green": anc_green_qubits,
                "anc_blue": anc_blue_qubits,
            }
        )

        # Add edges
        links = []
        offsets = [(-2, 1), (2, 1), (4, 0), (2, -1), (-2, -1), (-4, 0)]
        for anc_qubit in self.qubit_groups["anc"]:
            data_qubits = []
            for offset in offsets:
                data_qubit_x = anc_qubit["x"] + offset[0]
                data_qubit_y = anc_qubit["y"] + offset[1]
                data_qubit_name = f"{data_qubit_x}-{data_qubit_y}"
                try:
                    data_qubit = tanner_graph.vs.find(name=data_qubit_name)
                except ValueError:
                    continue
                data_qubits.append(data_qubit)
                tanner_graph.add_edge(anc_qubit, data_qubit, kind="tanner", color=None)

            if anc_qubit["pauli"] == "Z":
                weight = len(data_qubits)
                for i in range(weight):
                    qubit = data_qubits[i]
                    next_qubit = data_qubits[(i + 1) % weight]
                    if not tanner_graph.are_adjacent(qubit, next_qubit):
                        link = tanner_graph.add_edge(
                            qubit, next_qubit, kind="lattice", color=None
                        )
                        links.append(link)

        # Assign colors to links
        link: ig.Edge
        for link in links:
            v1: ig.Vertex
            v2: ig.Vertex
            v1, v2 = link.target_vertex, link.source_vertex
            ngh_ancs_1 = {anc.index for anc in v1.neighbors() if anc["pauli"] == "Z"}
            ngh_ancs_2 = {anc.index for anc in v2.neighbors() if anc["pauli"] == "Z"}
            color = tanner_graph.vs[(ngh_ancs_1 ^ ngh_ancs_2).pop()]["color"]
            link["color"] = color

    def get_logical_ops(self, basis: PauliXZ):
        return [self.observable]

def _build_parallel_circuit(
    d: int, rounds: int, schedule_config: dict, basis: PauliXZ, shared: bool = True
) -> tuple[stim.Circuit, np.ndarray]:
    """
    Build a parallelized color-code circuit from a schedule config.

    Uses manual construction with 12 parallel CX steps per round (6 for Z, 6 for X).
    Suitable for both tri_optimal (pass TRI_OPTIMAL_SCHEDULE_CONFIG) and
    optimized zero-collision schedules (pass config from load_zero_collision_schedule).

    Args:
        d: Code distance
        rounds: Number of syndrome extraction rounds
        schedule_config: Dict with 'r_schedule', 'g_schedule', 'b_schedule' keys
                        (each a 6-element list: Z step 1-6 per data position; X uses same at 7-12)
        basis: Memory experiment basis, either "Z" (default) or "X".
        noise_model: noise model to use for injecting noise

    Returns:
        Tuple containing the noisy stim.Circuit and the logical observable of the code
    """
    assert basis in [Pauli.X, Pauli.Z], f"basis must be 'X' or 'Z', got {basis!r}"

    # Get ColorCode structure for qubit layout only (skip DEM generation for speed)
    colorcode = ColorCode(
        d=d,
        rounds=1
    )
    
    tanner_graph = colorcode.tanner_graph
    anc_Z_qubits = colorcode.qubit_groups["anc_Z"]
    anc_X_qubits = colorcode.qubit_groups["anc_X"]
    data_qubits = colorcode.qubit_groups["data"]

    # Canonical offsets for hexagonal plaquettes
    OFFSETS = [(-2, 1), (2, 1), (4, 0), (2, -1), (-2, -1), (-4, 0)]

    def get_data_qid(anc_qubit, offset_idx):
        """Get data qubit id for an ancilla at given offset."""
        offset = OFFSETS[offset_idx % 6]
        data_x = anc_qubit["x"] + offset[0]
        data_y = anc_qubit["y"] + offset[1]
        data_name = f"{data_x}-{data_y}"
        try:
            return tanner_graph.vs.find(name=data_name).index
        except ValueError:
            return None

    # Build full schedules by color (same for bulk and edge in parallel version)
    schedules_by_color = {
        "r": schedule_config["r_schedule"],
        "g": schedule_config["g_schedule"],
        "b": schedule_config["b_schedule"],
    }

    # Collect CNOTs grouped by time step (1-12)
    # Z stabilizers: 1-6, X stabilizers: 7-12
    cnots_by_timestep = {t: [] for t in range(1, 13)}

    # Z stabilizers
    for anc_qubit in anc_Z_qubits:
        color = anc_qubit["color"]
        schedule = schedules_by_color[color]
        anc_qid = anc_qubit.index
        for pos_idx, timestep in enumerate(schedule):
            data_qid = get_data_qid(anc_qubit, pos_idx)
            if data_qid is not None:
                # Z stabilizer: CX data -> ancilla
                cnots_by_timestep[timestep].append((data_qid, anc_qid))

    # [QCCD patch] one auxiliary per plaquette (arXiv:2603.28852's title, and this file's own
    # comments): the X check of a plaquette uses the Z check's ancilla, measured and re-prepared
    # in |+> between the two halves.  shared=False keeps the original two ancillas per plaquette.
    z_at = {(a["x"], a["y"]): a.index for a in anc_Z_qubits}
    share = {a.index: (z_at[(a["x"], a["y"])] if shared else a.index) for a in anc_X_qubits}

    # X stabilizers (time steps 7-12): reuse same ancillas as Z (same plaquette = same support)
    for anc_qubit in anc_X_qubits:
        color = anc_qubit["color"]
        schedule = schedules_by_color[color]
        anc_qid = share[anc_qubit.index]
        for pos_idx, timestep in enumerate(schedule):
            data_qid = get_data_qid(anc_qubit, pos_idx)
            if data_qid is not None:
                # X stabilizer: CX ancilla -> data (at timestep + 6); same qubit as Z ancilla
                cnots_by_timestep[timestep + 6].append((anc_qid, data_qid))

    # Qubit IDs: only data + Z ancillas (X ancillas are reused from Z ancillas)
    data_qids = [dq["qid"] for dq in data_qubits]
    z_anc_qids = [anc["qid"] for anc in anc_Z_qubits]
    x_anc_qids = [share[anc.index] for anc in anc_X_qubits]

    num_anc = len(z_anc_qids) + len(x_anc_qids)
    num_z_anc = len(z_anc_qids)
    num_x_anc = len(x_anc_qids)
    used_qids = set(data_qids) | set(z_anc_qids) | set(x_anc_qids)

    # Build the stim circuit
    circuit = stim.Circuit()

    # Add QUBIT_COORDS only for qubits used in the circuit
    for v in tanner_graph.vs:
        if v.index in used_qids:
            circuit.append("QUBIT_COORDS", [v.index], [v["x"], v["y"]])

    # Initialize data qubits and ancillas
    own_x = [] if shared else x_anc_qids   # a shared ancilla is put in |+> mid-round
    if basis == Pauli.X:
        x_reset_qids = own_x + data_qids
        z_reset_qids = z_anc_qids
    else:
        x_reset_qids = own_x
        z_reset_qids = z_anc_qids + data_qids
    circuit.append("R", z_reset_qids)
    circuit.append("RX", x_reset_qids)
    circuit.append("TICK")

    def add_syndrome_round(circuit, is_first: bool = False, is_last: bool = False):
        """One round: steps 1-6 (Z), M + RX on ancillas, steps 7-12 (X), MX; if not last round, R ancillas. No noise (added later)."""
        # Steps 1-6: Z stabilizer CNOTs (data -> ancilla)
        for t in range(1, 7):
            cx_targets = []
            for ctrl, targ in cnots_by_timestep[t]:
                cx_targets.extend([ctrl, targ])
            if cx_targets:
                circuit.append("CX", cx_targets)
            circuit.append("TICK")

        if shared:
            # the Z half is read, and the same ancillas re-prepared in |+> for the X half
            circuit.append("M", z_anc_qids)
            circuit.append("TICK")
            circuit.append("RX", x_anc_qids)
            circuit.append("TICK")

        # Steps 7-12: X stabilizer CNOTs (ancilla -> data), same ancilla qubits
        for t in range(7, 13):
            cx_targets = []
            for ctrl, targ in cnots_by_timestep[t]:
                cx_targets.extend([ctrl, targ])
            if cx_targets:
                circuit.append("CX", cx_targets)
            circuit.append("TICK")

        # Measure ancilla qubits
        if not shared:
            circuit.append("M", z_anc_qids)
        circuit.append("MX", x_anc_qids)
        circuit.append("TICK")

        # Construct basis-dependent detectors from ancilla measurements
        if basis == Pauli.X:
            # X ancillas measured most recently, so span the measurement record 
            # from [-num_x_anc, -1]
            for j, anc in enumerate(anc_X_qubits):
                color_val = 0 if anc["color"] == "r" else (1 if anc["color"] == "g" else 2)
                coords = (anc["x"], anc["y"], 0, 0, color_val)
                targets = [stim.target_rec(-num_x_anc + j)] if is_first else \
                          [stim.target_rec(-num_x_anc + j), stim.target_rec(-num_x_anc + j - num_anc)]
                
                circuit.append("DETECTOR", targets, coords)
        else:
            # Z ancillas measured before X ancillas, so span the measurement record
            # from [-num_x_anc - num_z_anc, -num_x_anc]
            for j, anc in enumerate(anc_Z_qubits):
                color_val = 0 if anc["color"] == "r" else (1 if anc["color"] == "g" else 2)
                coords = (anc["x"], anc["y"], 0, 0, color_val)
                targets = [stim.target_rec(-num_x_anc - num_z_anc + j)] if is_first else \
                          [stim.target_rec(-num_x_anc - num_z_anc + j),
                           stim.target_rec(-num_x_anc - num_z_anc + j - num_anc)]
                
                circuit.append("DETECTOR", targets, coords)

        # Shift coordinates for next round
        circuit.append("SHIFT_COORDS", [], [0, 0, 1])

        if not is_last:
            # Reset ancillas for next round
            circuit.append("R", z_anc_qids)
            if not shared:
                circuit.append("RX", x_anc_qids)
            circuit.append("TICK")

    # First round (is last if only one round)
    for round_idx in range(rounds):
        add_syndrome_round(circuit, is_first=(round_idx==0), is_last=(round_idx == rounds-1))

    # Final data measurement
    final_meas_gate = "MX" if basis == Pauli.X else "M"
    circuit.append(final_meas_gate, data_qids)

    # Final detectors: compare parity of data measurements 
    # with last ancilla measurements
    if basis == Pauli.X:
        # X ancillas measured right before data qubits, so
        # they span the measurement record from
        # [-num_x_anc - num_data, -num_data]
        anc_lookback = -len(data_qids) - num_x_anc
        pauli_coord = 0
        anc_qubits = anc_X_qubits
    else:
        # Z ancillas measured before X ancillas, so they
        # span the measurement record from
        # [-num_z_anc - num_x_anc - num_data, -num_x_anc - num_data]
        anc_lookback = -len(data_qids) - num_x_anc - num_z_anc
        pauli_coord = 2
        anc_qubits = anc_Z_qubits

    for j, anc in enumerate(anc_qubits):
        color_val = 0 if anc["color"] == "r" else (1 if anc["color"] == "g" else 2)
        coords = (anc["x"], anc["y"], 0, pauli_coord, color_val)

        connected_data = []
        for pos_idx in range(6):
            data_qid = get_data_qid(anc, pos_idx)
            if data_qid is not None:
                connected_data.append(data_qid)
        
        targets = [stim.target_rec(-len(data_qids) + data_qids.index(dq)) for dq in connected_data]
        targets.append(stim.target_rec(anc_lookback + j))
        circuit.append("DETECTOR", targets, coords)

    # Observable: logical Z or X string along red boundary
    obs_qids = [dq["qid"] for dq in data_qubits if dq["obs"]]
    obs_targets = [
        stim.target_rec(-len(data_qids) + data_qids.index(qid)) for qid in obs_qids
    ]
    circuit.append("OBSERVABLE_INCLUDE", obs_targets, 0)

    return circuit

def build_circuit(
        distance: int, 
        num_rounds: int,
        basis: PauliXZ,
        shared: bool = True
    ) -> stim.Circuit:

    schedule_config= { # schedules from Fig. 1 in the paper
        "r_schedule": [1, 3, 2, 5, 4, 6],
        "g_schedule": [5, 1, 4, 3, 6, 2],
        "b_schedule": [1, 5, 3, 6, 2, 4]
    }
    
    return _build_parallel_circuit(distance, num_rounds, schedule_config, basis, shared)