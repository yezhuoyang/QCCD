"""Files out of the workspace: a design's or a run's files, where the person can open them.

The agent in Studio reads files but writes none of its own unless the person allows it
(`settings()["agent_files"]`, the chat's "Let the agent save files in this folder"), so saving a
result goes through the service, like every other change: `export_files` writes into a NEW folder
`<workspace>/exports/<name>/` and nowhere else.  An export never replaces an earlier one -- the
same name again makes `<name>-2/` -- and never touches the database or the design's own files.

What goes in:

- a DESIGN: `<name>.studio.json` (the Studio document, which Studio can open again with
  `qccd import`), `<name>.arch.json` (the architecture document the compiler reads), and, when
  the design has a compiled program, that program (cooled), the certified program and the
  compiler's certificate;
- a RUN: the device it ran on, the circuit (OpenQASM), the compiled program (cooled, as replayed),
  the certified program, the certificate, each step's start time, and the performance report.

Each folder has a README.txt that says what every file is.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Mapping

__all__ = ["ExportsMixin", "EXPORT_DIR"]

EXPORT_DIR = "exports"
_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")


def _slug(text: str | None, fallback: str) -> str:
    s = _UNSAFE.sub("-", str(text or "").strip()).strip("-.")[:60].strip("-.")
    return s or fallback


def _json(obj) -> bytes:
    return (json.dumps(obj, indent=1, ensure_ascii=False) + "\n").encode("utf-8")


class ExportsMixin:
    """`Workspace.export_files`: the one way files leave the workspace for the person."""

    def export_files(self, what: str, actor: Mapping, *, design: str | None = None, revision: int | None = None,
                     run_id: str | None = None, name: str | None = None) -> dict:
        from .core import WorkspaceError
        if what == "design":
            files, label = self._design_export(design, revision)
        elif what == "run":
            if not run_id:
                raise WorkspaceError("bad_request", "name the run to save (run_id)", status=422)
            files, label = self._run_export(run_id)
        else:
            raise WorkspaceError("bad_request", "what is 'design' or 'run'", status=422)
        folder = self._new_export_folder(_slug(name, "") or label)
        lines = [f"{label}, saved by QCCD from the workspace {self.root.name}.", ""]
        written = []
        for fname, data, about in files:
            p = folder / fname
            p.write_bytes(data)
            written.append({"file": p.relative_to(self.root).as_posix(), "bytes": len(data), "about": about})
            lines.append(f"{fname}: {about}")
        (folder / "README.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
        rel = folder.relative_to(self.root).as_posix()
        self.emit("export.written", {"folder": rel, "what": what, "files": [w["file"] for w in written],
                                     "actor": dict(actor)})
        return {"folder": rel, "path": str(folder), "files": written,
                "note": f"saved {len(written)} files in {rel}/ (with a README.txt saying what each is)"}

    # -- what goes in ------------------------------------------------------------

    def _design_export(self, design: str | None, revision: int | None) -> tuple[list, str]:
        branch = self.resolve_draft(design)
        b = self.branch(branch)
        rev = self.revision(branch, revision if revision is not None else b["head"])
        title = self.design_title(b)
        stem = _slug(title, "design")
        doc = self.design_document(branch, rev.revision)
        arch = self.replayed(branch, rev.revision).arch_doc or {}
        files = [(f"{stem}.studio.json", _json(doc),
                  f"the design '{title}' at revision {rev.revision} as a Studio document "
                  f"(open it again with `qccd import {stem}.studio.json`)"),
                 (f"{stem}.arch.json", _json(arch),
                  "the architecture document the compiler and the checker read")]
        fp = rev.state.final_program or {}
        for key, fname, about in (("artifact", "program.cooled.tsir.json", "its compiled program, with cooling inserted"),
                                  ("certified", "program.tsir.json", "its compiled program as the compiler certified it"),
                                  ("certificate", "program.qcert.json", "the compiler's certificate for that program")):
            if fp.get(key):
                files.append((fname, self.get_artifact(fp[key]), about))
        return files, f"{stem}-r{rev.revision}"

    def _run_export(self, run_id: str) -> tuple[list, str]:
        run = self.run(run_id)
        arts = run.get("artifacts") or {}
        prog = (run.get("program") or {}).get("name") or "program"
        des = run.get("design") or {}
        dtitle = self._design_title_of(des.get("draft") or "main")
        files = []
        for key, fname, about in (("device", "device.arch.json", "the device the run compiled onto"),
                                  ("circuit", "circuit.qasm", "the circuit (OpenQASM 2.0)"),
                                  ("program", "program.cooled.tsir.json",
                                   "the compiled program with cooling inserted: what the run replayed"),
                                  ("certified", "program.tsir.json", "the compiled program as the compiler certified it"),
                                  ("certificate", "program.qcert.json", "the compiler's certificate"),
                                  ("times", "step_times.json", "when each step of the program starts, in microseconds")):
            if arts.get(key):
                files.append((fname, self.get_artifact(arts[key]), about))
        report = {"program": run.get("program"), "design": des, "compiler": run.get("compiler"),
                  "performance": run.get("performance"), "note": run.get("note")}
        files.append(("report.json", _json(report),
                      "the run's performance report: the round time, where the time goes, hotspots and rules"))
        return files, _slug(f"{prog}-on-{dtitle}-r{des.get('revision', '')}", "run")

    def _new_export_folder(self, stem: str) -> Path:
        base = self.root / EXPORT_DIR
        base.mkdir(parents=True, exist_ok=True)
        for i in range(1, 1000):
            p = base / (stem if i == 1 else f"{stem}-{i}")
            try:
                p.mkdir()
                return p
            except FileExistsError:
                continue
        from .core import WorkspaceError
        raise WorkspaceError("too_many", f"exports/{stem} has 999 copies already", status=409)
