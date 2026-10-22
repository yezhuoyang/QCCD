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
     "The QCCD architecture studies whose cost tables, devices and results the leaderboard's tasks are measured against."),
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
    # The list is empty on purpose (2026-09-10): the papers of the project go here as they
    # appear.  An entry looks like this, and the `used` line says where the paper enters
    # the site:
    #
    # {"key": "ye2026", "group": "this",
    #  "title": "...", "authors": ["Zhuoyang Ye", "..."],
    #  "venue": "...", "year": 2026, "arxiv": "2609.xxxxx", "doi": "10.xxxx/xxxxx",
    #  "used": "..."},
]

#: How to cite the website and the tool while no paper about them is published.
SOFTWARE = {
    "note": "No paper about the tool has been published yet. Until there is one, cite the software and the website.",
    "bibtex": """@software{qccd,
  author = {Ye, Zhuoyang},
  title  = {QCCD: a fault-tolerant algorithm and trapped-ion architecture codesign tool},
  year   = {2026},
  url    = {https://github.com/yezhuoyang/QCCD},
  note   = {https://qccd.academy/}
}""",
}
