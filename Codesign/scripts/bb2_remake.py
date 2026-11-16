"""Make one BB board entry's files under Compiler/build/bb2/ again, as the catalogue of
Codesign/scripts/bb_studio.py says they were made, and check the result against the board.

    QCCD_QCCDC=<released qccdc_cli.exe> python Codesign/scripts/bb2_remake.py <key> [timeout_s]

`Compiler/build/` is not in the repository, so on a fresh checkout `bb_studio.py` has nothing to
draw the twelve qccdc entries from.  This writes one entry's files there; run it for every key of
the catalogue that is not made by the catalogue itself, then `bb_studio.py`.  The released
compiler is what `qccd toolchain install` fetches (`~/.qccd/toolchain/qccdc/<version>/`).

The entry's COMMITTED page says what its machine is: the generator and its parameters, the
template, the release, and any control block taken from another template (the header of the
machine's text), and its description.  The machine is built from exactly that and entered on
bb144@2 (`board_device`).

The programme is compiled as `qccd.qec.compile_with_qccdc(mode="auto")` compiles, the
workspace's compile job -- export_arch, `qccdc rotate` on a device with a closed loop else
`qccdc compile`, insert_cooling at the rank table -- from `<key>.qasm`, so that it carries
the entry's name as the committed one does.  Its replayed time must equal the board's row.
"""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, ".")
from qccd import Machine
from qccd.arch.device import Architecture
from qccd.compile.programs import closed_loops
from qccd.cost.models import corrected_model
from qccd.ir.tsir import TSIR
from qccd.workspace.tasks import board_device, find_release

key = sys.argv[1]
timeout = float(sys.argv[2]) if len(sys.argv) > 2 else 1800.0
ROOT = Path(".").resolve()
BB2 = ROOT / "Compiler" / "build" / "bb2"
BRIDGE = ROOT / "Compiler" / "bridge"
QCCDC = os.environ["QCCD_QCCDC"]
TAG = '<script id="data" type="application/json">'
REF = os.environ.get("BB2_REF", "HEAD")
rows = {r["key"]: r for r in json.loads(subprocess.run(
    ["git", "show", f"{REF}:BBResults/studio/manifest.json"], capture_output=True, text=True, encoding="utf-8").stdout)}
row = rows[key]
t = subprocess.run(["git", "show", f"{REF}:BBResults/studio/{key}.html"], capture_output=True, text=True,
                   encoding="utf-8").stdout
d = json.loads(t[t.find(TAG) + len(TAG):t.find("</script>", t.find(TAG))])
a = d["arch"]

# ---- the machine, from what its page records
header = [x["text"] for x in a["listing"]["lines"] if x.get("kind") == "header"]
prov = {}
for line in header:
    body = line.lstrip("# ").strip()
    for k in ("template", "release", "control_from"):
        if body.startswith(k + ": "):
            v = body[len(k) + 2:]
            prov[k] = json.loads(v) if v.startswith("{") else v
gen, params = a["generator"], dict(a["params"])
doc = getattr(Machine, gen)(name=key, template=prov["template"], **params).arch.to_json()
if "control_from" in prov:
    src = json.loads((ROOT / "arch" / f"{prov['control_from']['template']}.arch.json").read_text(encoding="utf-8"))
    for block in prov["control_from"]["blocks"]:
        doc["control"][block] = src["control"][block]
doc["description"] = a["description"]
doc["provenance"] = prov
rel = find_release(prov["release"])
doc = board_device(doc, rel)
arch = Architecture.from_json(doc)
BB2.mkdir(parents=True, exist_ok=True)
arch_p = BB2 / f"{key}.arch.json"
arch_p.write_text(json.dumps(doc, indent=1) + "\n", encoding="utf-8", newline="\n")
qasm_p = BB2 / f"{key}.qasm"
qasm_p.write_text(rel.circuit_text(), encoding="utf-8", newline="\n")
if not row.get("prefix"):
    print(key, f"refused on the board: machine written ({prov})", flush=True)
    raise SystemExit(0)

# ---- the programme, as the workspace's compile job compiles it
prefix = BB2 / key


def run(cmd, left):
    return subprocess.run([str(x) for x in cmd], capture_output=True, text=True, timeout=left, cwd=BB2)


t0 = time.time()
expanded = BB2 / f"{key}.expanded.json"
cp = run([sys.executable, BRIDGE / "export_arch.py", arch_p, "-o", expanded], timeout)
assert cp.returncode == 0 and expanded.exists(), cp.stdout + cp.stderr
modes = ["rotate", "compile"] if closed_loops(arch) else ["compile"]
tsir, cert = Path(f"{prefix}.tsir.json"), Path(f"{prefix}.qcert.json")
used, log = None, ""
for m in modes:
    for stale in (tsir, cert):
        stale.unlink(missing_ok=True)
    cp = run([QCCDC, m, qasm_p, "--arch", expanded, "-o", prefix], timeout - (time.time() - t0))
    log += f"--- qccdc_cli {m} (exit {cp.returncode})\n" + (cp.stdout + "\n" + cp.stderr)[-2000:]
    if cp.returncode == 0 and tsir.exists() and cert.exists():
        if not json.loads(cert.read_text(encoding="utf-8")).get("unrealised"):
            used = m
            break
assert used, log[-1500:]
t_compile = time.time() - t0
cooled = Path(f"{prefix}.cooled.tsir.json")
cp = run([sys.executable, BRIDGE / "insert_cooling.py", tsir, "--arch", arch_p, "-o", cooled, "--table",
          "qccdsim_jones"], 1800)
assert cp.returncode == 0 and cooled.exists(), cp.stdout + cp.stderr
Path(f"{prefix}.compile.json").write_text(json.dumps(
    {"mode": used, "compile_seconds": round(t_compile, 1), "compiler": Path(QCCDC).parent.name,
     "made": "again on 2026-11-10 from the machine the entry's page records"}), encoding="utf-8")
prog = TSIR.load(str(cooled))
r = Machine(arch).run(prog, model=corrected_model("qccdsim_jones"))
want_t, want_n = (row.get("numbers") or {}).get("T_jones"), row.get("instructions")
same = abs(r.runtime_ms - (want_t or -1)) < 1e-6 and prog.name == d["program"]["name"]
print(key, f"qccdc {used} in {t_compile:.0f} s | programme {prog.name!r} (page {d['program']['name']!r})"
      f" | T_jones {r.runtime_ms:.3f} want {want_t} | instructions {len(prog.instructions)} (+1 decode = {want_n})"
      f" | rules {r.rules_failed}", "| SAME" if same else "| DIFFERENT", flush=True)
