"""Parser tests: each provider's wire shape → normalized `{head_id: Answer}`."""

from __future__ import annotations

import json

from conftest import load_fixture

import sys1


def _decide_response(content_obj: dict) -> dict:
    return {
        "choices": [{"message": {"content": json.dumps(content_obj)}}],
        "usage": {"prompt_tokens": 280, "completion_tokens": 22, "total_tokens": 302},
    }


class TestParseDecide:
    def test_prefix_keys(self):
        resp = _decide_response(
            {
                "What type of change is this diff? Choose e": {"label": "feat", "confidence": 0.91},
                "What is the risk level of this change? lo": {"label": "low", "confidence": 0.82},
            }
        )
        parsed = sys1.parsers.parse_decide(resp, sys1.heads_for("change", "decide"))
        assert parsed["diff_type"]["label"] == "feat"
        assert parsed["diff_type"]["confidence"] == 0.91
        assert parsed["risk_tier"]["label"] == "low"

    def test_full_task_keys(self):
        """Hosted Decide sometimes echoes the full task text; the prefix-fallback
        in _lookup_by_head_keys_with_key must still find it."""
        resp = _decide_response(
            {
                "What type of change is this diff? Choose exactly one category.": {
                    "label": "refactor",
                    "confidence": 0.77,
                }
            }
        )
        heads = [sys1.heads_for("change", "decide")[0]]
        parsed = sys1.parsers.parse_decide(resp, heads)
        assert parsed["diff_type"]["label"] == "refactor"

    def test_id_keys(self):
        """Provider keys matching head.id win too."""
        resp = _decide_response({"diff_type": {"label": "fix", "confidence": 0.88}})
        heads = sys1.heads_for("change", "decide")
        parsed = sys1.parsers.parse_decide(resp, heads)
        assert parsed["diff_type"]["label"] == "fix"
        # risk_tier wasn't in the response → declined, but no signal lost
        assert parsed["risk_tier"]["confidence"] == 0.0

    def test_unmatched_keys_preserved(self):
        """A key the parser can't attribute to a head gets preserved as
        `_unmatched:<key>` rather than silently dropped."""
        resp = _decide_response(
            {
                "diff_type": {"label": "test", "confidence": 0.7},
                "weird_extra_head": {"label": "extra", "confidence": 0.5},
            }
        )
        heads = sys1.heads_for("change", "decide")
        parsed = sys1.parsers.parse_decide(resp, heads)
        assert parsed["diff_type"]["label"] == "test"
        assert "_unmatched:weird_extra_head" in parsed

    def test_missing_choices_gracefully(self):
        resp = {"error": {"message": "Provider error"}}
        heads = sys1.heads_for("change", "decide")
        parsed = sys1.parsers.parse_decide(resp, heads)
        assert all(parsed[h.id]["confidence"] == 0.0 for h in heads)


class TestParseJev:
    def test_sdk_shape(self):
        resp = load_fixture("jev_sample")
        heads = sys1.heads_for("change", "jev")
        parsed = sys1.parsers.parse_jev(resp, heads)
        assert parsed["diff_type"]["label"] == "fix"
        assert parsed["diff_type"]["confidence"] == 0.78
        assert parsed["diff_type"]["probabilities"]["fix"] == 0.62
        # Jev noul answers carry no confidence field: the 0-1 output IS the
        # probability of "true" (docs.typesafe.ai/api). Do not gate on
        # answer.confidence for noul — gating.answer_confidence derives it.
        assert parsed["breaking_change"]["noul"] == 0.08
        assert parsed["breaking_change"]["confidence"] is None

    def test_chat_completions_wrapper(self):
        inner = {"answers": {"diff_type": {"type": "choice", "choice": "docs", "confidence": 0.7}}}
        resp = {"choices": [{"message": {"content": json.dumps(inner)}}]}
        heads = [sys1.heads_for("change", "jev")[0]]
        parsed = sys1.parsers.parse_jev(resp, heads)
        assert parsed["diff_type"]["label"] == "docs"

    def test_score_answer_with_legend(self):
        resp = {
            "answers": {
                "frustration": {
                    "type": "score",
                    "score": 2.4,
                    "legend": {"1": "Calm", "2": "Frustrated", "3": "Very angry"},
                    "probabilities": {"1": 0.1, "2": 0.6, "3": 0.3},
                    "confidence": 0.62,
                }
            }
        }
        head = sys1.make_score(
            "How frustrated does the author appear?",
            score_min=1,
            score_max=3,
            legend=["Calm", "Frustrated", "Very angry"],
            id="frustration",
        )
        parsed = sys1.parsers.parse_jev(resp, [head])
        # score answers can land between levels and carry the legend map
        assert parsed["frustration"]["score"] == 2.4
        assert parsed["frustration"]["legend"]["2"] == "Frustrated"
        assert parsed["frustration"]["confidence"] == 0.62

    def test_missing_head_declined(self):
        resp = {"answers": {"diff_type": {"type": "choice", "choice": "fix", "confidence": 0.7}}}
        heads = sys1.heads_for("change", "jev")
        parsed = sys1.parsers.parse_jev(resp, heads)
        # three heads should be declined because we only returned diff_type
        assert parsed["risk_tier"]["confidence"] == 0.0
        assert parsed["breaking_change"]["confidence"] == 0.0


class TestParseLocal:
    def test_vlm_keys_by_id(self):
        raw = load_fixture("local_sample")
        heads = sys1.heads_for("change", "local")
        parsed = sys1.parsers.parse_local(raw, heads)
        assert parsed["diff_type"]["label"] == "feat"
        assert parsed["risk_tier"]["label"] == "low"

    def test_vlm_keys_by_prefix(self):
        """The legacy local venv used task[:40] as the key."""
        raw = {
            "What type of change is this diff? Choose e": {"label": "fix", "confidence": 0.6},
        }
        heads = sys1.heads_for("change", "local")
        parsed = sys1.parsers.parse_local(raw, heads)
        assert parsed["diff_type"]["label"] == "fix"


class TestParseModernbert:
    def test_full_shape(self):
        raw = load_fixture("modernbert_sample")
        heads = sys1.heads_for("change", "modernbert")
        parsed = sys1.parsers.parse_modernbert(raw, heads)
        assert parsed["diff_type"]["label"] == "feat"
        assert parsed["diff_type"]["confidence"] == 0.6412
        assert "all_scores" in parsed["diff_type"]
        assert parsed["diff_type"]["top2_gap"] == 0.1289

    def test_error_response(self):
        raw = {"_error": "model load failed"}
        heads = sys1.heads_for("change", "modernbert")
        parsed = sys1.parsers.parse_modernbert(raw, heads)
        assert all(parsed[h.id]["confidence"] == 0.0 for h in heads)


class TestNoulFallback:
    """Regression: noul=None must fall back to label; yes/no strings must coerce to bool."""

    def test_noul_none_falls_back_to_label(self):
        """When noul is None but label is a bool, use the label."""
        resp = {
            "answers": {
                "breaking_change": {"type": "noul", "noul": None, "label": True, "confidence": 0.9}
            }
        }
        heads = [sys1.heads_for("change", "jev")[2]]  # breaking_change head
        parsed = sys1.parsers.parse_jev(resp, heads)
        assert parsed["breaking_change"]["noul"] is True

    def test_noul_none_label_false(self):
        """False label should also be used when noul is None."""
        resp = {
            "answers": {
                "breaking_change": {"type": "noul", "noul": None, "label": False, "confidence": 0.9}
            }
        }
        heads = [sys1.heads_for("change", "jev")[2]]
        parsed = sys1.parsers.parse_jev(resp, heads)
        assert parsed["breaking_change"]["noul"] is False

    def test_noul_yes_string_coerces_to_true(self):
        """String 'yes' should coerce to True."""
        resp = {
            "answers": {
                "breaking_change": {"type": "noul", "noul": "yes", "confidence": 0.9}
            }
        }
        heads = [sys1.heads_for("change", "jev")[2]]
        parsed = sys1.parsers.parse_jev(resp, heads)
        assert parsed["breaking_change"]["noul"] is True

    def test_noul_no_string_coerces_to_false(self):
        """String 'no' should coerce to False."""
        resp = {
            "answers": {
                "breaking_change": {"type": "noul", "noul": "no", "confidence": 0.9}
            }
        }
        heads = [sys1.heads_for("change", "jev")[2]]
        parsed = sys1.parsers.parse_jev(resp, heads)
        assert parsed["breaking_change"]["noul"] is False
