"""Pure unit tests — no DB, no network, no external services."""

from __future__ import annotations

import pytest

from app.core.security import (
    create_access_token,
    decode_access_token,
    hash_password,
    verify_password,
)
from app.pipeline.llm import _extract_json
from app.services.trends import _fallback


def test_password_roundtrip():
    h = hash_password("s3cret")
    assert h != "s3cret"
    assert verify_password("s3cret", h)
    assert not verify_password("wrong", h)


def test_jwt_roundtrip():
    token = create_access_token("user@example.com")
    payload = decode_access_token(token)
    assert payload["sub"] == "user@example.com"


@pytest.mark.parametrize(
    "raw",
    [
        '{"title": "Hi", "hashtags": ["a", "b"]}',
        '```json\n{"title": "Hi", "hashtags": ["a"]}\n```',
        'Here is your JSON:\n{"title": "Hi", "hashtags": []}\nThanks!',
    ],
)
def test_extract_json_variants(raw):
    data = _extract_json(raw)
    assert data["title"] == "Hi"
    assert isinstance(data["hashtags"], list)


def test_trend_fallback_shape():
    topics = _fallback(5)
    assert len(topics) == 5
    for t in topics:
        assert {"title", "source", "score", "keywords"} <= set(t)


def test_graph_compiles():
    from app.pipeline.graph import build_graph

    graph = build_graph()
    assert graph is not None


# --------------------------------------------------------------- quality gate
class _FakeLLM:
    """Stands in for LLMClient: returns queued script drafts + a judge verdict."""

    def __init__(self, drafts, verdict):
        self.drafts = list(drafts)
        self.verdict = verdict

    def chat(self, prompt, system=None, temperature=None):
        return self.drafts.pop(0)

    def chat_json(self, prompt, system=None):
        if isinstance(self.verdict, Exception):
            raise self.verdict
        return self.verdict


def _run_script_node(monkeypatch, drafts, verdict, candidates=3):
    from app.core.config import settings
    from app.pipeline.nodes import text_nodes

    fake = _FakeLLM(drafts, verdict)
    monkeypatch.setattr(settings, "script_candidates", candidates)
    monkeypatch.setattr(text_nodes, "get_llm", lambda: fake)
    return text_nodes.script_node(
        {"project_id": 1, "topic": "test topic", "video_format": "short", "content_type": "news"}
    )


def test_script_judge_picks_winner(monkeypatch):
    drafts = ["draft one text", "draft two text", "draft three text"]
    out = _run_script_node(
        monkeypatch, drafts, {"winner": 2, "scores": [4, 9, 5], "reason": "best hook"}
    )
    assert out["script"] == "draft two text"


def test_script_judge_failure_falls_back_to_first_draft(monkeypatch):
    drafts = ["draft one text", "draft two text", "draft three text"]
    out = _run_script_node(monkeypatch, drafts, RuntimeError("judge model down"))
    assert out["script"] == "draft one text"


def test_script_judge_bad_index_falls_back(monkeypatch):
    drafts = ["draft one text", "draft two text", "draft three text"]
    out = _run_script_node(monkeypatch, drafts, {"winner": 99, "scores": [], "reason": ""})
    assert out["script"] == "draft one text"


def test_single_candidate_skips_judge(monkeypatch):
    # With script_candidates=1 the judge must never be called (verdict would raise).
    out = _run_script_node(
        monkeypatch, ["only draft"], RuntimeError("should not be called"), candidates=1
    )
    assert out["script"] == "only draft"


# ------------------------------------------------------------ feedback loop
def test_ledger_roundtrip_and_idempotency(monkeypatch, tmp_path):
    from app.core.config import settings
    from app.services import performance

    monkeypatch.setattr(settings, "state_dir", str(tmp_path))
    performance.record_published("vid1", "dev_humor", "love as debugging")
    performance.record_published("vid1", "dev_humor", "love as debugging")  # dup ignored
    performance.record_published("vid2", "news", "GPT-6 released")

    ledger = performance.load_ledger()
    assert [v["video_id"] for v in ledger] == ["vid1", "vid2"]
    assert ledger[0]["topic_key"] == performance.topic_key("love as debugging")


def test_theme_weights_boost_performers(monkeypatch, tmp_path):
    import json

    from app.core.config import settings
    from app.services import performance

    monkeypatch.setattr(settings, "state_dir", str(tmp_path))
    themes = ["love as debugging", "a breakup explained with git", "never tested theme"]
    perf = {
        "videos": {
            "v1": {
                "content_type": "dev_humor",
                "topic_key": performance.topic_key(themes[0]),
                "score": 100.0,
            },
            "v2": {
                "content_type": "dev_humor",
                "topic_key": performance.topic_key(themes[1]),
                "score": 25.0,
            },
        }
    }
    (tmp_path / "performance.json").write_text(json.dumps(perf), encoding="utf-8")

    w = performance.theme_weights(themes)
    assert w[0] == 4.0          # top scorer: base 1 + boost 3
    assert 1.0 < w[1] < w[0]    # weaker scorer: boosted proportionally
    assert w[2] == 1.0          # untested theme keeps exploration base weight


def test_theme_weights_uniform_without_data(monkeypatch, tmp_path):
    from app.core.config import settings
    from app.services import performance

    monkeypatch.setattr(settings, "state_dir", str(tmp_path))
    assert performance.theme_weights(["a", "b"]) == [1.0, 1.0]


def test_weighted_sample_distinct_and_complete():
    from app.services.performance import weighted_sample

    items = ["a", "b", "c"]
    out = weighted_sample(items, [1.0, 5.0, 1.0], 3)
    assert sorted(out) == items          # k = len: everything drawn exactly once
    assert weighted_sample(items, [1.0, 1.0, 1.0], 99) and len(
        weighted_sample(items, [1.0, 1.0, 1.0], 2)
    ) == 2
