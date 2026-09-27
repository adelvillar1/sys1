"""Gating / interpret tests: confidence floors, escalation, provider floors."""

from __future__ import annotations

import sys1
from sys1.gating import interpret


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
