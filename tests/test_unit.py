"""Pure unit tests — no DB, no network, no external services."""

from __future__ import annotations

import os
from datetime import UTC

import pytest
from app.core.security import (
    create_access_token,
    decode_access_token,
    hash_password,
    verify_password,
)
from app.pipeline.llm import _extract_json
from app.services.sensitive import is_safe, rejection_reason
from app.services.trends import _drop_sensitive, _fallback


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


def _serve_once(handler) -> int:
    """Start a one-shot HTTP server on a free port and return it."""
    import socket
    import threading

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen(1)

    def run():
        conn, _ = sock.accept()
        conn.recv(65536)
        try:
            handler(conn)
            # Half-close so the client sees a clean EOF: closing outright sends an
            # RST on Windows and httpx reports a ReadError instead of the response.
            conn.shutdown(socket.SHUT_WR)
            conn.recv(65536)
        except OSError:
            pass  # client hung up first — that's the point of the stall test
        finally:
            conn.close()

    threading.Thread(target=run, daemon=True).start()
    return sock.getsockname()[1]


def _client_on(port: int):
    from app.pipeline.llm import LLMClient

    client = LLMClient()
    client.base_url = f"http://127.0.0.1:{port}"
    # These tests talk to a local socket, so the credential is never checked --
    # but it still has to be a *legal* header value. Left to the real config it
    # is the empty string on any clone without a .env, and httpx rejects
    # "Bearer " before the request goes out, so the test fails for a reason that
    # has nothing to do with what it is testing.
    client.api_key = "test-key"
    return client


def _call(client, model="test/model"):
    # __wrapped__ skips tenacity so one call means one request.
    return client._call_model.__wrapped__(
        client, model, [{"role": "user", "content": "hi"}], 0.7
    )


def test_llm_call_abandons_a_stalled_response(monkeypatch):
    """A trickling body must hit the wall clock, not run forever.

    OpenRouter answers a queued free-model request with headers immediately and
    then dribbles keepalive bytes. Every individual read lands inside httpx's read
    timeout, so a plain .post() waits as long as the model takes — one such call
    ran 50 minutes and the CI job was cancelled with nothing published.
    """
    import time

    from app.core.config import settings
    from app.pipeline.llm import ModelUnavailable

    def dribble(conn):
        conn.sendall(
            b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
            b"Transfer-Encoding: chunked\r\n\r\n"
        )
        for _ in range(200):  # 100s of body, far past the 2s budget below
            conn.sendall(b"1\r\n \r\n")
            time.sleep(0.5)

    monkeypatch.setattr(settings, "llm_timeout_seconds", 2)
    started = time.monotonic()
    with pytest.raises(ModelUnavailable, match="wall clock"):
        _call(_client_on(_serve_once(dribble)))
    assert time.monotonic() - started < 10


def test_llm_call_reads_a_normal_response():
    """The streaming read must still return content on the happy path."""
    import json

    body = json.dumps({"choices": [{"message": {"content": "hello world"}}]}).encode()

    def respond(conn):
        conn.sendall(
            b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
            b"Content-Length: %d\r\n\r\n" % len(body) + body
        )

    assert _call(_client_on(_serve_once(respond))) == "hello world"


def test_llm_chat_stops_at_its_budget(monkeypatch):
    """chat() must not multiply the per-request timeout across the whole chain."""
    import time

    from app.core.config import settings
    from app.pipeline.llm import LLMClient, LLMError

    monkeypatch.setattr(settings, "llm_chat_budget_seconds", 1)
    client = LLMClient()
    client.models = ["a", "b", "c", "d"]
    calls = []

    def slow(model, messages, temperature):
        calls.append(model)
        time.sleep(0.6)
        raise LLMError(f"{model} is down")

    monkeypatch.setattr(client, "_call_model", slow)
    with pytest.raises(LLMError, match="budget"):
        client.chat("hi")
    assert len(calls) < 12  # would be 4 models x 3 sweeps without the budget


def test_trend_fallback_shape():
    topics = _fallback(5)
    assert len(topics) == 5
    for t in topics:
        assert {"title", "source", "score", "keywords"} <= set(t)


# --------------------------------------------------------- sensitive-topic filter
@pytest.mark.parametrize(
    "title",
    [
        "3 AI stocks to buy before the end of the year",
        "Should you buy Nvidia after the earnings beat?",
        "How to cure back pain with this one stretch",
        "Know your rights when police stop your car",
        "Best supplements for focus and memory",
    ],
)
def test_advice_shaped_titles_are_blocked_everywhere(title):
    """The advice tier applies to every stream, curated news included."""
    assert not is_safe(title, strict=False)
    assert not is_safe(title, strict=True)


@pytest.mark.parametrize(
    "title",
    [
        "Sensex closes 400 points higher as IT stocks rally",
        "Dengue symptoms rise across Delhi as monsoon peaks",
        "Supreme Court hears petitions on the amendment act",
        "Gold rate today in Mumbai and Chennai",
    ],
)
def test_regulated_subjects_blocked_only_for_raw_trend_feeds(title):
    """Domain tier is strict-only — raw trend terms carry no editorial context."""
    assert not is_safe(title, strict=True)
    assert is_safe(title, strict=False)


@pytest.mark.parametrize(
    "title",
    [
        "Anthropic raises $2B at a $60B valuation",
        "New open-weight model beats GPT-4 on reasoning benchmarks",
        "AI system flags tumours earlier than radiologists in trial",
        "OpenAI ships a cheaper embeddings endpoint",
    ],
)
def test_tech_journalism_survives_the_news_filter(title):
    """Reporting on funding or medical AI is not advice — the news stream keeps it."""
    assert is_safe(title, strict=False)


def test_rejection_reason_names_the_trigger():
    reason = rejection_reason("Top 5 stocks to buy now", strict=False)
    assert reason and "advice-shaped" in reason
    reason = rejection_reason("Nifty ends flat ahead of the policy meet", strict=True)
    assert reason and "regulated subject" in reason


def test_drop_sensitive_filters_and_preserves_order():
    topics = [
        {"title": "Anthropic raises $2B at a $60B valuation"},
        {"title": "Best mutual funds for 2026 returns"},
        {"title": "Space telescope spots the earliest known galaxy"},
    ]
    kept = _drop_sensitive(topics, strict=True)
    assert [t["title"] for t in kept] == [
        "Anthropic raises $2B at a $60B valuation",
        "Space telescope spots the earliest known galaxy",
    ]


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


# ------------------------------------------------------- scheduled publishing
# The publish slot exists because the cron was never the publish time: measured
# over 2026-08-05..19 the runs drifted +18m, +47m and +2h39m. These pin the
# decision table so that drift can't creep back in unnoticed.
def _at(hour, minute=0):
    from datetime import datetime

    return datetime(2026, 8, 20, hour, minute, tzinfo=UTC)


@pytest.mark.parametrize(
    ("slot", "now_hour", "offset", "expected"),
    [
        # Normal case: run finishes at 21:00, slot is 22:30 the same day.
        ("22:30", 21, 0, "2026-08-20T22:30:00Z"),
        # Second video of the run is staggered so the two don't compete.
        ("22:30", 21, 15, "2026-08-20T22:45:00Z"),
        # Slot is tomorrow's when it has already passed today — the humor run
        # starts at 23:00 UTC and targets 00:30.
        ("00:30", 23, 0, "2026-08-21T00:30:00Z"),
    ],
)
def test_publish_slot_resolves_to_the_next_occurrence(slot, now_hour, offset, expected):
    from app.services.youtube import next_publish_slot

    assert next_publish_slot(slot, offset, now=_at(now_hour)) == expected


def test_publish_slot_gives_up_when_the_run_overran_it():
    """A run that misses its slot publishes now rather than holding the video 23h."""
    from app.services.youtube import next_publish_slot

    # Slot was 22:30; the run limped past it and finished at 23:00. The next
    # occurrence is 23.5h away, far beyond the max lead, so: publish immediately.
    assert next_publish_slot("22:30", 0, now=_at(23)) is None


@pytest.mark.parametrize("slot", ["", "   ", "25:00", "12:99", "half past two", "1230"])
def test_publish_slot_unset_or_malformed_publishes_immediately(slot):
    """Scheduling is an enhancement; a bad value must never block an upload."""
    from app.services.youtube import next_publish_slot

    assert next_publish_slot(slot, 0, now=_at(12)) is None


def test_scheduling_forces_private_and_declares_language(monkeypatch):
    """publishAt is only honoured on a private video, and language must be sent."""
    from app.services import youtube

    captured = {}

    class _Request:
        def next_chunk(self):
            return None, {"id": "vid123"}

    class _Videos:
        def insert(self, part, body, media_body):
            captured.update(body)
            return _Request()

    class _Service:
        def videos(self):
            return _Videos()

    monkeypatch.setattr(youtube, "_build_service", lambda: _Service())
    monkeypatch.setattr(youtube, "MediaFileUpload", object, raising=False)
    monkeypatch.setitem(
        __import__("sys").modules,
        "googleapiclient.http",
        type("m", (), {"MediaFileUpload": lambda *a, **k: None}),
    )

    vid = youtube.upload_video(
        "video.mp4", "t", "d", ["tag"],
        privacy="public", category_id="28", publish_at="2026-08-20T22:30:00Z",
    )

    assert vid == "vid123"
    # "public" was asked for, but publishAt requires private — YouTube flips it.
    assert captured["status"]["privacyStatus"] == "private"
    assert captured["status"]["publishAt"] == "2026-08-20T22:30:00Z"
    assert captured["snippet"]["defaultLanguage"] == "en-US"
    assert captured["snippet"]["defaultAudioLanguage"] == "en-US"
    assert captured["snippet"]["categoryId"] == "28"


def test_news_gets_the_tech_category_and_themed_streams_stay_people_and_blogs():
    from app.core.config import settings

    assert settings.category_for("news") == "28"
    assert settings.category_for("dev_humor") == "22"
    assert settings.category_for("code_heartbreak") == "22"
    assert settings.category_for("something_new") == "22"  # safe default


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


# --- animated captions ------------------------------------------------------


def _words(*spec):
    """(text, start, end) triples -> Word list."""
    from app.media.subtitles.animated import Word

    return [Word(s, e, t) for t, s, e in spec]


def test_split_cue_expands_sentence_level_timings():
    """Some edge-tts voices only emit SentenceBoundary; one cue must become words."""
    from app.media.subtitles.animated import _split_cue

    out = _split_cue(0.0, 3.0, "markets closed high")
    assert [w.text for w in out] == ["markets", "closed", "high"]
    assert out[0].start == 0.0 and abs(out[-1].end - 3.0) < 1e-9
    # Butted end to end, and the longest token gets the longest slice.
    assert all(abs(out[i].end - out[i + 1].start) < 1e-9 for i in range(2))
    assert (out[0].end - out[0].start) > (out[2].end - out[2].start)


def test_split_cue_passes_single_words_through():
    from app.media.subtitles.animated import _split_cue

    assert [w.text for w in _split_cue(1.0, 1.4, "markets")] == ["markets"]


def test_normalise_merges_punctuation_and_forces_monotonic_time():
    from app.media.subtitles.animated import _normalise

    out = _normalise(_words(("rates", 1.0, 1.5), ("...", 1.4, 1.6), ("next", 1.2, 1.9)))
    assert [w.text for w in out] == ["rates...", "next"]
    assert out[1].start >= out[0].end  # overlap resolved, never rewound


def test_phrases_never_exceed_the_measured_line_width():
    """A phrase that overflows would wrap and shift the block off its anchor."""
    from app.media.subtitles.animated import STYLES, _metrics, group_phrases, resolve_face

    style = STYLES["default"]
    size, outline, _, margin = _metrics(style, 1080, 1920)
    _family, font = resolve_face(style, size)
    assert font is not None, "no candidate face resolved — widths would be guessed"

    limit = 1080 - 2 * margin - 2 * outline
    text = "Indian markets closed at a record high after the central bank cut rates today"
    words = _words(*[(w, i * 0.4, i * 0.4 + 0.35) for i, w in enumerate(text.split())])

    phrases = group_phrases(words, style, limit, font.getlength)
    assert phrases and sum(len(p) for p in phrases) == len(words)  # nothing dropped
    for phrase in phrases:
        assert font.getlength(" ".join(w.text.upper() for w in phrase)) <= limit


def test_bundled_face_is_preferred_so_ci_matches_local():
    """The repo's own font must win, or CI and local renders diverge silently."""
    from app.media.subtitles.animated import STYLES, _metrics, font_dir, resolve_face

    bundled = font_dir() / "Anton-Regular.ttf"
    if not bundled.exists():
        pytest.skip("no bundled font checked out")

    style = STYLES["default"]
    size, *_ = _metrics(style, 1080, 1920)
    family, font = resolve_face(style, size)
    assert (family, font is not None) == ("Anton", True)


def test_every_style_resolves_a_real_face_on_this_platform():
    """The last candidate in each chain must exist, or captions silently degrade.

    This is the check that would have caught Arial Black being absent from the
    Linux CI runners that render production videos.
    """
    from app.media.subtitles.animated import STYLES, _metrics, resolve_face

    for name, style in STYLES.items():
        size, *_ = _metrics(style, 1080, 1920)
        family, font = resolve_face(style, size)
        assert font is not None, f"style '{name}' resolved no installed face"
        assert family in [f for f, _ in style.faces]


def test_caption_events_are_gapless_and_never_overlap(tmp_path):
    r"""Overlapping \pos'd lines would draw on top of each other; gaps flicker."""
    import json
    import re

    from app.media.subtitles.animated import build_animated_captions

    audio = tmp_path / "voiceover.mp3"
    audio.write_bytes(b"")
    cues, t = [], 0.0
    for w in "Indian markets closed at a record high. Analysts say it may last.".split():
        cues.append({"s": t, "e": t + 0.3, "t": w})
        t += 0.36
    (tmp_path / "voiceover.words.json").write_text(json.dumps(cues))

    ass = build_animated_captions(audio, None, tmp_path / "c.ass", 1080, 1920)
    assert ass is not None

    body = ass.read_text(encoding="utf-8")
    assert "PlayResX: 1080" in body and "PlayResY: 1920" in body

    def secs(v):
        h, m, s = v.split(":")
        return int(h) * 3600 + int(m) * 60 + float(s)

    spans = [
        (secs(p[1]), secs(p[2]))
        for p in (ln.split(",", 3) for ln in body.splitlines() if ln.startswith("Dialogue:"))
    ]
    assert len(spans) == len(cues)  # one highlight step per word
    for (_, end), (start, _) in zip(spans, spans[1:], strict=False):
        assert start >= end - 1e-6                 # no overlap
        assert start - end < 0.05                  # no visible gap
    # Every event carries a fixed anchor, so the line cannot drift between words.
    anchors = set(re.findall(r"\\pos\((\d+),(\d+)\)", body))
    assert len(anchors) == 1


def test_captions_degrade_to_none_without_timings(tmp_path):
    """No word data must return None so the caller can fall back to the SRT."""
    from app.media.subtitles.animated import build_animated_captions

    audio = tmp_path / "voiceover.mp3"
    audio.write_bytes(b"")
    assert build_animated_captions(audio, None, tmp_path / "c.ass", 1080, 1920) is None


def test_words_interpolated_from_srt_when_no_sidecar(tmp_path):
    from app.media.subtitles.animated import load_words

    srt = tmp_path / "subtitles.srt"
    srt.write_text(
        "1\n00:00:00,000 --> 00:00:02,000\nmarkets closed high\n\n"
        "2\n00:00:02,000 --> 00:00:03,000\ntoday\n",
        encoding="utf-8",
    )
    words = load_words(tmp_path / "voiceover.mp3", srt)
    assert [w.text for w in words] == ["markets", "closed", "high", "today"]
    assert words[-1].end <= 3.0 + 1e-9


# --- punctuation recovery ---------------------------------------------------
#
# The speech service reports bare words, so without this the captions carry no
# punctuation at all and the sentence-based phrase break can never fire.


def test_punctuation_reattached_from_source_text():
    from app.media.tts.edge_tts_provider import _attach_punctuation

    out = _attach_punctuation("Rates fell. Really?", ["Rates", "fell", "Really"])
    assert out == ["Rates", "fell.", "Really?"]


def test_punctuation_attach_never_loses_or_reorders_words():
    """Timings are keyed by position — a dropped token desynchronises everything after."""
    from app.media.tts.edge_tts_provider import _attach_punctuation

    text = "GPT 5 shipped, reportedly. OpenAI's team said: it's 70 percent faster!"
    tokens = text.replace(",", "").replace(".", "").replace(":", "").replace("!", "").split()
    out = _attach_punctuation(text, tokens)
    assert len(out) == len(tokens)
    assert all(o.startswith(t) for o, t in zip(out, tokens, strict=True))


def test_punctuation_attach_bails_out_when_the_service_expands_tokens():
    """Half-punctuated output breaks phrases at arbitrary points; none is better."""
    from app.media.tts.edge_tts_provider import _attach_punctuation

    tokens = ["It", "cost", "1.2", "billion", "dollars", "in", "twenty", "twenty", "five"]
    assert _attach_punctuation("It cost $1.2B in 2025.", tokens) == tokens


def test_punctuation_attach_handles_repeated_words():
    """Guards the forward-only search: a global find would match the wrong 'no'."""
    from app.media.tts.edge_tts_provider import _attach_punctuation

    assert _attach_punctuation("No, no, no.", ["No", "no", "no"]) == ["No,", "no,", "no."]


def test_punctuation_attach_ignores_opening_quotes():
    """An opening quote belongs to the next word, and always follows a space."""
    from app.media.tts.edge_tts_provider import _attach_punctuation

    out = _attach_punctuation(
        'He said "stop." Then we left.', ["He", "said", "stop", "Then", "we", "left"]
    )
    assert out[1] == "said"  # not 'said"'
    assert out[2] == 'stop."'


def test_punctuation_attach_is_a_noop_on_already_punctuated_cues():
    """The SentenceBoundary fallback already carries punctuation — never double it."""
    from app.media.tts.edge_tts_provider import _attach_punctuation

    tokens = ["Rates", "fell.", "Really?"]
    assert _attach_punctuation("Rates fell. Really?", tokens) == tokens


def test_phrases_break_on_sentence_ends():
    """The whole point of recovering punctuation: captions stop straddling sentences."""
    from app.media.subtitles.animated import STYLES, group_phrases

    # Short enough that only the sentence rule can be doing the splitting — the
    # default style's fallback budget is 18 chars and max_words is 4.
    words = _words(
        ("Oil", 0.0, 0.3),
        ("fell.", 0.4, 0.7),
        ("Banks", 0.8, 1.1),
        ("held.", 1.2, 1.5),
    )
    phrases = group_phrases(words, STYLES["default"])
    assert [[w.text for w in p] for p in phrases] == [["Oil", "fell."], ["Banks", "held."]]


def test_phrases_do_not_break_on_abbreviations():
    """Regression guard: recovering punctuation makes the break rule fire on 'U.S.' too."""
    from app.media.subtitles.animated import STYLES, group_phrases

    words = _words(("The", 0.0, 0.2), ("U.S.", 0.3, 0.6), ("team", 0.7, 0.9), ("won.", 1.0, 1.2))
    assert len(group_phrases(words, STYLES["default"])) == 1


def test_normalise_does_not_double_punctuate():
    """Punctuated tokens must take the normal path, not the merge branch."""
    from app.media.subtitles.animated import _normalise

    out = _normalise(_words(("rates,", 1.0, 1.5), ("next.", 1.6, 1.9)))
    assert [w.text for w in out] == ["rates,", "next."]


# --- caption emphasis -------------------------------------------------------


def _emphasised(text: str, style=None):
    """Run a space-separated script through mark_emphasis; returns the flagged words."""
    from app.media.subtitles.animated import STYLES, mark_emphasis

    words = _words(*[(w, i * 0.4, i * 0.4 + 0.3) for i, w in enumerate(text.split())])
    return [w.text for w in mark_emphasis(words, style or STYLES["default"]) if w.emphasis]


def _unlimited():
    """The default style with rate limiting off, to test the rules in isolation."""
    import dataclasses

    from app.media.subtitles.animated import STYLES

    return dataclasses.replace(STYLES["default"], emphasis_min_gap=0)


def test_emphasis_marks_numbers_and_acronyms_but_not_openers_or_i():
    """'I' and sentence openers are capitalised everywhere; emphasising them is noise."""
    hits = _emphasised("I shipped 70 patches. The OpenAI team never noticed me.", _unlimited())
    assert "70" in hits and "OpenAI" in hits
    for noise in ("I", "The", "shipped", "team"):
        assert noise not in hits


def test_emphasis_keeps_a_version_number_welded_to_its_name():
    """'GPT 5' is one thing; splitting the highlight across it reads as two."""
    assert {"GPT", "5"} <= set(_emphasised("Today GPT 5 landed without warning."))


def test_emphasis_is_rate_limited():
    """Unlimited, the rules fire on a third of a technical script and mean nothing."""
    from app.media.subtitles.animated import STYLES, mark_emphasis

    text = "SQL JOIN NULL HEAD API GPT REST HTTP JSON YAML"
    words = _words(*[(w, i * 0.4, i * 0.4 + 0.3) for i, w in enumerate(text.split())])
    marked = mark_emphasis(words, STYLES["default"])
    idx = [i for i, w in enumerate(marked) if w.emphasis]
    assert len(idx) < len(words) / 2


def test_last_word_of_the_script_is_emphasised():
    """The closing line is the payoff; it is emphasised on merit, not by rule match."""
    assert "table." in _emphasised("She indexed every moment then dropped the table.")


def test_quote_style_renders_no_emphasis(tmp_path):
    """A heartbreak reel is deliberately calm — no second colour, no scaling."""
    import json

    from app.media.subtitles.animated import build_animated_captions

    audio = tmp_path / "voiceover.mp3"
    audio.write_bytes(b"")
    cues, t = [], 0.0
    for w in "She was my primary key. GPT 5 never called me.".split():
        cues.append({"s": t, "e": t + 0.3, "t": w})
        t += 0.36
    (tmp_path / "voiceover.words.json").write_text(json.dumps(cues))

    ass = build_animated_captions(audio, None, tmp_path / "c.ass", 1080, 1920, "quote")
    body = ass.read_text(encoding="utf-8")
    assert "\\fscx112" not in body
    assert "&H" + "FFE500" not in body  # emphasis_colour 00E5FF in BGR


def _dialogue_lines(body: str) -> list[str]:
    return [ln for ln in body.splitlines() if ln.startswith("Dialogue:")]


def _emphasised_ass(tmp_path, style_name="default", width=1080, height=1920):
    """Build captions over a script that definitely contains emphasis."""
    import json

    from app.media.subtitles.animated import build_animated_captions

    audio = tmp_path / "voiceover.mp3"
    audio.write_bytes(b"")
    cues, t = [], 0.0
    for w in "GPT 5 runs 70 percent faster. Nobody saw it coming.".split():
        cues.append({"s": t, "e": t + 0.3, "t": w})
        t += 0.36
    (tmp_path / "voiceover.words.json").write_text(json.dumps(cues))
    ass = build_animated_captions(audio, None, tmp_path / "c.ass", width, height, style_name)
    assert ass is not None
    return ass.read_text(encoding="utf-8")


def test_emphasis_scale_is_constant_across_every_event_of_a_phrase(tmp_path):
    r"""The wobble guard.

    A phrase is anchored \an5 + \pos, so if the scale pattern changed between a
    phrase's events the line would re-flow and visibly shift while it is being read.
    Every event of one phrase must lay out identically.
    """
    import re

    body = _emphasised_ass(tmp_path)
    assert "\\fscx112" in body, "fixture produced no emphasis; the test proves nothing"

    by_phrase: dict[str, list[str]] = {}
    for line in _dialogue_lines(body):
        text = line.split(",", 9)[-1]
        # The rendered words are the phrase's identity; the scales must follow them.
        key = "|".join(re.findall(r"\}([^{]+)", text))
        by_phrase.setdefault(key.strip(), []).append(",".join(re.findall(r"\\fscx(\d+)", text)))

    for key, patterns in by_phrase.items():
        # Ignore the entry pop, which legitimately animates from a smaller size.
        steady = [p for p in patterns[1:]] if len(patterns) > 1 else patterns
        assert len(set(steady)) == 1, f"scale changes mid-phrase for {key!r}: {steady}"


def test_every_word_carries_an_explicit_scale_when_a_phrase_is_emphasised(tmp_path):
    r"""\fscx persists until overridden, so one bare word would inherit 112%."""
    import re

    body = _emphasised_ass(tmp_path)
    for line in _dialogue_lines(body):
        text = line.split(",", 9)[-1]
        if "\\fscx112" not in text:
            continue
        runs = re.findall(r"\{[^}]*\}", text)
        # Every run that precedes drawn text declares its own scale.
        drawn = [r for r in runs if "\\c&H" in r or "\\fscx" in r]
        assert all("\\fscx" in r for r in drawn), text


def test_emphasised_phrases_still_fit_the_measured_line_width(tmp_path):
    """A 112% word widens the line; unaccounted for, a full phrase would wrap."""
    from app.media.subtitles.animated import (
        STYLES,
        _metrics,
        _phrase_width,
        group_phrases,
        mark_emphasis,
        resolve_face,
    )

    style = STYLES["default"]
    size, outline, _, margin = _metrics(style, 1080, 1920)
    _family, font = resolve_face(style, size)
    assert font is not None

    limit = 1080 - 2 * margin - 2 * outline
    text = "OpenAI shipped GPT 5 today and it runs 70 percent faster than Claude did"
    words = mark_emphasis(
        _words(*[(w, i * 0.4, i * 0.4 + 0.35) for i, w in enumerate(text.split())]), style
    )
    assert any(w.emphasis for w in words)

    phrases = group_phrases(words, style, limit, font.getlength)
    assert sum(len(p) for p in phrases) == len(words)  # nothing dropped
    for phrase in phrases:
        assert _phrase_width(phrase, style, font.getlength) <= limit


def test_caption_events_survive_punctuated_sentence_dense_input(tmp_path):
    """Sentence breaks create more clamped phrase ends; events must still not overlap."""
    body = _emphasised_ass(tmp_path)

    def secs(v):
        h, m, s = v.split(":")
        return int(h) * 3600 + int(m) * 60 + float(s)

    spans = [
        (secs(p[1]), secs(p[2])) for p in (ln.split(",", 3) for ln in _dialogue_lines(body))
    ]
    # Not strict equality with the word count: an event whose span is clamped to
    # nothing by the next phrase is legitimately dropped.
    assert 0 < len(spans) <= 10
    for (_, end), (start, _) in zip(spans, spans[1:], strict=False):
        assert start >= end - 1e-6


def test_terminal_punctuation_is_drawn_but_commas_are_not(tmp_path):
    """Sentence intent survives; a comma at display size is just a speck."""
    from app.media.subtitles.animated import STYLES, Word, _render_text

    style = STYLES["default"]
    assert _render_text(Word(0, 1, "query,"), style) == "query"
    assert _render_text(Word(0, 1, "table."), style) == "table."
    assert _render_text(Word(0, 1, "really?"), style) == "really?"
    # The quote style sets its captions as written prose instead.
    assert _render_text(Word(0, 1, "query,"), STYLES["quote"]) == "query,"


# --- narration text normalization -------------------------------------------


def test_clean_text_normalises_smart_punctuation_for_speech():
    """Typographic characters mangle the voice, and pollute the caption matching."""
    from app.pipeline.nodes.text_nodes import _clean_text

    out = _clean_text("She said “wait”… it’s over \U0001f600 now—really")
    assert "“" not in out and "”" not in out and "’" not in out
    assert "..." in out and "…" not in out  # edge-tts pauses on three periods
    assert "\U0001f600" not in out  # otherwise read aloud as "grinning face"
    assert "—" in out and " — " in out  # a spaced dash reads as a beat


def test_clean_text_still_strips_markdown_and_preamble():
    """The normalization must not have displaced what this function already did."""
    from app.pipeline.nodes.text_nodes import _clean_text

    assert _clean_text("Sure, here's the script:\n**Rates** fell.") == "Rates fell."


def test_script_prompts_carry_the_delivery_block_and_stay_format_safe():
    """A stray brace in the shared block would KeyError every script generation."""
    from app.pipeline import prompts

    for name in (
        "SCRIPT_PROMPT_SHORT",
        "SCRIPT_PROMPT_LONG",
        "DEV_HUMOR_SCRIPT_PROMPT",
        "CODE_HEARTBREAK_SCRIPT_PROMPT",
    ):
        template = getattr(prompts, name)
        assert "DELIVERY" in template, name
        assert "{rhythm}" not in template, f"{name} kept the unsubstituted placeholder"
        template.format(topic="t", research="r", script="s")  # must not raise


# --- tracing ----------------------------------------------------------------
#
# The property that matters most is that tracing is inert unless switched on, and
# that it can never take a render down.


@pytest.fixture
def _clean_tracing_env(monkeypatch):
    """Reset tracing to a pristine, disabled state for one test."""
    import app.core.tracing as tracing

    for key in ("LANGSMITH_TRACING", "LANGSMITH_API_KEY", "LANGCHAIN_TRACING_V2"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(tracing, "_configured", False)
    return tracing


def test_tracing_is_off_by_default(_clean_tracing_env, monkeypatch):
    """Zero-infrastructure default: nothing leaves the machine unless asked."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "langsmith_tracing", False)
    assert _clean_tracing_env.setup_tracing() is False
    # Pinned to "false", not merely absent — a stray env var in a shell profile
    # must not be able to start shipping prompts on its own.
    assert os.environ["LANGSMITH_TRACING"] == "false"


def test_tracing_without_a_key_disables_itself(_clean_tracing_env, monkeypatch):
    """Half-configured tracing must degrade to off, never raise mid-run."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "langsmith_tracing", True)
    monkeypatch.setattr(settings, "langsmith_api_key", "")
    assert _clean_tracing_env.setup_tracing() is False
    assert os.environ["LANGSMITH_TRACING"] == "false"


def test_tracing_helpers_are_inert_when_disabled(_clean_tracing_env, monkeypatch):
    """trace_run/tag_current_run are called on every run; off, they must do nothing."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "langsmith_tracing", False)
    _clean_tracing_env.setup_tracing()

    with _clean_tracing_env.trace_run("news/short", project_id=1, content_type="news"):
        _clean_tracing_env.tag_current_run(judge_winner=2, judge_scores=[1, 2, 3])


def test_tracing_setup_enables_with_a_key(_clean_tracing_env, monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "langsmith_tracing", True)
    monkeypatch.setattr(settings, "langsmith_api_key", "lsv2_test")
    monkeypatch.setattr(settings, "langsmith_project", "unit-test")
    assert _clean_tracing_env.setup_tracing() is True
    assert os.environ["LANGSMITH_PROJECT"] == "unit-test"


def test_tag_current_run_survives_a_broken_backend(_clean_tracing_env, monkeypatch):
    """Annotating a trace must never be the thing that fails a render."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "langsmith_tracing", True)
    monkeypatch.setattr(settings, "langsmith_api_key", "lsv2_test")
    monkeypatch.setattr(settings, "langsmith_endpoint", "http://127.0.0.1:9")
    _clean_tracing_env.setup_tracing()
    # No active span, unreachable endpoint — still a silent no-op.
    _clean_tracing_env.tag_current_run(model="x", failovers=["a", "b"])


def test_traced_llm_call_returns_the_models_reply(monkeypatch):
    """@traceable must be transparent: same return value, traced or not."""
    from app.pipeline.llm import LLMClient

    client = LLMClient.__new__(LLMClient)
    client.provider = "openrouter"
    client.models = ["model-a", "model-b"]
    calls: list[str] = []

    def fake_call(model, messages, temperature):
        calls.append(model)
        if model == "model-a":
            raise RuntimeError("rate limited")
        return "  the winning draft  "

    monkeypatch.setattr(client, "_call_model", fake_call)
    assert client.chat("prompt", system="sys") == "the winning draft"
    assert calls == ["model-a", "model-b"]  # failed over, and the result is unchanged


# --- video motion -----------------------------------------------------------


def test_motion_clip_is_frame_exact_and_subpixel_smooth(tmp_path):
    """Integer-rounded scaling judders; the crop-box approach must not."""
    import numpy as np
    from app.media.video import _motion_clip
    from PIL import Image

    src = tmp_path / "scene.png"
    rng = np.random.default_rng(0)
    Image.fromarray(rng.integers(0, 255, (600, 400, 3), dtype=np.uint8)).save(src)

    clip = _motion_clip(str(src), 3.0, 360, 640, 0)
    frames = [clip.make_frame(i / 30) for i in range(20)]

    assert all(f.shape == (640, 360, 3) for f in frames)  # exactly the target frame
    diffs = [float(np.abs(frames[i + 1].astype(float) - frames[i]).mean()) for i in range(19)]
    assert all(d > 0 for d in diffs)  # no frozen frames, so the move never stalls


def test_motion_moves_are_all_in_bounds():
    """Every preset must keep its crop window inside the source at both ends."""
    from app.media.video import _MOVES

    for z0, z1, x0, x1, y0, y1 in _MOVES:
        assert z0 >= 1.0 and z1 >= 1.0  # never upscale past 1:1
        assert all(0.0 <= v <= 1.0 for v in (x0, x1, y0, y1))


# --- audio mastering --------------------------------------------------------


def test_loudnorm_measuring_pass_carries_no_measurements():
    """Pass one has nothing to correct with; it must not claim linear mode."""
    from app.media.video import LOUDNESS_TARGET_LUFS, _loudnorm

    f = _loudnorm()
    assert f"I={LOUDNESS_TARGET_LUFS}" in f
    assert "measured_" not in f
    assert "linear" not in f


def test_loudnorm_corrective_pass_is_linear_and_uses_every_measurement():
    """Dropping a measurement silently reverts loudnorm to dynamic (pumping) mode."""
    from app.media.video import _loudnorm

    f = _loudnorm(
        {
            "input_i": "-20.74",
            "input_tp": "-1.20",
            "input_lra": "2.40",
            "input_thresh": "-31.00",
            "target_offset": "0.15",
        }
    )
    assert "linear=true" in f
    for part in ("measured_I=-20.74", "measured_TP=-1.20", "measured_LRA=2.40",
                 "measured_thresh=-31.00", "offset=0.15"):
        assert part in f


def test_audio_chain_mixes_music_only_when_there_is_music(tmp_path):
    """Both shapes must end on [a] — the mapped output label in _run_master."""
    from app.media.video import _audio_chain

    dry = _audio_chain(None, "anull")
    assert "amix" not in dry and dry.endswith("[a]")

    wet = _audio_chain(tmp_path / "bed.mp3", "anull")
    assert "amix=inputs=2:duration=first:normalize=0" in wet and wet.endswith("[a]")
    # normalize=0 matters: amix's own normalization would halve the voice to make
    # headroom for a bed that is already ducked.
    assert "normalize=0" in wet


def test_audio_inputs_loop_the_music_forever(tmp_path):
    """A bed shorter than the narration must repeat, not fall silent partway."""
    from app.media.video import _audio_inputs

    assert _audio_inputs(tmp_path / "v.mp4", None).count("-i") == 1
    args = _audio_inputs(tmp_path / "v.mp4", tmp_path / "bed.mp3")
    assert args[2:4] == ["-stream_loop", "-1"]


def test_audio_inputs_order_matches_the_filter_graph_labels(tmp_path):
    """The voiceover input sits between the render and the music, and _audio_chain
    hard-codes [1:a]/[2:a] against that order — so the ordering is load-bearing."""
    from pathlib import Path

    from app.media.video import _audio_inputs

    args = _audio_inputs(tmp_path / "v.mp4", tmp_path / "bed.mp3", tmp_path / "vo.wav")
    inputs = [args[i + 1] for i, a in enumerate(args) if a == "-i"]
    assert [Path(p).name for p in inputs] == ["v.mp4", "vo.wav", "bed.mp3"]
    # the loop flag must still attach to the music, not the voiceover
    assert args[args.index("-stream_loop") + 2 : args.index("-stream_loop") + 4] == [
        "-i",
        str(tmp_path / "bed.mp3"),
    ]


@pytest.mark.parametrize("value", ["-inf", "inf", "nan", "", None, "n/a"])
def test_non_finite_measurements_are_rejected(value):
    """A silent track measures -inf, and linear mode cannot be built from it."""
    from app.media.video import _is_finite

    assert not _is_finite(value)


@pytest.mark.parametrize("value", ["-20.74", "0", "-1.5", -14.0])
def test_real_measurements_are_accepted(value):
    from app.media.video import _is_finite

    assert _is_finite(value)


# --- image seeding ----------------------------------------------------------


def test_scene_seed_is_stable_per_project_but_varies_across_them():
    """Re-rendering a project must reproduce it; a repeated theme must not."""
    from pathlib import Path

    from app.media.images.pollinations import _seed

    prompt = "rain on a dark window, single glowing monitor"
    images = Path("media_output/project_16/images")
    scene = images / "scene_003.png"

    assert _seed(prompt, scene) == _seed(prompt, scene)
    other_project = Path("media_output/project_44/images/scene_003.png")
    assert _seed(prompt, scene) != _seed(prompt, other_project)
    assert _seed(prompt, scene) != _seed(prompt, images / "scene_004.png")
    assert 0 <= _seed(prompt, scene) < 1_000_000


# --- scene prompt padding ---------------------------------------------------


def _run_thumbnail_node(monkeypatch, scene_prompts, script_words=130):
    from app.pipeline.nodes import text_nodes

    fake = _FakeLLM([], {"thumbnail_prompt": "bold thumb", "scene_prompts": scene_prompts})
    monkeypatch.setattr(text_nodes, "get_llm", lambda: fake)
    return text_nodes.thumbnail_prompt_node(
        {
            "project_id": 1,
            "topic": "test topic",
            "title": "test title",
            "script": " ".join(["word"] * script_words),
            "video_format": "short",
            "content_type": "news",
        }
    )


def test_short_scene_prompt_lists_cycle_instead_of_repeating_the_opener(monkeypatch):
    """A model returning too few prompts must still give the video visual variety.

    `len(scenes) % len(scenes)` is always 0, so padding that reads the list while
    appending to it pins every extra scene to prompt #1 — the back half of the
    video becomes the opening shot again, whatever the narration moved on to.
    """
    returned = ["A rainy window", "B empty desk", "C neon street"]
    scenes = _run_thumbnail_node(monkeypatch, returned)["scene_prompts"]

    assert len(scenes) > len(returned)  # padding actually happened
    assert scenes[: len(returned)] == returned
    assert set(scenes) == set(returned)  # every prompt reused, not just the first
    # Cycling, so no prompt is used more than one time above any other.
    counts = [scenes.count(p) for p in returned]
    assert max(counts) - min(counts) <= 1


def test_scene_prompt_list_is_truncated_when_the_model_overshoots(monkeypatch):
    """Extra prompts cost an image each, so the cap has to bind downward too."""
    from app.core.config import settings

    scenes = _run_thumbnail_node(monkeypatch, [f"scene {i}" for i in range(40)])["scene_prompts"]
    assert len(scenes) <= settings.max_scenes_short


def test_scene_count_follows_the_configured_cut_rhythm(monkeypatch):
    """Scene count tracks narration length, so the rhythm setting is what drives it."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "seconds_per_scene", 5)
    fast = _run_thumbnail_node(monkeypatch, ["a"], script_words=130)["scene_prompts"]
    monkeypatch.setattr(settings, "seconds_per_scene", 10)
    slow = _run_thumbnail_node(monkeypatch, ["a"], script_words=130)["scene_prompts"]
    assert len(fast) > len(slow)


# --------------------------------------------------------------------------- #
# music fades, voice routing and upscale sharpening
# --------------------------------------------------------------------------- #


def test_music_fades_out_before_the_end():
    """Without a fade the looped bed is guillotined at full level on the last frame."""
    from app.media.video import MUSIC_FADE_IN, MUSIC_FADE_OUT, _music_fades

    f = _music_fades(30.0)
    assert f.startswith(",afade=t=in:st=0:d=")
    assert f"d={MUSIC_FADE_IN:.3f}" in f
    # the out must START before the end, so it has finished by the time amix cuts
    assert f"st={30.0 - MUSIC_FADE_OUT:.3f}:d={MUSIC_FADE_OUT:.3f}" in f


def test_short_videos_get_proportionally_shorter_fades():
    """A 1.2s fade-out on a 2s video would mean the bed is never at full level."""
    from app.media.video import _music_fades

    f = _music_fades(2.0)
    assert "d=0.500" in f  # capped at duration / 4, not MUSIC_FADE_OUT
    assert "st=1.500" in f
    # an unknown duration must not produce a fade at a bogus timestamp
    assert _music_fades(0.0) == ""
    assert _music_fades(-1.0) == ""


def test_chain_reads_the_original_voiceover_when_it_is_supplied(tmp_path):
    """Mastering from input 1 is what keeps the narration to a single AAC encode."""
    from app.media.video import _audio_chain

    voice, music = tmp_path / "vo.wav", tmp_path / "bed.mp3"

    with_voice = _audio_chain(music, "LN", 20.0, voice)
    assert "[1:a][m]amix" in with_voice  # voice = the dedicated input
    assert "[2:a]volume=" in with_voice  # music shifted along by one

    # ...and with no voiceover we fall back to the render's own audio, as before
    without = _audio_chain(music, "LN", 20.0, None)
    assert "[0:a][m]amix" in without
    assert "[1:a]volume=" in without


def test_analysis_and_correction_measure_the_same_chain(tmp_path, monkeypatch):
    """loudnorm's linear mode is only correct if the measured graph is the applied one.

    A fade or an input-index difference between the two passes would silently push
    the finished mix off the target it reports hitting.
    """
    from app.media.video import _audio_chain, _loudnorm

    voice, music = tmp_path / "vo.wav", tmp_path / "bed.mp3"
    measured = {
        "input_i": "-20.9", "input_tp": "-3.2", "input_lra": "2.1",
        "input_thresh": "-31.0", "target_offset": "0.1",
    }
    analysis = _audio_chain(music, _loudnorm() + ":print_format=json", 20.0, voice)
    correction = _audio_chain(music, _loudnorm(measured), 20.0, voice)

    # everything up to the loudnorm filter itself must be byte-identical
    assert analysis.split("[mix]")[0] == correction.split("[mix]")[0]


def test_enlarged_sources_are_sharpened_and_native_ones_are_not():
    """The providers cap at 576x1024, so a Short is a ~2.1x enlargement every time."""
    import math

    from app.media.video import SHARPEN_MAX_PERCENT, _sharpen
    from PIL import Image, ImageFilter, ImageStat

    src = Image.new("RGB", (200, 200))
    px = src.load()
    for y in range(200):
        for x in range(200):
            # mid-frequency, mid-contrast detail — the kind a resample smears and
            # the kind the scene art is actually made of
            v = 128 + 70 * math.sin(x / 5.0) * math.cos(y / 6.0)
            px[x, y] = (int(v), int(v * 0.85), int(v * 0.7))

    big = src.resize((428, 428), Image.LANCZOS)  # the 2.14x the real pipeline does

    def edges(im):
        return ImageStat.Stat(im.convert("L").filter(ImageFilter.FIND_EDGES)).mean[0]

    assert edges(_sharpen(big, 2.14)) > edges(big)
    # a provider that returns frame-sized images must not be sharpened at all
    assert _sharpen(big, 1.0) is big
    # and the strength stays capped however far the image was stretched
    assert round(min(SHARPEN_MAX_PERCENT, 65.0 * (10.0 - 1.0))) == SHARPEN_MAX_PERCENT


def test_undersized_images_are_reported(tmp_path):
    """The silence was the bug: a 576x1024 image made a 1080x1920 run log `7/7 real`."""
    from app.media.images.pollinations import _warn_if_undersized
    from PIL import Image

    small = tmp_path / "scene_000.png"
    Image.new("RGB", (576, 1024)).save(small)

    messages = []
    from app.core.logging import logger

    sink = logger.add(lambda m: messages.append(str(m)), level="WARNING")
    try:
        _warn_if_undersized(small, 1080, 1920)
        assert any("576x1024" in m and "1080x1920" in m for m in messages)

        messages.clear()
        exact = tmp_path / "scene_001.png"
        Image.new("RGB", (1080, 1920)).save(exact)
        _warn_if_undersized(exact, 1080, 1920)
        assert not messages
    finally:
        logger.remove(sink)
