"""The QEC clock cycle with its classical loop: the model, and the two pages that show it.

The arithmetic is small; what these tests hold down is the *meaning*:

* the two modes are different clocks -- storing a correction in the classical memory costs
  the ions nothing, feeding it back costs the reaction time;
* the backlog verdict flips on the round, not on the decoder: the same GPU decoder keeps up
  with a millisecond QCCD round and falls behind a microsecond superconducting one;
* one source of truth -- the gadget layer's place latencies, the leaderboard's columns and
  the studio's panel all read `qccd.analysis.feedback`, so they cannot drift apart;
* the round time is never invented: a report refuses to exist without a measured one.
"""

from __future__ import annotations

import json

import pytest

from qccd.analysis.analyses import get_analysis
from qccd.analysis.feedback import (DECODERS, LINK, MODES, ROUND_REFERENCE, cycle_report,
                                    decoder_profile)

QCCD_ROUND_US = 31000.0        # one verified d = 3 surface-code round (se_d3.se.1)
SC_ROUND_US = 1.0              # one superconducting round, for contrast


# --------------------------------------------------------------------------- the model


def test_the_stages_add_up_and_the_two_modes_are_different_clocks():
    store = cycle_report(QCCD_ROUND_US, decoder="lut", mode="store")
    react = cycle_report(QCCD_ROUND_US, decoder="lut", mode="react")
    # store: the ions never wait, so the cycle IS the round
    assert store["cycle_us"] == pytest.approx(QCCD_ROUND_US)
    assert store["classical_us"] == pytest.approx(
        LINK["readout_to_control_us"] + DECODERS["lut"]["latency_us"] + LINK["frame_write_us"])
    assert [s["stage"] for s in store["stages"]][0] == "syndrome extraction"
    assert sum(s["us"] for s in store["stages"][1:]) == pytest.approx(store["classical_us"])
    # react: the cycle carries the reaction, which includes getting the guard back
    assert react["cycle_us"] > store["cycle_us"]
    assert react["reaction_us"] == pytest.approx(
        store["classical_us"] + LINK["resolve_us"] + LINK["decision_us"])
    assert react["cycles_per_s"] < store["cycles_per_s"]
    assert react["mode_note"] == MODES["react"]


def test_the_backlog_verdict_turns_on_the_round_not_the_decoder():
    # the same decoder, two platforms: this is the comparison the boards make
    slow = cycle_report(QCCD_ROUND_US, decoder="gpu_bp", mode="store")
    fast = cycle_report(SC_ROUND_US, decoder="gpu_bp", mode="store")
    assert slow["keeps_up"] and not fast["keeps_up"]
    assert slow["decoder_margin"] > 1 and fast["decoder_margin"] < 1
    assert slow["backlog_per_round_us"] == 0
    assert fast["backlog_per_round_us"] == pytest.approx(
        DECODERS["gpu_bp"]["latency_us"] - SC_ROUND_US)
    assert "backlog" in [v for v in fast["verdicts"] if v["metric"] == "decoder_margin"][0]["why"]
    # and the break-even is stated, so an architect can read off the budget
    assert slow["slowest_decoder_us"] == pytest.approx(QCCD_ROUND_US)


def test_feedback_is_cheap_on_a_qccd_round_and_not_on_a_microsecond_one():
    q = cycle_report(QCCD_ROUND_US, decoder="lut", mode="react")
    s = cycle_report(SC_ROUND_US, decoder="lut", mode="react")
    assert q["reaction_fraction"] < 0.001        # under a tenth of a percent of a cycle
    assert s["reaction_fraction"] > 1            # more than a whole round
    cheap = [v for v in q["verdicts"] if v["metric"] == "reaction_fraction"][0]
    dear = [v for v in s["verdicts"] if v["metric"] == "reaction_fraction"][0]
    assert cheap["ok"] and not dear["ok"]


def test_a_report_refuses_to_invent_a_round_or_a_decoder():
    with pytest.raises(ValueError, match="measured round time"):
        cycle_report(0.0)
    with pytest.raises(ValueError):
        cycle_report(-5.0)
    with pytest.raises(KeyError, match="unknown decoder"):
        cycle_report(100.0, decoder="wishful")
    with pytest.raises(KeyError, match="unknown mode"):
        cycle_report(100.0, mode="hope")


def test_every_decoder_profile_says_where_its_number_comes_from():
    for name, d in DECODERS.items():
        assert d["source"] and d["note"] and d["limit"], name
        assert d["latency_us"] >= 0
        assert decoder_profile(name)["name"] == name
    # a software decoder walks the check graph, so its latency scales with the code
    assert decoder_profile("gpu_bp", detectors=160)["latency_us"] > \
           decoder_profile("gpu_bp", detectors=16)["latency_us"]
    assert decoder_profile("fpga_bp", detectors=160)["latency_us"] == \
           decoder_profile("fpga_bp")["latency_us"]
    assert all(r["source"] for r in ROUND_REFERENCE)


def test_the_report_is_json_able_and_keeps_its_provenance():
    r = cycle_report(QCCD_ROUND_US, decoder="fpga_bp", mode="react", rounds_per_decode=3)
    assert json.loads(json.dumps(r))["rounds_per_decode"] == 3
    # a window decoder still has to finish a window per round
    assert r["decoder"]["latency_us"] * 3 <= r["round_us"]
    assert any("measured" in n for n in r["notes"])


# --------------------------------------------------------------------------- one source of truth


def test_the_gadget_places_and_the_model_cannot_disagree():
    from qccd.gadget.logic.decode import LATENCY
    assert LATENCY["link_us"] == LINK["readout_to_control_us"]
    assert LATENCY["write_us"] == LINK["frame_write_us"]
    assert LATENCY["resolve_us"] == LINK["resolve_us"]
    assert LATENCY["decision_us"] == LINK["decision_us"]
    assert LATENCY["lookup_us"] == DECODERS["lut"]["latency_us"]


def test_the_analysis_wears_the_contract_and_sweeps():
    a = get_analysis("cycle")(round_us=QCCD_ROUND_US, decoder="lut", mode="react")
    d = a.run()
    assert d["keeps_up"] and d["cycle_us"] > d["round_us"]
    assert set(get_analysis("cycle").data_labels) <= set(d)
    with pytest.raises(KeyError):
        get_analysis("cycle")(decodr="lut")          # a typo must not be ignored
    # the architect's question: how slow may my decoder be?
    sweep = a.sweep("decoder", ["lut", "fpga_bp", "gpu_bp"])
    assert [p.data["keeps_up"] for p in sweep.points] == [True, True, True]
    sweep = get_analysis("cycle")(round_us=SC_ROUND_US).sweep(
        "decoder", ["lut", "fpga_bp", "gpu_bp"])
    assert [p.data["keeps_up"] for p in sweep.points] == [True, True, False]


# --------------------------------------------------------------------------- the two pages


def test_the_board_block_and_section_state_the_cycle():
    from qccd.site import qec_cycle
    line = qec_cycle.board_block(55000.0)
    assert "QEC clock" in line and "55 ms" in line and "cycles/s" in line
    assert "margin" in line and 'href="#cycle"' in line
    assert qec_cycle.board_block(None) == ""
    boards = [{"id": "bb144", "title": "BB", "round_us": 55000.0},
              {"id": "rep9", "title": "Repetition", "round_us": 1990.0}]
    sec = qec_cycle.board_section(boards)
    assert 'id="cycle"' in sec and "<svg" in sec
    for b in boards:
        assert f'href="#{b["id"]}"' in sec
    # every decoder gets a column, and the superconducting yardstick a row
    for d in qec_cycle.SHOWN_DECODERS:
        assert DECODERS[d]["title"] in sec
    assert "superconducting" in sec and "1,000,000" in sec
    assert qec_cycle.board_section([]) == ""


def test_the_studio_panel_is_self_contained_and_carries_no_hardcoded_latency():
    from qccd.site import qec_cycle
    block = qec_cycle.studio_block()
    assert block.startswith("<style>") and "<script>" in block
    # the page must stay self-contained: the studio's own rule (render.py FORBIDDEN)
    for bad in ("fetch(", "<script src=", "<link ", "@import", "XMLHttpRequest"):
        assert bad not in block, bad
    assert "__PAYLOAD__" not in block
    # the latencies arrive as data, from the one table
    payload = qec_cycle.studio_payload()
    assert payload["link"] is LINK and payload["decoders"] is DECODERS
    assert str(LINK["readout_to_control_us"]) in block
    # and the panel reads the studio's own price rather than computing a round itself
    assert "EDITOR.price" in block
    assert 'data-embed' in block                  # no controls on the embedded examples
