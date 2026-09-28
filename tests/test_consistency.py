"""Self-consistency helper tests: /cookbooks/consistency_noul_cookbook.

The helper re-asks a task n times and bands the per-head signal. The stub
providers here answer from fixed or scripted sequences, so bands and stats
are deterministic — the cookbook's bounds are illustrative, and so are these.
"""

from __future__ import annotations

import json

import pytest

import sys1
from sys1.types import Provider, Task, make_choice, make_noul


def _fixed_provider(pid: str, answers: dict) -> Provider:
    def _fn(text, heads, cfg, tele):
        return {h.id: dict(answers.get(h.id, {"_declined": "no-answer", "confidence": 0.0})) for h in heads}

    return Provider(id=pid, name=pid, classify=_fn, is_available=lambda c: True)


def _scripted_provider(pid: str, script: list[dict]) -> Provider:
    """Each classify draw pops the next answer map (last one repeats)."""
    draws = iter(script)

    def _fn(text, heads, cfg, tele):
        try:
            answers = next(draws)
        except StopIteration:
            answers = script[-1]
        return {h.id: dict(answers.get(h.id, {"_declined": "no-answer", "confidence": 0.0})) for h in heads}

    return Provider(id=pid, name=pid, classify=_fn, is_available=lambda c: True)


NOUL_TASK = Task(id="consistency_noul", heads=[make_noul("Is this a refund request?", id="refund")])


@pytest.fixture(autouse=True)
def _restore_registry():
    """These tests register stub providers freely; restore the registry after
    each test so tier=core stubs don't leak into later enabled-set tests."""
    before = dict(sys1.REGISTRY)
    yield
    sys1.REGISTRY.clear()
    sys1.REGISTRY.update(before)


@pytest.fixture
def cfg(tmp_path):
    c = sys1.load_config()
    c["classify"]["log_dir"] = str(tmp_path / "logs")
    return c


class TestNoulBanding:
    def test_confident_yes_bands_yes(self, cfg):
        sys1.register(_fixed_provider("p90", {"refund": {"type": "noul", "noul": 0.9}}))
        out = sys1.consistency_classify("p90", NOUL_TASK, "text", n=3, cfg=cfg, log=False)
        head = out["heads"]["refund"]
        assert head["samples"] == [0.9, 0.9, 0.9]
        assert head["mean"] == pytest.approx(0.9)
        assert head["stdev"] == pytest.approx(0.0)
        assert head["band"] == "yes"

    def test_inclusive_band_edges_are_uncertain(self, cfg):
        # 0.30 and 0.70 land inside the uncertain band per the cookbook
        for p, expected in ((0.29, "no"), (0.30, "uncertain"), (0.50, "uncertain"),
                            (0.70, "uncertain"), (0.71, "yes")):
            sys1.register(_fixed_provider(f"p{int(p*100)}", {"refund": {"type": "noul", "noul": p}}))
            out = sys1.consistency_classify(f"p{int(p*100)}", NOUL_TASK, "text", n=2, cfg=cfg, log=False)
            assert out["heads"]["refund"]["band"] == expected, f"p={p}"
        assert out["bounds"] == {"low": 0.30, "high": 0.70}

    def test_raw_probability_banded_not_derived_certainty(self, cfg):
        """A confident 'no' (p=0.05) bands 'no' — NOT 'yes' via max(p, 1-p).
        Consistency bands the raw signal; gating derives certainty separately."""
        sys1.register(_fixed_provider("p05", {"refund": {"type": "noul", "noul": 0.05}}))
        out = sys1.consistency_classify("p05", NOUL_TASK, "text", n=2, cfg=cfg, log=False)
        assert out["heads"]["refund"]["band"] == "no"

    def test_bool_noul_estimates_p_true_from_confidence(self, cfg):
        """Decide-shaped bool noul: confidence is the winning label's max
        probability, so P(true) = conf when true, 1 - conf when false."""
        sys1.register(_fixed_provider("pbool", {"refund": {"type": "noul", "noul": True, "confidence": 0.9}}))
        out = sys1.consistency_classify("pbool", NOUL_TASK, "text", n=2, cfg=cfg, log=False)
        assert out["heads"]["refund"]["samples"] == [0.9, 0.9]
        assert out["heads"]["refund"]["band"] == "yes"
        sys1.register(_fixed_provider("pboolf", {"refund": {"type": "noul", "noul": False, "confidence": 0.9}}))
        out = sys1.consistency_classify("pboolf", NOUL_TASK, "text", n=2, cfg=cfg, log=False)
        assert out["heads"]["refund"]["samples"] == [pytest.approx(0.1), pytest.approx(0.1)]
        assert out["heads"]["refund"]["band"] == "no"

    def test_bool_noul_without_confidence_scores_none(self, cfg):
        sys1.register(_fixed_provider("pboolnc", {"refund": {"type": "noul", "noul": True}}))
        out = sys1.consistency_classify("pboolnc", NOUL_TASK, "text", n=2, cfg=cfg, log=False)
        head = out["heads"]["refund"]
        assert head["samples"] == [None, None]
        assert head["mean"] is None and head["band"] is None


class TestChoiceAndSpread:
    def test_choice_bands_on_winner_max_probability(self, cfg):
        task = Task(id="consistency_choice", heads=[make_choice("Which?", ["a", "b", "c"], id="pick")])
        answer = {"type": "choice", "label": "b", "confidence": 0.55,
                  "probabilities": {"a": 0.2, "b": 0.6, "c": 0.2}}
        sys1.register(_fixed_provider("pc", {"pick": answer}))
        out = sys1.consistency_classify("pc", task, "text", n=3, cfg=cfg, log=False)
        head = out["heads"]["pick"]
        # winner's max probability (0.6), not the shape-statistic confidence (0.55)
        assert head["samples"] == [0.6, 0.6, 0.6]
        assert head["band"] == "uncertain"

    def test_spread_scripted_draws_produce_nonzero_stdev(self, cfg):
        script = [
            {"refund": {"type": "noul", "noul": 0.4}},
            {"refund": {"type": "noul", "noul": 0.6}},
            {"refund": {"type": "noul", "noul": 0.8}},
        ]
        sys1.register(_scripted_provider("pspread", script))
        out = sys1.consistency_classify("pspread", NOUL_TASK, "text", n=3, cfg=cfg, log=False)
        head = out["heads"]["refund"]
        assert head["samples"] == [0.4, 0.6, 0.8]
        assert head["stdev"] == pytest.approx(0.2, abs=1e-9)
        assert head["mean"] == pytest.approx(0.6)
        assert head["band"] == "uncertain"

    def test_declined_draws_counted_but_not_scored(self, cfg):
        script = [
            {"refund": {"type": "noul", "noul": 0.9}},
            {"refund": {"_declined": "no-answer", "confidence": 0.0}},
        ]
        sys1.register(_scripted_provider("pdecl", script))
        out = sys1.consistency_classify("pdecl", NOUL_TASK, "text", n=2, cfg=cfg, log=False)
        head = out["heads"]["refund"]
        assert head["samples"] == [0.9, None]
        assert head["mean"] == pytest.approx(0.9)  # mean over usable draws
        assert head["band"] == "yes"

    def test_all_unusable_yields_none_band(self, cfg):
        sys1.register(_fixed_provider("pgone", {}))  # every head declines
        out = sys1.consistency_classify("pgone", NOUL_TASK, "text", n=2, cfg=cfg, log=False)
        head = out["heads"]["refund"]
        assert head["band"] is None and head["mean"] is None


class TestContract:
    def test_n_one_degrades_gracefully(self, cfg):
        sys1.register(_fixed_provider("p1", {"refund": {"type": "noul", "noul": 0.9}}))
        out = sys1.consistency_classify("p1", NOUL_TASK, "text", n=1, cfg=cfg, log=False)
        head = out["heads"]["refund"]
        assert head["stdev"] is None  # undefined for a single draw
        assert head["band"] == "yes"

    def test_n_below_one_raises(self, cfg):
        with pytest.raises(ValueError):
            sys1.consistency_classify("p1", NOUL_TASK, "text", n=0, cfg=cfg, log=False)

    def test_log_true_emits_ordinary_records_per_draw(self, cfg, tmp_path):
        sys1.register(_fixed_provider("plog", {"refund": {"type": "noul", "noul": 0.9}}))
        sys1.consistency_classify("plog", NOUL_TASK, "text", n=3, cfg=cfg, log=True)
        logs = list((tmp_path / "logs").rglob("events.jsonl"))
        assert logs
        records = []
        with open(logs[0]) as f:
            records = [json.loads(line) for line in f]
        assert len(records) == 3  # n ordinary classify records, no new shape
        assert all(r["op"] == "classify" and r["task"] == "consistency_noul" for r in records)

    def test_task_instance_and_id_both_resolve(self, cfg):
        sys1.register(_fixed_provider("pboth", {"refund": {"type": "noul", "noul": 0.9}}))
        out = sys1.consistency_classify("pboth", NOUL_TASK, "text", n=1, cfg=cfg, log=False)
        assert out["task"] == "consistency_noul"
