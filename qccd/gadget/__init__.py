"""Logical QCCD gadgets: black boxes with ion ports, composed hierarchically.

See docs/GADGETS.md.  A logical program (LogicQ text) is realised as operations on gadget
instances -- memory blocks, transversal-CNOT stations, messenger reservoirs, factories --
each compiled once per master into a verified TSIR and characterized into an abstract,
so a machine of 10^4 ions is scheduled over abstracts and replayed ion by ion only where
someone looks.
"""

from .codes import CSSCode, bivariate_bicycle, bare, parse_poly
from .logicq import LogicalProgram, Instruction, ParseError, parse, parse_file

__all__ = ["CSSCode", "bivariate_bicycle", "bare", "parse_poly",
           "LogicalProgram", "Instruction", "ParseError", "parse", "parse_file"]
