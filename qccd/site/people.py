"""The People page: who takes part in the project.  Pure data; edit the lists.

Every entry is one card on `site/people/`.  `role` is one line, `about` one or two
sentences, `links` a mapping of label to URL, `photo` a file in `static/people/` (a
square head-and-shoulders crop; a card without one shows `static/people/blank.svg`).  Add a
person by adding a dict; groups are rendered in the order of GROUPS, and a person whose
`group` names none of them is shown under the last one.  The institutions are the footer's
leads and funders, then the places the project's contributors are based.

Photos: John's is his own; Jens Palsberg's is from his UCLA Samueli profile, Hartmut Häffner's
and Ke Sun's from the Häffner group's members page (ions.berkeley.edu/members).
"""

from __future__ import annotations

GROUPS = (
    ("team", "PhD Students and Postdocs", "The people who build the compiler, the verifier, the studio and this website."),
    ("faculty", "Leading Faculty", "The faculty who lead the collaboration."),
    ("collaborators", "Collaborators and advisors", "The physicists and computer scientists the design questions are worked out with."),
    ("alumni", "Alumni", "Earlier contributors."),
)

PEOPLE: list[dict] = [
    {
        "name": "Jens Palsberg",
        "role": "Professor of Computer Science",
        "affiliation": "University of California, Los Angeles",
        "group": "faculty",
        "about": "Professor and former chair of Computer Science at UCLA, working on programming "
                 "languages, software engineering and quantum computing.",
        "links": {"Homepage": "https://web.cs.ucla.edu/~palsberg/"},
        "photo": "jens-palsberg.jpg",
    },
    {
        "name": "Hartmut Häffner",
        "role": "Professor of Physics",
        "affiliation": "University of California, Berkeley",
        "group": "faculty",
        "about": "Leads the trapped-ion group at UC Berkeley, whose recent work includes "
                 "ultra-low-noise and 3D-printed ion traps, control of a quantum rotor and "
                 "trapped electrons.",
        "links": {"Group": "https://ions.berkeley.edu/"},
        "photo": "hartmut-haffner.jpg",
    },
    {
        "name": "John Zhuoyang Ye",
        "role": "Lead developer",
        "affiliation": "University of California, Los Angeles",
        "group": "team",
        "about": "Builds the compiler, the Lean-checked verifier, the studio and this website, "
                 "and runs the architecture studies behind the leaderboard.",
        "links": {"GitHub": "https://github.com/yezhuoyang"},
        "photo": "john-zhuoyang-ye.jpg",
    },
    {
        "name": "Ke Sun",
        "role": "Team Lead",
        "affiliation": "University of California, Berkeley",
        "group": "team",
        "about": "I originated the project idea within the NQVL FTL project to design a compiler "
                 "mapping user-level quantum circuits down to QCCD ion-trap hardware. Working "
                 "alongside students across QEC, compilation, and hardware engineering, we "
                 "developed a collaborative web platform where I focused on defining ion movement "
                 "and gate primitives, analyzing fabrication constraints, and exploring 3D "
                 "shuttling concepts.",
        "links": {},
        "photo": "ke-sun.jpg",
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
    {
        "name": "Jaewon",
        "role": "Contributor",
        "affiliation": "University of California, Berkeley",
        "group": "team",
        "about": "Reran our schedules for the Jones et al. layouts and found that they did not keep "
                 "each qubit's gate order, which is why the compiler now never reorders an input "
                 "circuit; checks the reproduced numbers against the papers.",
        "links": {},
    },
    {
        "name": "Alex",
        "role": "Contributor",
        "affiliation": "University of Michigan",
        "group": "team",
        "about": "Reads the Compilation and Reproduce pages line by line and asks what each term "
                 "means: which checks ran, how capacity and fidelity are defined, and what the "
                 "instructions look like written out in full.",
        "links": {},
    },
    {
        "name": "Yuewen",
        "role": "Contributor",
        "affiliation": "University of Michigan",
        "group": "team",
        "about": "Yuewen Hou is a third-year Ph.D. student at the University of Michigan. His research "
                 "focuses on quantum error correction, classical simulation of quantum programs, "
                 "and quantum algorithms.",
        "links": {},
    },
    {
        "name": "Jack",
        "role": "Contributor",
        "affiliation": "Cornell University",
        "group": "team",
        "about": "Read the Compilation page as a newcomer and asked where its terms come from, "
                 "whether the input is standard OpenQASM and what the dependency graph is, which "
                 "the page now explains.",
        "links": {},
    },
]

#: Who leads, who funds, and where the contributors are based -- in that order on the page.
#: `logo` and `url` are both optional: an entry without a logo is a text card, and one without
#: a url is not a link.  The contributor institutions are here because someone from each was
#: invited to the project, read off their invitation's email domain; being listed says a
#: contributor is based there, not that the institution is a partner.
#:
#: Every mark is the institution's own public-domain wordmark from Wikimedia Commons -- the
#: same source and licence as the original UCLA and Berkeley marks -- and deliberately not a
#: seal or an athletics logo: `University_of_Arizona_logo.svg`,
#: `University_of_Michigan_wordmark.svg`, `University_of_California,_San_Diego_logo.svg`,
#: `Cornell_University_logo.svg`, each licence read on its Commons page before use.
INSTITUTIONS = (
    # first: the project QCCD studio grew out of (Hartmut Haeffner, 2026-10-21: "it was triggered
    # mostly by FTL, so maybe we should put this a bit more front and center")
    {"name": "FTL: Accelerating Fault-Tolerant Quantum Logic", "short": "NQVL FTL",
     "url": "https://www.samueli.ucla.edu/ucla-led-team-selected-for-national-science-foundation-quantum-technology-award/",
     "what": "The project QCCD studio grew out of, and its funder: the UCLA-led project of the NSF National "
             "Quantum Virtual Laboratory that is designing a fault-tolerant trapped-ion (QCCD) computer "
             "with 60 logical qubits."},
    {"name": "University of California, Los Angeles", "short": "UCLA", "url": "https://www.ucla.edu/",
     "logo": "ucla.svg", "what": "Leads the project."},
    {"name": "University of California, Berkeley", "short": "UC Berkeley", "url": "https://www.berkeley.edu/",
     "logo": "berkeley.svg", "what": "Partner in the collaboration."},
    {"name": "Challenge Institute for Quantum Computation", "short": "CIQC", "url": "https://ciqc.berkeley.edu/",
     "logo": "ciqc.png", "what": "Funds the work, as an NSF Quantum Leap Challenge Institute."},
    {"name": "University of Arizona", "short": "Arizona", "url": "https://www.arizona.edu/",
     "logo": "arizona.svg", "what": "A contributor is based here."},
    {"name": "University of Michigan", "short": "Michigan", "url": "https://umich.edu/",
     "logo": "michigan.svg", "what": "Contributors are based here."},
    {"name": "University of California, San Diego", "short": "UC San Diego", "url": "https://ucsd.edu/",
     "logo": "ucsd.svg", "what": "A contributor is based here."},
    {"name": "Cornell University", "short": "Cornell", "url": "https://www.cornell.edu/",
     "logo": "cornell.svg", "what": "A contributor is based here."},
)

#: How to be listed: the sentence on the page, so the process is written down once.
JOIN = ("To be listed here, add yourself in a pull request, or open a thread on the Discuss "
        "page. Contributors to the code are also on GitHub's contributor graph.")
