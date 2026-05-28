"""Tests for ai.py helpers that don't hit the network: JSON extraction/repair,
inline-math flattening, MCQ shuffling, notes blocks, and the prompt-caching
plumbing (cached system block + the central call_claude wiring)."""
import io
from contextlib import redirect_stdout

import pytest

import ai


# ---------------------------------------------------------------- JSON extraction

def test_extract_json_from_code_fence():
    assert ai.extract_json_from_response('```json\n{"a": 1}\n```') == {"a": 1}


def test_extract_json_from_loose_braces():
    assert ai.extract_json_from_response('blah {"a": 2} trailing') == {"a": 2}


def test_extract_json_none_and_garbage():
    assert ai.extract_json_from_response(None) is None
    assert ai.extract_json_from_response("no json at all") is None


def test_extract_json_array():
    assert ai.extract_json_array_from_response('[{"x": 1}, {"y": 2}]') == [{"x": 1}, {"y": 2}]


# ---------------------------------------------------------------- JSON repair

def test_repair_invalid_latex_backslash():
    # \alpha is not a valid JSON escape; the relaxed loader must double it.
    assert ai._loads_relaxed(r'{"q": "\alpha"}') == {"q": r"\alpha"}


def test_repair_unescaped_control_chars():
    # A literal newline inside a string value is illegal JSON; loader repairs it.
    assert ai._loads_relaxed('{"a": "line1\nline2"}') == {"a": "line1\nline2"}


# ---------------------------------------------------------------- inline math flattening

def test_flatten_inline_math_collapses_row_breaks():
    out = ai._flatten_inline_math("$a \\\\ b$")
    assert "\\\\" not in out          # row-break removed
    assert out.startswith("$") and out.endswith("$")


def test_flatten_inline_math_leaves_display_math_alone():
    display = "$$a \\\\ b$$"
    assert ai._flatten_inline_math(display) == display


def test_flatten_question_math_applies_to_parts():
    q = {"question": "$x \\\\ y$", "parts": [{"text": "$c \\\\ d$"}]}
    out = ai._flatten_question_math(q)
    assert "\\\\" not in out["question"]
    assert "\\\\" not in out["parts"][0]["text"]


# ---------------------------------------------------------------- MCQ shuffle

def test_shuffle_mcq_preserves_correct_answer():
    mcq = {
        "options": {"A": "right", "B": "w1", "C": "w2", "D": "w3"},
        "correct": "A",
    }
    for _ in range(20):
        out = ai._shuffle_mcq_options(mcq)
        # The letter marked correct must point at the original correct text.
        assert out["options"][out["correct"]] == "right"
        assert set(out["options"].values()) == {"right", "w1", "w2", "w3"}


# ---------------------------------------------------------------- notes blocks

def test_notes_block_renders_known_topic(monkeypatch):
    monkeypatch.setattr(ai, "_NOTES_INDEX", {
        "t1": {"key_facts": ["fact one"], "terms": {"k": "v"}, "exam_tips": ["tip"]},
    })
    block = ai._notes_block("t1")
    assert "fact one" in block
    assert "Key facts" in block


def test_notes_block_empty_for_unknown_topic(monkeypatch):
    monkeypatch.setattr(ai, "_NOTES_INDEX", {})
    assert ai._notes_block("missing") == ""


# ---------------------------------------------------------------- prompt caching

def test_cached_system_wraps_with_cache_control():
    assert ai._cached_system("SYS") == [{
        "type": "text",
        "text": "SYS",
        "cache_control": {"type": "ephemeral"},
    }]


class _FakeUsage:
    def __init__(self, read=0, write=0, inp=10):
        self.cache_read_input_tokens = read
        self.cache_creation_input_tokens = write
        self.input_tokens = inp


class _FakeBlock:
    def __init__(self, text):
        self.type = "text"
        self.text = text


class _FakeMsg:
    def __init__(self, text="RESULT", usage=None):
        self.content = [_FakeBlock(text)]
        self.usage = usage


class _FakeMessages:
    def __init__(self, usage=None):
        self.last_kwargs = None
        self._usage = usage

    def create(self, **kwargs):
        self.last_kwargs = kwargs
        return _FakeMsg(usage=self._usage)


class _FakeClient:
    def __init__(self, usage=None):
        self.messages = _FakeMessages(usage=usage)


@pytest.fixture
def fake_client(monkeypatch):
    client = _FakeClient(usage=_FakeUsage(read=100, write=0))
    monkeypatch.setattr(ai, "_get_client", lambda: client)
    return client


def test_call_claude_caches_system_prompt_by_default(fake_client):
    out = ai.call_claude("hi", model="m", system="SYSTEM TEXT")
    assert out == "RESULT"
    sent = fake_client.messages.last_kwargs["system"]
    assert sent == ai._cached_system("SYSTEM TEXT")  # wrapped with cache_control


def test_call_claude_can_opt_out_of_caching(fake_client):
    ai.call_claude("hi", model="m", system="SYSTEM TEXT", cache_system=False)
    assert fake_client.messages.last_kwargs["system"] == "SYSTEM TEXT"  # raw string


def test_call_claude_without_system_sends_no_system_key(fake_client):
    ai.call_claude("hi", model="m")
    assert "system" not in fake_client.messages.last_kwargs


def test_log_cache_usage_reports_hits(capsys):
    ai._log_cache_usage(_FakeMsg(usage=_FakeUsage(read=512, write=0)), "course_chat")
    out = capsys.readouterr().out
    assert "cache_read=512" in out


def test_log_cache_usage_silent_without_cache_activity(capsys):
    ai._log_cache_usage(_FakeMsg(usage=_FakeUsage(read=0, write=0)), "x")
    assert capsys.readouterr().out == ""


def test_log_cache_usage_handles_missing_usage():
    ai._log_cache_usage(_FakeMsg(usage=None), "x")  # must not raise
