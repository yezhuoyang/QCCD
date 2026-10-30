"""Goal 3's hypergraph-product code: the [[58,16,3]] product of the [7,4,3] Hamming code with itself.

    build_circuit(d, rounds, basis) -> stim.Circuit      # the interface of Alex's builders

The code is qLDPC's `HGPCode(HammingCode(3))` (Tillich and Zemor, arXiv:0903.0566): 58 data
qubits, 21 X and 21 Z checks of weight 5 to 7, one ancilla per check.  The circuit is qLDPC's
memory experiment with the coloration schedule of Tremblay, Delfosse and Beverland
(arXiv:2109.14609, Algorithm 1): the X checks' Tanner graph is edge-coloured and applied one
colour at a time, then the Z checks' the same way.  Every ancilla is prepared in |+> and read in
the X basis: an X check is CX(ancilla, data), a Z check CZ(ancilla, data).  The one change to
qLDPC's circuit is a TICK between colours, so that each colour is a layer of the circuit (qLDPC
applies them back to back).  No other HGP circuit was available (CODES.md); Alex may replace it.
"""

from __future__ import annotations

import collections

import networkx as nx
import stim
from qldpc import circuits, codes
from qldpc.circuits import EdgeColoringXZ
from qldpc.circuits.bookkeeping import MeasurementRecord, QubitIDs
from qldpc.objects import Pauli

#: distance -> the classical code whose product with itself is Goal 3's HGP code
CLASSICAL = {3: codes.HammingCode(3)}


def _layers(graph, qubit_ids: QubitIDs, strategy: str) -> stim.Circuit:
    """qLDPC's EdgeColoring.graph_to_circuit, with a TICK after each colour."""
    coloring = nx.greedy_color(nx.line_graph(graph.to_undirected()), strategy)
    by_colour: dict[int, list[tuple[str, int, int]]] = collections.defaultdict(list)
    for edge, colour in coloring.items():
        data_node, check_node = sorted(edge)
        gate = f"C{graph[check_node][data_node][Pauli]}"
        by_colour[colour].append((gate, qubit_ids.check[check_node.index], qubit_ids.data[data_node.index]))
    c = stim.Circuit()
    for colour in sorted(by_colour):
        for gate, check, data in sorted(by_colour[colour]):
            c.append(gate, [check, data])
        c.append("TICK")
    return c


class TickedXZ(EdgeColoringXZ):
    """EdgeColoringXZ (arXiv:2109.14609 Algorithm 1) with each colour its own layer."""

    def get_circuit(self, code, qubit_ids: QubitIDs | None = None):
        qubit_ids = qubit_ids or QubitIDs.from_code(code)
        c = stim.Circuit()
        c.append("RX", qubit_ids.check)
        c.append("TICK")
        c += _layers(code.graph_x, qubit_ids, self.strategy)
        c += _layers(code.graph_z, qubit_ids, self.strategy)
        c.append("MX", qubit_ids.check)
        return c, MeasurementRecord({q: [m] for m, q in enumerate(qubit_ids.check)})


def code(d: int = 3) -> codes.HGPCode:
    return codes.HGPCode(CLASSICAL[d])


def build_circuit(d: int, rounds: int, basis) -> stim.Circuit:
    """The noiseless memory experiment in `basis` (qldpc.objects.Pauli.Z or .X), `rounds` rounds."""
    return circuits.get_memory_experiment(code(d), basis=basis, num_rounds=rounds,
                                          syndrome_measurement_strategy=TickedXZ())
