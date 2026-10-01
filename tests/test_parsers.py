"""Parser tests: each provider's wire shape → normalized `{head_id: Answer}`."""

from __future__ import annotations

import json

from conftest import load_fixture

import sys1
from sys1.parsers import parse_drex, parse_glide
from sys1.types import make_choice, make_noul, make_score


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


# ── Drex (/v1/systemone, nace.ai) ─────────────────────────────────────────────


class TestParseDrex:
    def _heads(self):
        return [
            make_choice("Is this a feature or fix?", ["feat", "fix"], id="diff_type"),
            make_noul("Does this convey urgency?", id="urgent"),
            make_score(
                "Rate the risk",
                id="risk",
                score_min=1,
                score_max=5,
                legend=["low", "medium", "high", "very high", "extreme"],
            ),
        ]

    def test_choice_answer(self):
        resp = {
            "answers": {
                "diff_type": {
                    "type": "choice",
                    "choice": "fix",
                    "probabilities": {"fix": 0.7, "feat": 0.3},
                    "confidence": 0.77,
                }
            }
        }
        parsed = parse_drex(resp, self._heads())
        entry = parsed["diff_type"]
        assert entry["type"] == "choice"
        assert entry["label"] == "fix"
        assert entry["confidence"] == 0.77
        assert entry["probabilities"]["fix"] == 0.7

    def test_noul_answer_has_no_confidence_field(self):
        # Drex noul: p IS the certainty; gating derives max(p, 1-p).
        resp = {"answers": {"urgent": {"type": "noul", "noul": 0.931}}}
        parsed = parse_drex(resp, self._heads())
        entry = parsed["urgent"]
        assert entry["type"] == "noul" and entry["noul"] == 0.931
        assert entry.get("confidence") is None

    def test_score_shifted_to_sys1_scale_and_legend_dict(self):
        # Drex score = Σ index x probability over the 0-indexed array;
        # sys1 scales run score_min..score_max, so shift by score_min.
        resp = {
            "answers": {
                "risk": {
                    "type": "score",
                    "score": 2.3,
                    "legend": ["low", "medium", "high", "very high", "extreme"],
                    "probabilities": {"0": 0.1, "1": 0.2, "2": 0.4, "3": 0.2, "4": 0.1},
                    "confidence": 0.8,
                }
            }
        }
        parsed = parse_drex(resp, self._heads())
        entry = parsed["risk"]
        assert entry["score"] == 3.3  # 1 (score_min) + 2.3
        assert entry["legend"] == {"1": "low", "2": "medium", "3": "high", "4": "very high", "5": "extreme"}
        assert entry["confidence"] == 0.8

    def test_missing_answer_declined(self):
        parsed = parse_drex({"answers": {}}, self._heads())
        assert parsed["diff_type"].get("label") is None
        assert parsed["diff_type"].get("_declined") is not None

    def test_no_answers_envelope(self):
        parsed = parse_drex({"model": "drex-v1.5"}, self._heads())
        assert all(parsed[h.id].get("_declined") == "no-answers" for h in self._heads())


class TestParseGlide:
    def _heads(self):
        return [
            make_choice("Is this a feature or fix?", ["feat", "fix"], id="diff_type"),
            make_noul("Does this convey urgency?", id="urgent"),
            make_score(
                "Rate the risk",
                id="risk",
                score_min=1,
                score_max=5,
                legend=["low", "medium", "high", "very high", "extreme"],
            ),
        ]

    def test_choice_answer_keeps_margin_confidence(self):
        # GLiDE choice confidence is the documented top1-top2 margin — kept.
        resp = {
            "answers": {
                "diff_type": {
                    "type": "choice",
                    "choice": "fix",
                    "probabilities": {"fix": 0.7, "feat": 0.3},
                    "confidence": 0.4,
                }
            }
        }
        parsed = parse_glide(resp, self._heads())
        entry = parsed["diff_type"]
        assert entry["type"] == "choice"
        assert entry["label"] == "fix"
        assert entry["confidence"] == 0.4
        assert entry["probabilities"]["fix"] == 0.7

    def test_noul_confidence_stripped(self):
        # GLiDE noul confidence is |2p-1| — on that scale a p=0.85 answer
        # scores 0.70, which would make the shared Jev-family floor stricter
        # than jev/drex for the same answer. Stripped so gating derives
        # max(p, 1-p) family-consistently.
        resp = {"answers": {"urgent": {"type": "noul", "noul": 0.9977, "confidence": 0.9954}}}
        parsed = parse_glide(resp, self._heads())
        entry = parsed["urgent"]
        assert entry["type"] == "noul" and entry["noul"] == 0.9977
        assert entry.get("confidence") is None

    def test_score_shifted_and_legend_dict_keys_shifted(self):
        # GLiDE score is 0-indexed like Drex, but legend/probabilities arrive
        # as an index-keyed dict (Drex sends a legend array), so the legend
        # keys shift instead of an array->dict conversion.
        resp = {
            "answers": {
                "risk": {
                    "type": "score",
                    "score": 2.3,
                    "expected_level": 2.28,
                    "confidence": 0.4,
                    "probabilities": {"0": 0.1, "1": 0.2, "2": 0.4, "3": 0.2, "4": 0.1},
                    "legend": {"0": "low", "1": "medium", "2": "high", "3": "very high", "4": "extreme"},
                }
            }
        }
        parsed = parse_glide(resp, self._heads())
        entry = parsed["risk"]
        assert entry["score"] == 3.3  # 1 (score_min) + 2.3
        assert entry["legend"] == {"1": "low", "2": "medium", "3": "high", "4": "very high", "5": "extreme"}
        assert entry["expected_level"] == 2.28
        assert entry["confidence"] == 0.4

    def test_missing_answer_declined(self):
        parsed = parse_glide({"answers": {}}, self._heads())
        assert parsed["diff_type"].get("label") is None
        assert parsed["diff_type"].get("_declined") is not None

    def test_no_answers_envelope(self):
        parsed = parse_glide({"model": "glide"}, self._heads())
        assert all(parsed[h.id].get("_declined") == "no-answers" for h in self._heads())

    def test_live_probe_shapes(self):
        # Response shapes captured verbatim from the 2026-10-01 live probes.
        choice = parse_glide(
            {
                "model": "glide",
                "answers": {
                    "action": {
                        "type": "choice",
                        "choice": "rollback",
                        "confidence": 0.9976600111427877,
                        "probabilities": {
                            "rollback": 0.9985705895911658,
                            "hotfix": 0.0009105784483781524,
                            "wait": 0.0005188319604560046,
                        },
                    }
                },
                "usage": {"input_tokens": 135, "output_tokens": 1},
                "token_usage": 136,
            },
            [make_choice("What should we do right now?", ["rollback", "hotfix", "wait"], id="action")],
        )
        assert choice["action"]["label"] == "rollback"
        assert choice["action"]["confidence"] == 0.9976600111427877

        noul = parse_glide(
            {
                "model": "glide",
                "answers": {
                    "refund_allowed": {
                        "type": "noul",
                        "noul": 0.006692851264816553,
                        "confidence": 0.9866142974703669,
                    }
                },
            },
            [make_noul("Does this request qualify?", id="refund_allowed")],
        )
        assert noul["refund_allowed"]["noul"] == 0.006692851264816553
        assert noul["refund_allowed"].get("confidence") is None

        score = parse_glide(
            {
                "model": "glide",
                "answers": {
                    "severity": {
                        "type": "score",
                        "score": 0,
                        "expected_level": 0.0265412062438048,
                        "confidence": 0.9504486995424942,
                        "probabilities": {"0": 0.97485368478167, "1": 0.024404985239175822},
                        "legend": {"0": "Negligible", "1": "Minor"},
                    }
                },
            },
            [make_score("How severe?", id="severity", score_min=1, score_max=2, legend=["Negligible", "Minor"])],
        )
        assert score["severity"]["score"] == 1.0  # score_min + 0
        assert score["severity"]["legend"] == {"1": "Negligible", "2": "Minor"}
