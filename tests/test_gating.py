"""Gating / interpret tests: confidence floors, escalation, provider floors."""

from __future__ import annotations

import sys1
from sys1.gating import answer_confidence, effective_floor, floors_for, interpret


def _make_cfg(floor: float = 0.7) -> dict:
    cfg = sys1.load_config()
    cfg["classify"]["confidence_floor"] = floor
    return cfg


class TestInterpret:
    def test_all_high_confidence_passes(self):
        answers = {
            "decide": {
                "diff_type": {"type": "choice", "label": "feat", "confidence": 0.9},
                "risk_tier": {"type": "choice", "label": "low", "confidence": 0.85},
            }
        }
        verdict, escalated, _ = interpret(answers, _make_cfg())
        assert verdict == "pass"
        assert escalated is False

    def test_low_confidence_escalates(self):
        answers = {
            "decide": {
                "diff_type": {"type": "choice", "label": "feat", "confidence": 0.4},
                "risk_tier": {"type": "choice", "label": "low", "confidence": 0.9},
            }
        }
        verdict, escalated, _ = interpret(answers, _make_cfg(floor=0.7))
        assert verdict == "escalate"
        assert escalated is True

    def test_declined_escalates(self):
        answers = {"decide": {"diff_type": {"type": "choice", "label": None, "confidence": 0.95}}}
        _, escalated, _ = interpret(answers, _make_cfg())
        assert escalated is True

    def test_noul_false_not_escalated(self):
        """noul=false is a real answer; the label/None check must treat False as a label."""
        answers = {"jev": {"breaking_change": {"type": "noul", "noul": False, "confidence": 0.92}}}
        _, escalated, _ = interpret(answers, _make_cfg())
        assert escalated is False

    def test_noul_none_escalates(self):
        answers = {"jev": {"breaking_change": {"type": "noul", "noul": None, "confidence": 0.92}}}
        _, escalated, _ = interpret(answers, _make_cfg())
        assert escalated is True

    def test_meta_keys_skipped(self):
        answers = {
            "decide": {
                "_raw": "noise",
                "_unmatched:weird": {"label": "x", "confidence": 0.1},
                "diff_type": {"type": "choice", "label": "fix", "confidence": 0.8},
            }
        }
        _, escalated, _ = interpret(answers, _make_cfg())
        assert escalated is False

    def test_provider_specific_floor(self):
        """ModernBERT default floor is 0.55; 0.6 should pass there but fail Decide."""
        cfg = sys1.load_config()
        # explicit per-provider overrides
        cfg["classify"]["decide_confidence_floor"] = 0.7
        cfg["classify"]["modernbert_confidence_floor"] = 0.55
        answers = {
            "modernbert": {"diff_type": {"type": "choice", "label": "feat", "confidence": 0.6}},
        }
        _, esc_m, _ = interpret(answers, cfg)
        assert esc_m is False
        answers_d = {"decide": {"diff_type": {"type": "choice", "label": "feat", "confidence": 0.6}}}
        _, esc_d, _ = interpret(answers_d, cfg)
        assert esc_d is True

    def test_global_floor_propagates_to_defaults(self):
        cfg = _make_cfg(floor=0.4)
        # A conf of 0.5 should now pass for any provider (no per-provider overrides set)
        answers = {"jev": {"diff_type": {"type": "choice", "label": "x", "confidence": 0.5}}}
        _, esc, _ = interpret(answers, cfg)
        assert esc is False

    def test_block_on_classification_returns_block(self):
        """When block_on_classification=True and answers escalate, verdict must be 'block'."""
        cfg = _make_cfg()
        cfg["classify"]["block_on_classification"] = True
        answers = {"decide": {"diff_type": {"type": "choice", "label": "feat", "confidence": 0.4}}}
        verdict, escalated, _ = interpret(answers, cfg)
        assert verdict == "block"
        assert escalated is True

    def test_task_confidence_floor_overrides_global(self):
        """Task.confidence_floor must override cfg.classify.confidence_floor."""
        cfg = _make_cfg(floor=0.7)
        heads = sys1.heads_for("change", "decide")
        task = sys1.Task(id="change", heads=heads, confidence_floor=0.5)
        answers = {"decide": {"diff_type": {"type": "choice", "label": "feat", "confidence": 0.6}}}
        verdict, escalated, _ = interpret(answers, cfg, task=task)
        assert verdict == "pass"
        assert escalated is False


class TestAnswerConfidence:
    """Jev noul answers carry no confidence — the 0-1 output is the probability
    of "true", so the gateable certainty is max(p, 1 - p)."""

    def test_explicit_confidence_returned(self):
        assert answer_confidence({"confidence": 0.83}) == 0.83

    def test_noul_probability_derives_certainty(self):
        assert answer_confidence({"noul": 0.95}) == 0.95

    def test_confident_no_derives_high_certainty(self):
        assert answer_confidence({"noul": 0.05}) == 0.95

    def test_explicit_confidence_beats_derivation(self):
        assert answer_confidence({"noul": 0.95, "confidence": 0.6}) == 0.6

    def test_bool_noul_has_no_gateable_number(self):
        assert answer_confidence({"noul": True}) is None
        assert answer_confidence({"noul": False}) is None

    def test_non_numeric_confidence_degrades_to_none(self):
        """The gate must never raise on a malformed provider answer."""
        assert answer_confidence({"confidence": "high"}) is None
        assert answer_confidence({"confidence": None}) is None

    def test_declined_keeps_zero_confidence(self):
        assert answer_confidence({"confidence": 0.0, "_declined": "no-answer"}) == 0.0


class TestNoulGating:
    def test_noul_probability_gates_pass(self):
        answers = {"jev": {"breaking_change": {"type": "noul", "noul": 0.95}}}
        _, escalated, _ = interpret(answers, _make_cfg(floor=0.7))
        assert escalated is False

    def test_noul_probability_gates_escalate(self):
        answers = {"jev": {"breaking_change": {"type": "noul", "noul": 0.55}}}
        _, escalated, _ = interpret(answers, _make_cfg(floor=0.7))
        assert escalated is True

    def test_confident_no_clears_floor(self):
        """p(true)=0.05 is a confident "no": certainty 0.95 must clear."""
        answers = {"jev": {"breaking_change": {"type": "noul", "noul": 0.05}}}
        _, escalated, _ = interpret(answers, _make_cfg(floor=0.7))
        assert escalated is False

    def test_explicit_confidence_still_wins(self):
        answers = {"jev": {"breaking_change": {"type": "noul", "noul": 0.95, "confidence": 0.6}}}
        _, escalated, _ = interpret(answers, _make_cfg(floor=0.7))
        assert escalated is True

    def test_bool_noul_is_not_floor_gated(self):
        """A bool noul with no confidence keeps the old behavior: label check only."""
        answers = {"jev": {"breaking_change": {"type": "noul", "noul": False}}}
        _, escalated, _ = interpret(answers, _make_cfg(floor=0.7))
        assert escalated is False

    def test_decide_label_noul_unchanged(self):
        """Decide-shaped nouls ({label, confidence}) keep gating on confidence."""
        low = {"decide": {"breaking_change": {"type": "noul", "noul": True, "confidence": 0.6}}}
        _, escalated, _ = interpret(low, _make_cfg(floor=0.7))
        assert escalated is True
        high = {"decide": {"breaking_change": {"type": "noul", "noul": True, "confidence": 0.9}}}
        _, escalated_ok, _ = interpret(high, _make_cfg(floor=0.7))
        assert escalated_ok is False

    def test_summary_marks_derived_confidence(self):
        answers = {"jev": {"breaking_change": {"type": "noul", "noul": 0.95}}}
        _, _, summary = interpret(answers, _make_cfg())
        assert any("derived" in line and "0.95" in line for line in summary)

    def test_non_numeric_confidence_does_not_raise(self):
        answers = {"decide": {"diff_type": {"type": "choice", "label": "feat", "confidence": "high"}}}
        verdict, _, _ = interpret(answers, _make_cfg())
        assert verdict == "pass"

    def test_fixture_parse_then_interpret(self):
        """End-to-end over the docs-accurate fixture: noul answers gate on the
        derived certainty, choice answers on their explicit confidence."""
        from conftest import load_fixture

        resp = load_fixture("jev_sample")
        heads = sys1.heads_for("change", "jev")
        parsed = sys1.parsers.parse_jev(resp, heads)
        # at floor 0.6 every head clears — the two noul heads (p=0.08/0.04)
        # only gate because their certainty is derived
        verdict, _, _ = interpret({"jev": parsed}, _make_cfg(floor=0.6), heads=heads)
        assert verdict == "pass"
        # at the default 0.7 exactly one head escalates (risk_tier's explicit
        # 0.65) — everything else behaves as before the noul fix
        verdict2, _, summary = interpret({"jev": parsed}, _make_cfg(), heads=heads)
        assert verdict2 == "escalate"
        assert any("risk_tier" in line for line in summary)
        assert any("breaking_change" in line and "derived" in line for line in summary)


class TestEffectiveFloor:
    def test_default_layer(self):
        cfg = _make_cfg()
        floors = floors_for(cfg)
        assert effective_floor(cfg, floors, "decide") == 0.7

    def test_head_floor_layer(self):
        cfg = _make_cfg(floor=0.7)
        floors = floors_for(cfg)
        head = sys1.make_choice("Risky?", ["yes", "no"], id="risky", floor=0.85)
        assert effective_floor(cfg, floors, "decide", head) == 0.85

    def test_config_key_beats_head_floor(self):
        cfg = _make_cfg(floor=0.7)
        cfg["classify"]["decide_confidence_floor"] = 0.9
        floors = floors_for(cfg)
        head = sys1.make_choice("Risky?", ["yes", "no"], id="risky", floor=0.5)
        assert effective_floor(cfg, floors, "decide", head) == 0.9


class TestHeadFloors:
    def test_head_floor_escalates_above_default(self):
        head = sys1.make_choice("Risky?", ["yes", "no"], id="risky", floor=0.85)
        answers = {"decide": {"risky": {"type": "choice", "label": "yes", "confidence": 0.8}}}
        _, escalated, _ = interpret(answers, _make_cfg(), heads=[head])
        assert escalated is True

    def test_head_floor_passes_above_it(self):
        head = sys1.make_choice("Risky?", ["yes", "no"], id="risky", floor=0.85)
        answers = {"decide": {"risky": {"type": "choice", "label": "yes", "confidence": 0.9}}}
        _, escalated, _ = interpret(answers, _make_cfg(), heads=[head])
        assert escalated is False

    def test_sibling_head_keeps_default_floor(self):
        """A head floor applies to its head only, not to the whole task."""
        heads = [
            sys1.make_choice("Risky?", ["yes", "no"], id="risky", floor=0.85),
            sys1.make_choice("Safe?", ["yes", "no"], id="safe"),
        ]
        answers = {
            "decide": {
                "risky": {"type": "choice", "label": "yes", "confidence": 0.9},
                "safe": {"type": "choice", "label": "yes", "confidence": 0.75},
            }
        }
        verdict, _, _ = interpret(answers, _make_cfg(), heads=heads)
        assert verdict == "pass"  # risky clears 0.85, safe clears the 0.7 default

    def test_floored_head_escalates_while_sibling_passes(self):
        heads = [
            sys1.make_choice("Risky?", ["yes", "no"], id="risky", floor=0.85),
            sys1.make_choice("Safe?", ["yes", "no"], id="safe"),
        ]
        answers = {
            "decide": {
                "risky": {"type": "choice", "label": "yes", "confidence": 0.8},
                "safe": {"type": "choice", "label": "yes", "confidence": 0.75},
            }
        }
        verdict, _, summary = interpret(answers, _make_cfg(), heads=heads)
        assert verdict == "escalate"
        assert any("risky" in line for line in summary)

    def test_head_floor_beats_task_floor(self):
        head = sys1.make_choice("Risky?", ["yes", "no"], id="risky", floor=0.85)
        task = sys1.Task(id="t", heads=[head], confidence_floor=0.6)
        answers = {"decide": {"risky": {"type": "choice", "label": "yes", "confidence": 0.8}}}
        verdict, _, _ = interpret(answers, _make_cfg(floor=0.7), heads=[head], task=task)
        # task floor 0.6 would pass at 0.8; the head floor 0.85 wins
        assert verdict == "escalate"

    def test_per_provider_config_key_beats_head_floor(self):
        cfg = _make_cfg()
        cfg["classify"]["decide_confidence_floor"] = 0.9
        head = sys1.make_choice("Risky?", ["yes", "no"], id="risky", floor=0.5)
        answers = {"decide": {"risky": {"type": "choice", "label": "yes", "confidence": 0.75}}}
        _, escalated, _ = interpret(answers, cfg, heads=[head])
        assert escalated is True  # 0.9 config key wins over head floor 0.5

    def test_head_floor_on_noul_derivation(self):
        """Head floors compose with noul certainty derivation."""
        head = sys1.make_noul("Is this a refund?", id="refund", floor=0.9)
        answers = {"jev": {"refund": {"type": "noul", "noul": 0.85}}}
        _, escalated, _ = interpret(answers, _make_cfg(), heads=[head])
        assert escalated is True
        answers_ok = {"jev": {"refund": {"type": "noul", "noul": 0.95}}}
        _, escalated_ok, _ = interpret(answers_ok, _make_cfg(), heads=[head])
        assert escalated_ok is False


class TestFinalExitCode:
    def test_pass_is_zero(self):
        assert sys1.final_exit_code(False, _make_cfg()) == 0

    def test_escalate_no_block_is_warn(self):
        cfg = _make_cfg()
        cfg["classify"]["block_on_classification"] = False
        assert sys1.final_exit_code(True, cfg) == 1

    def test_escalate_block_is_two(self):
        cfg = _make_cfg()
        cfg["classify"]["block_on_classification"] = True
        assert sys1.final_exit_code(True, cfg) == 2
