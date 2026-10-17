"""Memory-X probes use the identical CX layers of the memory-Z sources."""
from pathlib import Path
import subprocess
import sys

import pytest
import stim


@pytest.mark.parametrize("distance", [2, 3, 6, 12])
def test_generated_memory_x_has_identical_source_cx_layers(distance):
    root = Path(__file__).resolve().parents[1] / "Reproduce/jones2025/circuits"
    generated = stim.Circuit.generated("surface_code:rotated_memory_x",
                                       distance=distance, rounds=1)
    z = stim.Circuit((root / f"rotated_memory_z_d{distance}_r1.stim").read_text())
    x = stim.Circuit((root / f"rotated_memory_x_d{distance}_r1.stim").read_text())
    assert [ins for ins in generated if ins.name == "CX"] == [ins for ins in z if ins.name == "CX"]
    assert x == generated


@pytest.mark.parametrize("mode,suffix,distance,result,exit_code", [
    ("split", "", 3, "PASS", 0),
    ("shared_shape", "_shared_shape", 2, "FAIL", 1),
])
def test_memory_x_exposes_shared_shape_hook_errors(mode, suffix, distance, result, exit_code):
    root = Path(__file__).resolve().parents[1]
    base = root / "Reproduce/jones2025"
    run = subprocess.run([
        sys.executable, str(root / "tools/order.py"),
        str(base / f"ours/grid_rot_d3_c2_wise{suffix}.schedule.json.gz"),
        str(base / "circuits/rotated_memory_x_d3_r1.stim"),
    ], text=True, capture_output=True)
    assert run.returncode == exit_code, run.stdout + run.stderr
    assert "source  : detectors deterministic; circuit distance = 3" in run.stdout
    assert f"executed: detectors deterministic; circuit distance = {distance}" in run.stdout
    assert f"RESULT: {result}" in run.stdout
