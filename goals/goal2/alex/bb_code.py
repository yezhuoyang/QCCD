from functools import reduce
from itertools import chain
import numpy as np
import stim
from scipy.sparse import identity, kron
from qldpc.objects import Pauli, PauliXZ

# The below code for generating IBM's SM circuit for the BB codes is adapted
# from the original source at https://github.com/gongaa/SlidingWindowDecoder
# See IBM.ipynb and src/codes_q.py for the relevant code.

def _row_echelon(mat, reduced=False):
    r"""Converts a binary matrix to (reduced) row echelon form via Gaussian Elimination, 
    also works for rank-deficient matrix. Unlike the make_systematic method,
    no column swaps will be performed.

    Input 
    ----------
    mat : ndarry
        A binary matrix in numpy.ndarray format.
    reduced: bool
        Defaults to False. If true, the reduced row echelon form is returned. 
    
    Output
    -------
    row_ech_form: ndarray
        The row echelon form of input matrix.
    rank: int
        The rank of the matrix.
    transform: ndarray
        The transformation matrix such that (transform_matrix@matrix)=row_ech_form
    pivot_cols: list
        List of the indices of pivot num_cols found during Gaussian elimination
    """

    m, n = np.shape(mat)
    # Don't do "m<=n" check, allow over-complete matrices
    mat = np.copy(mat)
    # Convert to bool for faster arithmetics
    mat = mat.astype(bool)
    transform = np.identity(m).astype(bool)
    pivot_row = 0
    pivot_cols = []

    # Allow all-zero column. Row operations won't induce all-zero columns, if they are not present originally.
    # The make_systematic method will swap all-zero columns with later non-all-zero columns.
    # Iterate over cols, for each col find a pivot (if it exists)
    for col in range(n):
        # Select the pivot - if not in this row, swap rows to bring a 1 to this row, if possible
        if not mat[pivot_row, col]:
            # Find a row with a 1 in this column
            swap_row_index = pivot_row + np.argmax(mat[pivot_row:m, col])
            # If an appropriate row is found, swap it with the pivot. Otherwise, all zeroes - will loop to next col
            if mat[swap_row_index, col]:
                # Swap rows
                mat[[swap_row_index, pivot_row]] = mat[[pivot_row, swap_row_index]]
                # Transformation matrix update to reflect this row swap
                transform[[swap_row_index, pivot_row]] = transform[[pivot_row, swap_row_index]]

        if mat[pivot_row, col]: # will evaluate to True if this column is not all-zero
            if not reduced: # clean entries below the pivot 
                elimination_range = [k for k in range(pivot_row + 1, m)]
            else:           # clean entries above and below the pivot
                elimination_range = [k for k in range(m) if k != pivot_row]
            for idx_r in elimination_range:
                if mat[idx_r, col]:    
                    mat[idx_r] ^= mat[pivot_row]
                    transform[idx_r] ^= transform[pivot_row]
            pivot_row += 1
            pivot_cols.append(col)

        if pivot_row >= m: # no more rows to search
            break

    rank = pivot_row
    row_ech_form = mat.astype(int)

    return [row_ech_form, rank, transform.astype(int), pivot_cols]

def _inverse(mat):
    r"""Computes the left inverse of a full-rank matrix.

    Input
    ----------
    matrix: ndarray
        The binary matrix to be inverted in numpy.ndarray format. This matrix must either be
        square full-rank or rectangular with full-column rank.

    Output
    -------
    inverse: ndarray
        The inverted binary matrix
    
    Note
    -----
    The `left inverse' is computed when the number of rows in the matrix
    exceeds the matrix rank. The left inverse is defined as follows::

        Inverse(M.T@M)@M.T

    We can make a further simplification by noting that the row echelon form matrix
    with full column rank has the form::

        row_echelon_form=P@M=vstack[I,A]

    In this case the left inverse simplifies to::

        Inverse(M^T@P^T@P@M)@M^T@P^T@P=M^T@P^T@P=row_echelon_form.T@P"""

    m, n = mat.shape
    reduced_row_ech, rank, transform, _ = _row_echelon(mat, reduced=True)
    if m == n and rank == m:
        return transform
    # compute the left-inverse
    elif m > rank and n == rank:  # left inverse
        return reduced_row_ech.T @ transform % 2
    else:
        raise ValueError("This matrix is not invertible. Please provide either a full-rank square\
        matrix or a rectangular matrix with full column rank.")

def _kernel(mat):
    r"""Computes the kernel of the matrix M.
    All vectors x in the kernel of M satisfy the following condition::

        Mx=0 \forall x \in ker(M)

    Input 
    ----------
    mat: ndarray
        A binary matrix in numpy.ndarray format.
    
    Output
    -------
    ker: ndarray
        A binary matrix which is the kernel of the input binary matrix.

    rank: int
        Rank of transposed mat, which is the same as the rank of mat.

    pivot_cols: list
        List of the indices of pivot of the transposed mat. Can be used in row_basis.
    
    Note
    -----
    Why does this work?

    The transformation matrix, P, transforms the matrix M into row echelon form, ReM::

        P@M=ReM=[A,0]^T,
    
    where the width of A is equal to the rank. This means the bottom n-k rows of P
    must produce a zero vector when applied to M. For a more formal definition see
    the Rank-Nullity theorem.
    """

    transpose = mat.T
    m, _ = transpose.shape
    _, rank, transform, pivot_cols = _row_echelon(transpose)
    ker = transform[rank:m]
    return ker, rank, pivot_cols

class css_code(): # a refactored version of Roffe's package
    # do as less row echelon form calculation as possible.
    def __init__(self, hx=np.array([[]]), hz=np.array([[]]), 
                 code_distance=np.nan, name=None, name_prefix="", 
                 check_css=False):

        self.hx = hx # hx pcm
        self.hz = hz # hz pcm

        self.lx = np.array([[]]) # x logicals
        self.lz = np.array([[]]) # z logicals

        self.N = np.nan # block length
        self.K = np.nan # code dimension
        self.D = code_distance # do not take this as the real code distance
        # TODO: use QDistRnd to get the distance
        # the quantum code distance is the minimum weight of all the affine codes
        # each of which is a coset code of a non-trivial logical op + stabilizers
        self.L = np.nan # max column weight
        self.Q = np.nan # max row weight

        _, nx = self.hx.shape
        _, nz = self.hz.shape

        assert nx == nz, "hx and hz should have equal number of columns!"
        assert nx != 0,  "number of variable nodes should not be zero!"
        if check_css: # For performance reason, default to False
            assert not np.any(hx @ hz.T % 2), "CSS constraint not satisfied"
        
        self.N = nx
        self.hx_perp, self.rank_hx, self.pivot_hx = _kernel(hx) # orthogonal complement
        self.hz_perp, self.rank_hz, self.pivot_hz = _kernel(hz)
        self.hx_basis = self.hx[self.pivot_hx] # same as calling row_basis(self.hx)
        self.hz_basis = self.hz[self.pivot_hz] # but saves one row echelon calculation
        self.K = self.N - self.rank_hx - self.rank_hz

        self.compute_ldpc_params()
        self.compute_logicals()

        self.num_checks_z = self.N//2
        self.num_checks_x = self.N//2
        self.num_qubits = self.N
        # Stim qubit IDs the code's data qubits
        self.data_qubit_ids = [i for i in range(self.N//2, self.N//2 + self.N)]

        self.name = f"{name_prefix}_n{self.N}_k{self.K}" if name is None else name

    def compute_ldpc_params(self):

        #column weights
        hx_l = np.max(np.sum(self.hx, axis=0))
        hz_l = np.max(np.sum(self.hz, axis=0))
        self.L = np.max([hx_l, hz_l]).astype(int)

        #row weights
        hx_q = np.max(np.sum(self.hx, axis=1))
        hz_q = np.max(np.sum(self.hz, axis=1))
        self.Q = np.max([hx_q, hz_q]).astype(int)

    def compute_logicals(self):

        def compute_lz(ker_hx, im_hzT):
            # lz logical operators
            # lz\in ker{hx} AND \notin Im(hz.T)
            # in the below we row reduce to find vectors in kx that are not in the image of hz.T.
            log_stack = np.vstack([im_hzT, ker_hx])
            pivots = _row_echelon(log_stack.T)[3]
            log_op_indices = [i for i in range(im_hzT.shape[0], log_stack.shape[0]) if i in pivots]
            log_ops = log_stack[log_op_indices]
            return log_ops

        self.lx = compute_lz(self.hz_perp, self.hx_basis)
        self.lz = compute_lz(self.hx_perp, self.hz_basis)

        return self.lx, self.lz

    def canonical_logicals(self):
        temp = _inverse(self.lx @ self.lz.T % 2)
        self.lx = temp @ self.lx % 2
    
    def get_logical_ops(self, basis: Pauli):
        if basis == Pauli.X:
            return self.lx
        else:
            return self.lz

def _create_circulant_matrix(l, pows):
    h = np.zeros((l,l), dtype=int)
    for i in range(l):
        for c in pows:
            h[(i+c)%l, i] = 1
    return h

def _create_bivariate_bicycle_codes(l, m, A_x_pows, A_y_pows, 
                                   B_x_pows, B_y_pows, name=None
) -> tuple[css_code, list, list] :
    S_l = _create_circulant_matrix(l, [-1])
    S_m = _create_circulant_matrix(m, [-1])
    x = kron(S_l, identity(m, dtype=int))
    y = kron(identity(l, dtype=int), S_m)
    A_list = [x**p for p in A_x_pows] + [y**p for p in A_y_pows]
    B_list = [y**p for p in B_y_pows] + [x**p for p in B_x_pows] 
    A = reduce(lambda x,y: x+y, A_list).toarray()
    B = reduce(lambda x,y: x+y, B_list).toarray()
    hx = np.hstack((A, B))
    hz = np.hstack((B.T, A.T))
    return css_code(hx, hz, name=name, name_prefix="BB", check_css=True), A_list, B_list

def _build_circuit(code, A_list, B_list, rounds, z_basis=True):

    n = code.N
    a1, a2, a3 = A_list
    b1, b2, b3 = B_list

    def nnz(m):
        a, b = m.nonzero()
        return [int(i) for i in b[np.argsort(a)]]

    A1, A2, A3 = nnz(a1), nnz(a2), nnz(a3)
    B1, B2, B3 = nnz(b1), nnz(b2), nnz(b3)

    A1_T, A2_T, A3_T = nnz(a1.T), nnz(a2.T), nnz(a3.T)
    B1_T, B2_T, B3_T = nnz(b1.T), nnz(b2.T), nnz(b3.T)

    # |+> ancilla: 0 ~ n/2-1. Control in CNOTs.
    X_check_offset = 0
    # L data qubits: n/2 ~ n-1. 
    L_data_offset = n//2
    # R data qubits: n ~ 3n/2-1.
    R_data_offset = n
    # |0> ancilla: 3n/2 ~ 2n-1. Target in CNOTs.
    Z_check_offset = 3*n//2

    detector_circuit_str = ""
    for i in range(n//2):
        detector_circuit_str += f"DETECTOR rec[{-n//2+i}]\n"
    detector_circuit = stim.Circuit(detector_circuit_str)

    detector_repeat_circuit_str = ""
    for i in range(n//2):
        detector_repeat_circuit_str += f"DETECTOR rec[{-n//2+i}] rec[{-n-n//2+i}]\n"
    detector_repeat_circuit = stim.Circuit(detector_repeat_circuit_str)

    x_ancillas = [X_check_offset + i for i in range(n//2)]
    z_ancillas = [Z_check_offset + i for i in range(n//2)]
    data = [L_data_offset + i for i in range(n)]

    # The schedule in append_blocks follows the one shown in Fig. 5 of
    # arXiv:2308.07915
    def append_blocks(circuit: stim.Circuit, repeat=False):
        # CNOTs from R data to to Z-checks
        r_data = [R_data_offset + A1_T[i] for i in range(n//2)]
        circuit.append("CX", list(chain.from_iterable(zip(r_data, z_ancillas))))

        # tick
        circuit.append("TICK")

        # Round 2
        # CNOTs from X-checks to L data
        l_data = [L_data_offset + A2[i] for i in range(n//2)]
        circuit.append("CX", list(chain.from_iterable(zip(x_ancillas, l_data))))
        # CNOTs from R data to Z-checks
        r_data = [R_data_offset + A3_T[i] for i in range(n//2)]
        circuit.append("CX", list(chain.from_iterable(zip(r_data, z_ancillas))))

        # tick
        circuit.append("TICK")

        # Round 3
        # CNOTs from X-checks to R data
        r_data = [R_data_offset + B2[i] for i in range(n//2)]
        circuit.append("CX", list(chain.from_iterable(zip(x_ancillas, r_data))))
        # CNOTs from L data to Z-checks
        l_data = [L_data_offset + B1_T[i] for i in range(n//2)]
        circuit.append("CX", list(chain.from_iterable(zip(l_data, z_ancillas))))

        # tick
        circuit.append("TICK")

        # Round 4
        # CNOTs from X-checks to R data
        r_data = [R_data_offset + B1[i] for i in range(n//2)]
        circuit.append("CX", list(chain.from_iterable(zip(x_ancillas, r_data))))
        # CNOTs from L data to Z-checks
        l_data = [L_data_offset + B2_T[i] for i in range(n//2)]
        circuit.append("CX", list(chain.from_iterable(zip(l_data, z_ancillas))))

        # tick
        circuit.append("TICK")

        # Round 5
        # CNOTs from X-checks to R data
        r_data = [R_data_offset + B3[i] for i in range(n//2)]
        circuit.append("CX", list(chain.from_iterable(zip(x_ancillas, r_data))))
        # CNOTs from L data to Z-checks
        l_data = [L_data_offset + B3_T[i] for i in range(n//2)]
        circuit.append("CX", list(chain.from_iterable(zip(l_data, z_ancillas))))

        # tick
        circuit.append("TICK")

        # Round 6
        # CNOTs from X-checks to L data
        l_data = [L_data_offset + A1[i] for i in range(n//2)]
        circuit.append("CX", list(chain.from_iterable(zip(x_ancillas, l_data))))
        # CNOTs from R data to Z-checks
        r_data = [R_data_offset + A2_T[i] for i in range(n//2)]
        circuit.append("CX", list(chain.from_iterable(zip(r_data, z_ancillas))))

        # tick
        circuit.append("TICK")

        # Round 7
        # CNOTs from X-checks to L data
        l_data = [L_data_offset + A3[i] for i in range(n//2)]
        circuit.append("CX", list(chain.from_iterable(zip(x_ancillas, l_data))))
        # Measure Z-checks
        circuit.append("MR", z_ancillas)

        # Z check detectors
        if z_basis:
            if repeat:
                circuit += detector_repeat_circuit
            else:
                circuit += detector_circuit

        # tick
        circuit.append("TICK")
        
        # Round 8
        circuit.append("MRX", x_ancillas)
        
        # X basis detector
        if not z_basis:
            if repeat:
                circuit += detector_repeat_circuit
            else:
                circuit += detector_circuit
        
        # tick
        circuit.append("TICK")

   
    circuit = stim.Circuit()
    circuit.append("RX", x_ancillas)
    circuit.append("R", z_ancillas)
    circuit.append("R" if z_basis else "RX", data)

    # begin round tick
    circuit.append("TICK") 
    append_blocks(circuit, repeat=False) # encoding round

    rep_circuit = stim.Circuit()
    append_blocks(rep_circuit, repeat=True)
    circuit += (rounds-1) * rep_circuit

    circuit.append("M" if z_basis else "MX", data)
      
    pcm = code.hz if z_basis else code.hx
    logical_pcm = code.lz if z_basis else code.lx
    stab_detector_circuit_str = "" # stabilizers
    for i, s in enumerate(pcm):
        nnz = np.nonzero(s)[0]
        det_str = "DETECTOR"
        for ind in nnz:
            det_str += f" rec[{-n+ind}]"       
        det_str += f" rec[{-n-n+i}]" if z_basis else f" rec[{-n-n//2+i}]"
        det_str += "\n"
        stab_detector_circuit_str += det_str
    stab_detector_circuit = stim.Circuit(stab_detector_circuit_str)
    circuit += stab_detector_circuit
        
    log_detector_circuit_str = "" # logical operators
    for i, l in enumerate(logical_pcm):
        nnz = np.nonzero(l)[0]
        det_str = f"OBSERVABLE_INCLUDE({i})"
        for ind in nnz:
            det_str += f" rec[{-n+ind}]"        
        det_str += "\n"
        log_detector_circuit_str += det_str
    log_detector_circuit = stim.Circuit(log_detector_circuit_str)
    circuit += log_detector_circuit

    return circuit

def _get_code(distance: int):
    if distance == 6: # [[72,12,6]]
        return _create_bivariate_bicycle_codes(6, 6, [3], [1,2], [1,2], [3])
    elif distance == 10: # [[108,8,10]]
        return _create_bivariate_bicycle_codes(9, 6, [3], [1,2], [1,2], [3])
    elif distance == 12: # [[144,12,12]]
        return _create_bivariate_bicycle_codes(12, 6, [3], [1,2], [1,2], [3])

def build_circuit(
        distance: int, 
        num_rounds: int, 
        basis: PauliXZ
) -> stim.Circuit:

    code, A_list, B_list = _get_code(distance)
    z_basis = True if basis == Pauli.Z else False

    return _build_circuit(code, A_list, B_list, 
                          num_rounds, z_basis=z_basis)