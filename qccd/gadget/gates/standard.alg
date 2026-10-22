# The standard gates: each written from verified places in the algorithm language, each
# proved by the contract checker to be the gate it says it implements, in every branch of
# every measurement it makes, on the states the places are verified to produce.  A program
# uses them with
#
#   include "standard"
#
# The two π/4 rotations, `s` (S̄) and `sx` (√X̄), are instructions, not gates here: each
# consumes a |Ȳ⟩ block and conjugates the block's Pauli frame, which only the scheduler
# can do.  Everything else is built from them, the transversal CNOT and the factory.

gate sdg q implements Sdg:      # S̄† = Z̄ S̄; the Z̄ is a line in the archive
  s q
  z q

gate sxdg q implements SXdg:    # √X̄† = X̄ √X̄
  sx q
  x q

gate h q implements H:          # H = S·√X·S, up to a global phase
  s q
  sx q
  s q

gate t q implements T:          # gate teleportation through a magic block
  inject m                      # the factory's T†|+̄⟩ ...
  x m                           # ... is T|+̄⟩ once an X̄ is in its frame
  zz q m -> a                   # Z̄Z̄ between the data and the magic block
  read m X -> b
  decode q m                    # both corrections read a decoded outcome
  if a: s q                     # the Clifford correction runs only when a = 1
  if b: z q                     # the Pauli correction is a frame

gate tdg q implements Tdg:      # the factory's T†|+̄⟩ teleported as it is
  inject m
  zz q m -> a
  read m X -> b
  decode q m
  if a: sdg q                   # S̄† when a = 1
  if b: z q

gate cz a b implements CZ:      # CZ = (1 ⊗ H) CX (1 ⊗ H)
  h b
  cx a b
  h b

gate ccx a b c implements CCX:  # Toffoli: the standard seven-T circuit (Nielsen & Chuang, fig. 4.9)
  h c
  cx b c
  tdg c
  cx a c
  t c
  cx b c
  tdg c
  cx a c
  t b
  t c
  h c
  cx a b
  t a
  tdg b
  cx a b

gate cswap c x y implements CSWAP:   # Fredkin: a Toffoli between two CNOTs
  cx y x
  ccx c x y
  cx y x
