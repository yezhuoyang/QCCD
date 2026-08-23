# QCCD


There are many different ways to design a Trapper Ion Architecture, among which common problem exists: Ions need to be shuttle one after another with finite Control, each site has finite Capacity.

Ion moves 1-dimensionally, so an analogy is the Swap gate in Super conducting qubit.
So, designing the best QCCD architecture, the best strategy to Move/Shuttle ions for some QLDPC code/Quantum Algorithm becomes a non-trivial problem.
Key problem: Each trap has finite capacity, so a filled trap can block the movement of another ions. Some there need to be some strategy to make space for other Ions. 
Among the most popular design are Grid QCCD (Ion Traps put in the Middle of the wire of a Grid)
This software provides a platform to Specify any Hardware Architecture, compile any QASM program, and evaluate the Performance.



In "ion_transport_deck_v3.pptx.pdf"

Page 13-15 indicated the primitive movement of the ions 
The limitations:
1. Only EXACT the same operations can be implemented at the same time (eg. page 15 case 2, step 1)
2. The vertical green dash lines in the visualization I showed (attached .html file) are shuttling lines. The number of them can be different, in the demo we set it as 24 and therefore we have 24 ancilla ions. The existence of the vertical lines will increase the number of junctions, therefore increase the cost. But too few vertical lines will increase the number of rotations needed for ESM, so it's a tradeoff.


# Plan

1. Make design plan and Scope
2. Design Syntax for Architecture
3. Design syntax for Hardware Control given fixed Architecture
4. Formalize the Rules given Hardware control Constraints
5. Design good Visualization for Human Verification.
6. Use all existing Architecture as Examples
7. Develop the first version of an optimized compilation from input QASM to Hardware program



# Near term goal

For BBCode [[144,12,12]], what is the best Architecture?

# Long term goal

What is the best Code + Architecture to demonstrate breakeven?
