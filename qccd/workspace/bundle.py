"""Submission bundles: the exact, immutable artifacts a grade is about.

A bundle is a directory (or a deterministic zip of one) holding

    manifest.json            kind qccd.submission_bundle v1: task, files + digests, provenance
    device.arch.json         the normalized, expanded architecture       (authoritative device)
    program.tsir.json        the final hardware program that is graded
    certified.tsir.json      the program the certificate speaks about (the compiler's output)
    certificate.qcert.json   the compiler's certificate
    presentation.json        optional: title, description, the studio document for display

Every file is canonical JSON (sorted keys, one number spelling), so the same design
always makes the same bytes and the same digest.  The BUNDLE DIGEST is the digest of the
manifest, and the manifest pins every file's sha256 -- so one digest names the whole
submission.  Nothing else goes in: no optimizer source, no conversation, no database, no
credentials.

`read_bundle` is the only way a bundle is opened by the evaluator, locally or on the
official server: it re-hashes every file, refuses unknown or missing members, and (for
an archive) refuses absolute paths, `..`, links, duplicate names and oversize members
before extracting anything.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import stat
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Mapping

from .jsonsafe import Limits, canonical_bytes, digest, normalize_numbers, strict_loads

__all__ = ["BUNDLE_FILES", "REQUIRED_FILES", "BundleError", "Bundle", "write_bundle",
           "read_bundle", "archive_bundle", "extract_archive"]

BUNDLE_KIND = "qccd.submission_bundle"
BUNDLE_FILES = {
    "device": "device.arch.json",
    "program": "program.tsir.json",
    "certified_program": "certified.tsir.json",
    "certificate": "certificate.qcert.json",
    "presentation": "presentation.json",
}
REQUIRED_FILES = ("device", "program", "certified_program", "certificate")
MAX_FILE = 48 * 1024 * 1024
MAX_TOTAL = 64 * 1024 * 1024
_FILE_LIMITS = Limits(max_bytes=MAX_FILE, max_depth=64, max_items=5_000_000, max_string=2_000_000)


class BundleError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _sha(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


@dataclass
class Bundle:
    root: Path
    manifest: dict
    docs: dict            # role -> parsed JSON

    @property
    def digest(self) -> str:
        return digest(self.manifest)

    def file_digests(self) -> dict:
        return {k: v["digest"] for k, v in self.manifest["files"].items()}


def write_bundle(out_dir: Path, *, task_id: str, task_digest: str, device: Mapping,
                 program: Mapping, certified_program: Mapping | None, certificate: Mapping | None,
                 presentation: Mapping | None = None, provenance: Mapping | None = None) -> Bundle:
    """Write canonical files and the manifest.  `out_dir` must not exist yet."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=False)
    files: dict = {}
    docs = {"device": device, "program": program, "certified_program": certified_program,
            "certificate": certificate, "presentation": presentation}
    for role, doc in docs.items():
        if doc is None:
            continue
        data = canonical_bytes(normalize_numbers(doc))
        name = BUNDLE_FILES[role]
        (out / name).write_bytes(data)
        files[role] = {"path": name, "digest": _sha(data), "bytes": len(data)}
    manifest = {"kind": BUNDLE_KIND, "version": 1,
                "task": {"id": task_id, "digest": task_digest},
                "files": files, "provenance": dict(provenance or {})}
    (out / "manifest.json").write_bytes(canonical_bytes(manifest))
    return read_bundle(out)


def read_bundle(root: Path, *, require: tuple = REQUIRED_FILES) -> Bundle:
    root = Path(root)
    mpath = root / "manifest.json"
    if not mpath.is_file():
        raise BundleError("no_manifest", "the bundle has no manifest.json")
    try:
        man = strict_loads(mpath.read_bytes(), Limits(max_bytes=256 * 1024))
    except ValueError as exc:
        raise BundleError("bad_manifest", f"manifest.json: {exc}") from None
    if man.get("kind") != BUNDLE_KIND or man.get("version") != 1:
        raise BundleError("bad_manifest", "manifest is not a qccd.submission_bundle v1")
    extra = set(man) - {"kind", "version", "task", "files", "provenance"}
    if extra:
        raise BundleError("bad_manifest", f"unknown manifest fields {sorted(extra)}")
    files = man.get("files")
    if not isinstance(files, dict):
        raise BundleError("bad_manifest", "manifest.files must be an object")
    unknown = set(files) - set(BUNDLE_FILES)
    if unknown:
        raise BundleError("bad_manifest", f"unknown bundle members {sorted(unknown)}")
    missing = [r for r in require if r not in files]
    if missing:
        raise BundleError("missing_file", f"the bundle lacks {missing}")
    task = man.get("task") or {}
    if not isinstance(task.get("id"), str) or not isinstance(task.get("digest"), str):
        raise BundleError("bad_manifest", "manifest.task must name the task id and digest")
    docs: dict = {}
    total = 0
    for role, ref in files.items():
        if not isinstance(ref, dict) or ref.get("path") != BUNDLE_FILES[role]:
            raise BundleError("bad_manifest", f"{role}: path must be {BUNDLE_FILES[role]}")
        p = root / ref["path"]
        if p.is_symlink() or not p.is_file():
            raise BundleError("missing_file", f"{ref['path']} is missing or not a regular file")
        data = p.read_bytes()
        total += len(data)
        if len(data) > MAX_FILE or total > MAX_TOTAL:
            raise BundleError("too_large", f"{ref['path']} makes the bundle too large")
        if _sha(data) != ref.get("digest"):
            raise BundleError("digest_mismatch", f"{ref['path']} does not match its manifest digest")
        try:
            docs[role] = strict_loads(data, _FILE_LIMITS)
        except ValueError as exc:
            raise BundleError("bad_json", f"{ref['path']}: {exc}") from None
    present = {p.name for p in root.iterdir()}
    stray = present - {"manifest.json"} - {r["path"] for r in files.values()}
    if stray:
        raise BundleError("stray_file", f"the bundle holds files the manifest does not name: {sorted(stray)[:5]}")
    return Bundle(root=root, manifest=man, docs=docs)


def archive_bundle(b: Bundle) -> bytes:
    """A deterministic zip: sorted members, fixed timestamps, no extra attributes."""
    buf = io.BytesIO()
    names = ["manifest.json"] + sorted(r["path"] for r in b.manifest["files"].values())
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as z:
        for n in names:
            info = zipfile.ZipInfo(n, date_time=(1980, 1, 1, 0, 0, 0))
            info.external_attr = (0o644 << 16)
            info.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(info, (b.root / n).read_bytes())
    return buf.getvalue()


def extract_archive(data: bytes, dest: Path, *, max_members: int = 16) -> Bundle:
    """Unpack an uploaded archive safely, then `read_bundle` it."""
    if len(data) > MAX_TOTAL:
        raise BundleError("too_large", "the archive is too large")
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise BundleError("bad_archive", "not a zip archive") from None
    infos = z.infolist()
    if len(infos) > max_members:
        raise BundleError("bad_archive", "too many archive members")
    allowed = {"manifest.json", *BUNDLE_FILES.values()}
    seen = set()
    total = 0
    for i in infos:
        name = i.filename
        pp = PurePosixPath(name)
        if name in seen:
            raise BundleError("bad_archive", f"duplicate member {name!r}")
        seen.add(name)
        if (pp.is_absolute() or ".." in pp.parts or "\\" in name or ":" in name
                or len(pp.parts) != 1 or name not in allowed):
            raise BundleError("unsafe_path", f"archive member {name!r} is not an allowed bundle file")
        mode = (i.external_attr >> 16) & 0o170000
        if mode and not stat.S_ISREG(mode):
            raise BundleError("unsafe_path", f"archive member {name!r} is not a regular file")
        if i.file_size > MAX_FILE:
            raise BundleError("too_large", f"{name} is too large")
        total += i.file_size
        if total > MAX_TOTAL:
            raise BundleError("too_large", "the archive expands past the limit")
        if i.compress_size and i.file_size / max(1, i.compress_size) > 200:
            raise BundleError("bad_archive", f"{name} has a suspicious compression ratio")
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=False)
    for i in infos:
        with z.open(i) as src:
            data_i = src.read(MAX_FILE + 1)
        if len(data_i) > MAX_FILE:
            raise BundleError("too_large", f"{i.filename} is too large")
        target = dest / i.filename
        fd = os.open(str(target), os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), 0o644)
        with os.fdopen(fd, "wb") as f:
            f.write(data_i)
    return read_bundle(dest)
