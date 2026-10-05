"""Vision QC task: registration, the liquid-only allow-list, CLI image guards.

First consumer of the liquid `images` kwarg (docs/plans/2026-10-05-vision-qc-task.md):
the task's heads are only answerable from pixels, so `Task(providers=["liquid"])`
must keep text-only providers out of the call entirely — and `sys1 classify
--images` must enforce the d1 wire's limits (types, 5MB/image, 8/request)
before any request is made.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

import sys1
from sys1.cli import MAX_IMAGE_BYTES, load_images, main
from sys1.tasks import get_task

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def _vision_answers():
    """A d1-shaped response for the four vision_qc heads (family wire values:
    choice keeps confidence, noul carries none)."""
    return {
        "model": "d1",
        "answers": {
            "matches_brief": {
                "type": "choice",
                "choice": "yes",
                "probabilities": {"yes": 0.95, "partial": 0.03, "no": 0.02},
                "confidence": 0.925,
            },
            "garbled_text": {"type": "noul", "noul": 0.02},
            "on_palette": {"type": "noul", "noul": 0.97},
            "legible_small": {"type": "noul", "noul": 0.88},
        },
        "usage": {"input_tokens": 200, "output_tokens": 0, "cost": 0.000008},
    }


# ── registration + heads ─────────────────────────────────────────────────────


class TestVisionQCTask:
    def test_task_registered(self):
        assert "vision_qc" in sys1.list_tasks()
        t = get_task("vision_qc")
        assert t is not None
        assert t.providers == ["liquid"]

    def test_head_ids_and_kinds(self):
        ids = [h.id for h in get_task("vision_qc").heads]
        kinds = {h.id: h.kind for h in get_task("vision_qc").heads}
        assert ids == ["matches_brief", "garbled_text", "on_palette", "legible_small"]
        assert kinds == {
            "matches_brief": "choice",
            "garbled_text": "noul",
            "on_palette": "noul",
            "legible_small": "noul",
        }

    def test_heads_for_liquid_gets_task_heads_not_jev_inheritance(self):
        # liquid is in the jev-inheritance tuple, but the task defines its own
        # heads with no jev override — the fallthrough must return THOSE heads,
        # not the diff-classification set (that would ask "risk_tier" of an image)
        heads = sys1.heads_for("vision_qc", "liquid")
        assert [h.id for h in heads] == [
            "matches_brief",
            "garbled_text",
            "on_palette",
            "legible_small",
        ]


# ── allow-list enforcement ───────────────────────────────────────────────────


class TestProviderAllowList:
    def test_text_only_provider_never_answers_from_the_brief(self, cfg, monkeypatch):
        monkeypatch.setenv("FASTINO_API_KEY", "fk-test")  # decide would be available
        result = sys1.classify("decide", "vision_qc", "A blue wine-bottle hero.", cfg=cfg, log=False)
        # filtered out BEFORE any call: no provider ran, nothing answered
        assert result.providers_used == []
        assert result.answers == {}
        assert result.verdict == "error"

    def test_fanout_resolves_to_liquid_only(self, cfg, monkeypatch):
        monkeypatch.setenv("FASTINO_API_KEY", "fk-test")
        monkeypatch.setenv("LIQUID_API_KEY", "liquid-test")
        captured = {}

        def _fake_urlopen(req, timeout=None):
            captured["body"] = json.loads(req.data.decode())
            resp_body = json.dumps(_vision_answers()).encode()

            class R:
                status = 200

                def read(self):
                    return resp_body

                def __enter__(self):
                    return self

                def __exit__(self, *a):
                    return False

            return R()

        with patch("urllib.request.urlopen", side_effect=_fake_urlopen):
            result = sys1.classify(
                ["liquid", "decide"],
                "vision_qc",
                "brand hero: dark canvas, cyan/amber nodes.",
                images=["data:image/png;base64,AAAA"],
                cfg=cfg,
                log=False,
            )
        # the allow-list dropped decide even though it was named and available
        assert result.providers_used == ["liquid"]
        assert result.verdict == "pass"  # all four heads clear the 0.7 floor
        # and the image actually rode to the d1 wire
        assert captured["body"]["images"] == ["data:image/png;base64,AAAA"]


# ── CLI: --images parsing + guards ───────────────────────────────────────────


class TestCLIImages:
    def test_parser_accepts_images(self):
        args = main.__globals__["build_parser"]().parse_args(
            ["classify", "--provider", "liquid", "--task", "vision_qc", "--images", "a.png", "b.jpg"]
        )
        assert args.images == ["a.png", "b.jpg"]

    def test_load_images_png_data_url(self, tmp_path):
        p = tmp_path / "x.png"
        p.write_bytes(PNG_MAGIC + b"\x00" * 100)
        urls = load_images([str(p)])
        assert len(urls) == 1
        assert urls[0].startswith("data:image/png;base64,")

    def test_load_images_sniffs_jpeg_gif_webp(self, tmp_path):
        cases = {
            "a.jpg": b"\xff\xd8\xff\xe0junk",
            "a.gif": b"GIF89a junk",
            "a.webp": b"RIFF\x10\x00\x00\x00WEBP junk",
        }
        paths = []
        for name, data in cases.items():
            p = tmp_path / name
            p.write_bytes(data)
            paths.append(str(p))
        urls = load_images(paths)
        types = [u.split(";")[0] for u in urls]
        assert types == ["data:image/jpeg", "data:image/gif", "data:image/webp"]

    def test_non_image_rejected(self, tmp_path):
        p = tmp_path / "notes.txt"
        p.write_text("hello")
        with pytest.raises(ValueError, match="not a PNG/JPEG/WebP/GIF"):
            load_images([str(p)])

    def test_oversize_rejected(self, tmp_path):
        p = tmp_path / "big.png"
        data = bytearray(PNG_MAGIC)
        data.extend(b"\x00" * MAX_IMAGE_BYTES)
        p.write_bytes(bytes(data))
        with pytest.raises(ValueError, match="5MB"):
            load_images([str(p)])

    def test_too_many_images_rejected(self, tmp_path):
        p = tmp_path / "x.png"
        p.write_bytes(PNG_MAGIC + b"\x00" * 16)
        with pytest.raises(ValueError, match="at most 8"):
            load_images([str(p)] * 9)

    def test_missing_file_raises_before_request(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            load_images([str(tmp_path / "nope.png")])

    def test_cmd_classify_guard_exits_2_without_calling_classify(self, tmp_path):
        bad = tmp_path / "notes.txt"
        bad.write_text("not an image")
        calls = []
        with patch("sys1.cli.classify", side_effect=lambda *a, **k: calls.append(a)):
            rc = main(
                [
                    "classify",
                    "--provider",
                    "liquid",
                    "--task",
                    "vision_qc",
                    "--input",
                    str(_brief(tmp_path)),
                    "--images",
                    str(bad),
                ]
            )
        assert rc == 2
        assert calls == []  # guarded before any provider call


def _brief(tmp_path: Path) -> Path:
    b = tmp_path / "brief.txt"
    b.write_text("brand hero", encoding="utf-8")
    return b


class TestRouting:
    """Phase 3: the by_task override the pair probe earned for vision_qc —
    and the assertion that the default text chain stays liquid-free until a
    separate text-corpus decision admits it."""

    def test_vision_qc_routes_to_liquid(self, cfg, monkeypatch):
        from sys1.routing import route_decision

        monkeypatch.setenv("LIQUID_API_KEY", "lq-test")
        chain, reason = route_decision(get_task("vision_qc"), "a brief", cfg)
        assert chain == ["liquid"]
        assert reason == "task"

    def test_vision_qc_without_key_routes_empty(self, cfg, monkeypatch):
        from sys1.routing import route

        monkeypatch.delenv("LIQUID_API_KEY", raising=False)
        # availability filter beats the override: nothing servable is left
        assert route(get_task("vision_qc"), "a brief", cfg) == []

    def test_default_text_chain_unchanged(self, cfg, monkeypatch):
        from sys1.routing import route

        for name, val in {
            "FASTINO_API_KEY": "fk-test",
            "DREX_API_KEY": "sk-test",
            "TYPESAFE_API_KEY": "ts-test",
            "LIQUID_API_KEY": "lq-test",
        }.items():
            monkeypatch.setenv(name, val)
        chain = route(get_task("change"), "a diff", cfg)
        assert chain == ["glide", "drex", "jev"]  # liquid NOT in the text chain

class TestCLIBaseHappyPath:
    def test_happy_path_forwards_images_kwarg(self, tmp_path):
        import base64

        payload = PNG_MAGIC + b"\x00" * 64
        png = tmp_path / "a.png"
        png.write_bytes(payload)
        brief = _brief(tmp_path)
        captured = {}

        def _fake_classify(provider, task, text, **kw):
            captured.update(kw)
            return SimpleNamespace(
                task="vision_qc",
                providers_used=["liquid"],
                verdict="pass",
                escalated=False,
                latency_ms=1,
                summary=[],
                as_dict=lambda: {},
            )

        with patch("sys1.cli.classify", side_effect=_fake_classify):
            rc = main(
                [
                    "classify",
                    "--provider",
                    "liquid",
                    "--task",
                    "vision_qc",
                    "--input",
                    str(brief),
                    "--images",
                    str(png),
                ]
            )
        assert rc == 0
        expected_url = "data:image/png;base64," + base64.b64encode(payload).decode()
        assert captured["images"] == [expected_url]
