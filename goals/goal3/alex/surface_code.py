import numpy as np

import stim
from qldpc.codes import CSSCode
from qldpc.objects import Pauli, PauliXZ

def _Hx(d):
    """
    Generate the parity check matrix for the X stabilizers
    of a distance "d" rotated surface code
    
    Parameters:
        d (int): Distance of the rotated surface code (must be an odd integer)
    
    Returns:
        np.ndarray: A binary matrix representing the X stabilizer parity check matrix.

        
    Example: Distance-3 X stabilizer check matrix
    
    check_matrix = np.array([
        [0, 1, 1, 0, 0, 0, 0, 0, 0],
        [1, 1, 0, 1, 1, 0, 0, 0, 0],
        [0, 0, 0, 0, 1, 1, 0, 1, 1],
        [0, 0, 0, 0, 0, 0, 1, 1, 0]
    ])
    """
    if d % 2 == 0:
        raise ValueError("Distance d must be an odd integer.")
    
    n_qubits = d**2  # Total number of qubits (on edges of the lattice)
    n_stabilizers = ((d+1)*(d-1))//2  # Number of vertex stabilizers
    
    def get_qubit_index(row, col):
        # return col
        return row * d + col
    
    # Initialize the stabilizer matrix
    H_X = np.zeros((n_stabilizers, n_qubits), dtype=np.uint8)
    
    # Iterate over the stabilizers
    for row in range(d+1):
        for col in range((d-1)//2):
            stabilizer_idx = (row * (d-1)//2) + col
            if row == 0:
                H_X[stabilizer_idx, get_qubit_index(0, 2*col + 1)] = 1
                H_X[stabilizer_idx, get_qubit_index(0, 2*(col+1))] = 1
            elif row == d:
                H_X[stabilizer_idx, get_qubit_index(d-1, 2*col)] = 1
                H_X[stabilizer_idx, get_qubit_index(d-1, 2*col + 1)] = 1
            # Odd row
            elif row % 2:
                H_X[stabilizer_idx, get_qubit_index(row-1, 2*col)] = 1
                H_X[stabilizer_idx, get_qubit_index(row-1, 2*col + 1)] = 1
                H_X[stabilizer_idx, get_qubit_index(row, 2*col)] = 1
                H_X[stabilizer_idx, get_qubit_index(row, 2*col + 1)] = 1
            # Even row
            else:
                H_X[stabilizer_idx, get_qubit_index(row - 1, 2*col + 1)] = 1
                H_X[stabilizer_idx, get_qubit_index(row - 1, 2*(col+1))] = 1
                H_X[stabilizer_idx, get_qubit_index(row, 2*col + 1)] = 1
                H_X[stabilizer_idx, get_qubit_index(row, 2*(col+1))] = 1

    return H_X

def _Hz(d):
    """
    Generate the parity check matrix for the Z stabilizers
    of a distance "d" rotated surface code.

    Parameters:
        d (int): Distance of the rotated surface code (must be an odd integer)

    Returns:
        np.ndarray: A binary matrix representing the Z stabilizer parity check matrix.
    """
    if d % 2 == 0:
        raise ValueError("Distance d must be an odd integer.")
    
    n_qubits = d ** 2
    n_stabilizers = ((d - 1) * (d + 1)) // 2  # Number of plaquette stabilizers

    def get_qubit_index(row, col):
        return row * d + col

    H_Z = np.zeros((n_stabilizers, n_qubits), dtype=np.uint8)

    for row in range(d-1):
        for col in range((d+1)//2):
            stabilizer_idx = row*((d+1)//2) + col

            if col == 0 and row % 2 == 0: # left boundary
                H_Z[stabilizer_idx, get_qubit_index(row, 0)] = 1
                H_Z[stabilizer_idx, get_qubit_index(row+1, 0)] = 1
            elif col == ((d+1)//2)-1 and row % 2 == 1: # right boundary
                H_Z[stabilizer_idx, get_qubit_index(row, d-1)] = 1
                H_Z[stabilizer_idx, get_qubit_index(row+1, d-1)] = 1
            elif row % 2 == 0:
                H_Z[stabilizer_idx, get_qubit_index(row, 2*col-1)] = 1
                H_Z[stabilizer_idx, get_qubit_index(row, 2*col)] = 1
                H_Z[stabilizer_idx, get_qubit_index(row+1, 2*col-1)] = 1
                H_Z[stabilizer_idx, get_qubit_index(row+1, 2*col)] = 1
            else:
                H_Z[stabilizer_idx, get_qubit_index(row, 2*col)] = 1
                H_Z[stabilizer_idx, get_qubit_index(row, 2*col+1)] = 1
                H_Z[stabilizer_idx, get_qubit_index(row+1, 2*col)] = 1
                H_Z[stabilizer_idx, get_qubit_index(row+1, 2*col+1)] = 1

    return H_Z

def _get_CNOT_schedules(pcm: list[list[int]], distance: int, 
                       ancilla_id_offset: int, basis: PauliXZ) -> dict:
    """Calculates the internal 2D schedule order, ignoring output coordinates."""
    schedules = {}

    ancillas_per_row = (distance - 1) // 2 
    if basis == Pauli.Z:
        ancillas_per_row += 1

    for ancilla_idx, row in enumerate(pcm):
        ancilla_row = ancilla_idx // ancillas_per_row
        ancilla_col = ancilla_idx % ancillas_per_row
        ancilla_id = ancilla_idx + ancilla_id_offset
        data_indices = sorted([i for i, val in enumerate(row) if val == 1])
        if not data_indices:
            continue

        
        
        # When data qubits are indexed in row-major order, 
        # they naturally list in NW, NE, SW, SE order
        if basis == Pauli.Z: # N schedule
            # N schedule must go NE, SE, NW, SW
            if len(data_indices) == 2: # 2-body stabilizer at the edge of the lattice
                if ancilla_col == 0:
                    schedule = {0: data_indices[0], 1: data_indices[1]}
                else:
                    schedule = {2: data_indices[0], 3: data_indices[1]}
            else: # 4-body stabilizer
                schedule = {0: data_indices[1], 1: data_indices[3], 
                            2: data_indices[0], 3: data_indices[2]}
        else: # Z schedule
            # Z schedule must go NE, NW, SE, SW
            if len(data_indices) == 2: # 2-body stabilizer at the edge of the lattice
                if ancilla_row == 0:
                    schedule = {2: data_indices[1], 3: data_indices[0]}
                else:
                    schedule = {0: data_indices[1], 1: data_indices[0]}
            else: # 4-body stabilizer
                schedule = {0: data_indices[1], 1: data_indices[0],
                            2: data_indices[3], 3: data_indices[2]}
                
        schedules[ancilla_id] = schedule
    
    return schedules

def _build_syndrome_cycle(
    x_stabs: dict, 
    z_stabs: dict, 
    all_measure_qubits: list, 
    display_coords: dict, 
    is_first_cycle: bool, 
    basis: PauliXZ
) -> stim.Circuit:
    """Subroutine to generate a single cycle of syndrome extraction."""
    circuit = stim.Circuit()
    x_measure_qubits = list(x_stabs.keys())
    z_measure_qubits = list(z_stabs.keys())
    num_measurements = len(all_measure_qubits)
    
    # Pre-calculate measurement record offsets for this cycle
    record_offsets = {mq: -(num_measurements - i) 
                      for i, mq in enumerate(all_measure_qubits)}
    
    circuit.append("H", x_measure_qubits)
    circuit.append("TICK")
    
    # Collect CNOT control/targets based on schedules for each stabilizer
    for slot in range(4):
        cnot_targets = []
        for mq, schedule in x_stabs.items():
            if slot in schedule:
                # X ancillas are controls
                cnot_targets.extend([mq, schedule[slot]])
        for mq, schedule in z_stabs.items():
            # Z ancillas are targets
            if slot in schedule:
                cnot_targets.extend([schedule[slot], mq])
        
        if cnot_targets:
            circuit.append("CX", cnot_targets)
        circuit.append("TICK")
        
    circuit.append("H", x_measure_qubits)
    circuit.append("TICK")
    circuit.append("MR", all_measure_qubits)
    
    # --- Detectors ---
    measure_qubits = z_measure_qubits if basis == Pauli.Z else x_measure_qubits
    if is_first_cycle:
        for mq in measure_qubits:
            # Time coordinate is static '0'; SHIFT_COORDS will handle increments later
            circuit.append("DETECTOR", [stim.target_rec(record_offsets[mq])],
                           [0, 0, display_coords[mq][1]])
    else:
        # Compare current measurement to the previous cycle's measurement
        for mq in measure_qubits:
            circuit.append(
                "DETECTOR", 
                [stim.target_rec(record_offsets[mq]),
                 stim.target_rec(record_offsets[mq] - num_measurements)],
                [0, 0, display_coords[mq][1]]
            )
    
    circuit.append("SHIFT_COORDS", [], [1, 0, 0])
    circuit.append("TICK")
    
    return circuit

def build_circuit(
        distance: int, 
        num_rounds: int,
        basis: PauliXZ
    ) -> stim.Circuit:
    if basis not in [Pauli.X, Pauli.Z]:
        raise ValueError("basis must be 'X' or 'Z'")

    x_check = _Hx(distance)
    z_check = _Hz(distance)
    code = CSSCode(x_check, z_check)
    observable = np.flatnonzero(code.get_logical_ops(basis))
    
    num_data_qubits = len(x_check[0])
    num_x_ancillas = len(x_check)
    num_z_ancillas = len(z_check)
    
    x_stabs = _get_CNOT_schedules(x_check, distance, num_data_qubits, Pauli.X)
    z_stabs = _get_CNOT_schedules(z_check, distance, num_data_qubits + num_x_ancillas, 
                                  Pauli.Z)

    display_coords = {}
    # Define data and ancilla qubit coordinates
    for d in range(num_data_qubits):
        display_coords[d] = (0, d)
    for x_idx in range(num_x_ancillas):
        mq = num_data_qubits + x_idx
        display_coords[mq] = (1, x_idx)
    for z_idx in range(num_z_ancillas):
        mq = num_data_qubits + num_x_ancillas + z_idx
        display_coords[mq] = (1, num_x_ancillas + z_idx)

    circuit = stim.Circuit()

    # Extract qubit groups
    data_qubits = list(range(num_data_qubits))
    x_measure_qubits = list(x_stabs.keys())
    z_measure_qubits = list(z_stabs.keys())
    all_measure_qubits = x_measure_qubits + z_measure_qubits

    num_measurements_per_cycle = len(all_measure_qubits)
    
    # Write qubit coordinates
    for q in data_qubits + all_measure_qubits:
        circuit.append("QUBIT_COORDS", [q], [display_coords[q][0], 
                                             display_coords[q][1]])
        
    # Initialize data and ancilla qubits
    if basis == Pauli.X:
        circuit.append("RX", data_qubits)
        circuit.append("R", all_measure_qubits)
    else:
        circuit.append("R", data_qubits+all_measure_qubits)
    circuit.append("TICK")
    
    # Construct syndrome extraction cycles
    if num_rounds > 0:
        # Ensure we only use deterministic measurements for detectors
        cycle_1 = _build_syndrome_cycle(x_stabs, z_stabs, all_measure_qubits, 
                                        display_coords, is_first_cycle=True, 
                                        basis=basis)
        circuit += cycle_1
        
        # Wrap remaining cycles in a repeat block
        if num_rounds > 1:
            loop_body = _build_syndrome_cycle(x_stabs, z_stabs, all_measure_qubits, 
                                              display_coords, is_first_cycle=False, 
                                              basis=basis)
            circuit.append(stim.CircuitRepeatBlock(num_rounds - 1, loop_body))
            
    # Measure out data qubits at the end
    if basis == Pauli.X:
        circuit.append("MX", data_qubits)
    else:
        circuit.append("M", data_qubits)
    
    # Construct final detectors from the parity of data qubit measurements
    # and the previous round's ancilla measurements
    stabs_to_check = z_stabs if basis == Pauli.Z else x_stabs
    record_offsets = {mq: -(num_measurements_per_cycle - i) 
                      for i, mq in enumerate(all_measure_qubits)}
    
    for mq, schedule in stabs_to_check.items():
        detector_targets = [stim.target_rec(record_offsets[mq] - num_data_qubits)]
        for d_idx in schedule.values():
            detector_targets.append(stim.target_rec(-num_data_qubits + d_idx))
        circuit.append("DETECTOR", detector_targets, 
                       [0, display_coords[mq][0], display_coords[mq][1]])

    # Logical obserable
    observable_targets = [stim.target_rec(-num_data_qubits + o) for o in observable]
    circuit.append("OBSERVABLE_INCLUDE", observable_targets, 0)
        
    return circuit