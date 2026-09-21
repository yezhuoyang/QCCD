"""The People page: who takes part in the project.  Pure data; edit the lists.

Every entry is one card on `site/people/`.  `role` is one line, `about` one or two
sentences, `links` a mapping of label to URL.  Add a person by adding a dict; groups are
rendered in the order of GROUPS, and a person whose `group` names none of them is shown
under the last one.  The institutions are the footer's three, with the same marks.
"""

from __future__ import annotations

GROUPS = (
    ("team", "The team", "The people who build the compiler, the verifier, the studio and this website."),
    ("collaborators", "Collaborators and advisors", "The physicists and computer scientists the design questions are worked out with."),
    ("alumni", "Alumni", "Earlier contributors."),
)

PEOPLE: list[dict] = [
    {
        "name": "John Zhuoyang Ye",
        "role": "Lead developer",
        "affiliation": "University of California, Los Angeles",
        "group": "team",
        "about": "Builds the compiler, the Lean-checked verifier, the studio and this website, "
                 "and runs the architecture studies behind the leaderboard.",
        "links": {"GitHub": "https://github.com/yezhuoyang"},
    },
    {
        "name": "Ke Sun",
        "role": "Team Lead",
        "affiliation": "University of California, Berkeley",
        "group": "team",
        "about": "I originated the project idea within the NVQL FTL project to design a compiler "
                 "mapping user-level quantum circuits down to QCCD ion-trap hardware. Working "
                 "alongside students across QEC, compilation, and hardware engineering, we "
                 "developed a collaborative web platform where I focused on defining ion movement "
                 "and gate primitives, analyzing fabrication constraints, and exploring 3D "
                 "shuttling concepts.",
        "links": {},
    },
    {
        "name": "Zhuangzhuang Chen",
        "role": "Team member",
        "affiliation": "",
        "group": "team",
        "about": "Works through the studio's course as a first reader and reports what does not hold: "
                 "found that the CNOT exercise in B2 asks for a merge no single waveform can perform, "
                 "and that the compiler lesson's companion page was never published.",
        "links": {},
    },
]

INSTITUTIONS = (
    {"name": "University of California, Los Angeles", "short": "UCLA", "url": "https://www.ucla.edu/",
     "logo": "ucla.svg", "what": "Leads the project."},
    {"name": "University of California, Berkeley", "short": "UC Berkeley", "url": "https://www.berkeley.edu/",
     "logo": "berkeley.svg", "what": "Partner in the collaboration."},
    {"name": "Challenge Institute for Quantum Computation", "short": "CIQC", "url": "https://ciqc.berkeley.edu/",
     "logo": "ciqc.png", "what": "Funds the work, as an NSF Quantum Leap Challenge Institute."},
)

#: How to be listed: the sentence on the page, so the process is written down once.
JOIN = ("To be listed here, add yourself in a pull request, or open a thread on the Discuss "
        "page. Contributors to the code are also on GitHub's contributor graph.")
