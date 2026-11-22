"""The Publications page: the papers this website is built on, and how to cite it.

Pure data; edit the lists.  Every entry is one card on `site/publications/`, grouped by
`group` in the order of GROUPS.  `used` is the one line saying where the paper enters the
site: a cost table, a rule, a fidelity, a model.  `arxiv` and `doi` become links; `url`
is for anything else (with an optional `url_label`).  A paper written about the project
itself belongs in the group `this`; until there is one the page cites the software.
"""

from __future__ import annotations

GROUPS = (
    ("this", "From this project", "Papers about the tool and the studies run with it."),
    ("arch", "Architectures and cost tables",
     "The QCCD architecture studies whose cost tables, machines and results the leaderboard's tasks are measured against."),
    ("control", "Wiring and control",
     "Why the control plane is broadcast, and what a compiler for a broadcast-wired machine has to respect."),
    ("transport", "Transport, heating and junctions",
     "What moving an ion costs in time and quanta, and what happens at a junction."),
    ("hardware", "Processors and electrode design",
     "The measured processor the gate numbers come from, and the electrode model the fab view is checked against."),
    ("codes", "Codes and the QCCD proposal",
     "The code the tasks run a round of, and the paper that proposed the architecture."),
)

PUBS: list[dict] = [
    # The papers of the project, newest first.  `arxiv`/`doi` are added when they exist;
    # `used` says where the paper enters the site.
    {"key": "qccdcert", "group": "this",
     "title": "QCCDCert: Formally Verified Compilation for QCCD Co-Design",
     "authors": ["John Zhuoyang Ye", "Jens Palsberg", "Ke Sun", "Hartmut Häffner"],
     "venue": "Preprint", "year": 2026,
     "url": "../compilation/", "url_label": "the compiler, explained on this site",
     "used": "The compiler of this site: its hardware and rule programs, the event frame, the nine "
             "stages and the theorem Meaning ∧ Legality ∧ Time are the paper's, on the Compilation page "
             "and in the tutorial."},
]

#: How to cite the paper, the website and the tool.
SOFTWARE = {
    "note": "For the verified compiler and the co-design method, cite the QCCDCert paper; for the website "
            "and the design tool, cite the software.",
    "bibtex": """@misc{qccdcert,
  author = {Ye, John Zhuoyang and Palsberg, Jens and Sun, Ke and H{\\"a}ffner, Hartmut},
  title  = {{QCCDCert}: Formally Verified Compilation for {QCCD} Co-Design},
  year   = {2026},
  note   = {Preprint}
}

@software{qccd,
  author = {Ye, Zhuoyang},
  title  = {QCCD: a fault-tolerant algorithm and trapped-ion architecture codesign tool},
  year   = {2026},
  url    = {https://github.com/yezhuoyang/QCCD},
  note   = {https://qccd.academy/}
}""",
}
