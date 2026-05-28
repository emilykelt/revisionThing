"""Tests for data.py — the confidence model and JSON-backed state layer.

These functions own irreplaceable revision state, so they're the highest-value
thing in the app to pin down. File-touching functions are redirected to tmp
paths via monkeypatch so the real data/ files are never read or written."""
import json

import pytest

import data


# ---------------------------------------------------------------- update_confidence

def test_update_confidence_first_answer_uses_high_alpha():
    # times_tested=0 → alpha = max(0.15, 0.6/1) = 0.6; from 0.0 toward 1.0.
    assert data.update_confidence(0.0, 1.0, 0, 0) == 0.6


def test_update_confidence_clamps_to_unit_interval():
    assert data.update_confidence(1.0, 1.0, 0, 5) == 1.0   # +streak bonus would exceed 1.0
    assert data.update_confidence(0.0, 0.0, 0, 0) == 0.0


def test_update_confidence_alpha_decays_with_experience():
    # Same move, but a seasoned topic should shift less than a brand-new one.
    fresh = data.update_confidence(0.0, 1.0, 0, 0)
    seasoned = data.update_confidence(0.0, 1.0, 20, 0)
    assert fresh > seasoned


def test_streak_bonus_applies_only_on_good_streak():
    with_streak = data.update_confidence(0.5, 0.9, 5, 3)
    without_streak = data.update_confidence(0.5, 0.9, 5, 0)
    assert round(with_streak - without_streak, 3) == 0.03


def test_no_streak_bonus_when_score_low():
    # Streak high but score below 0.6 → no bonus, identical to no-streak.
    assert data.update_confidence(0.5, 0.5, 5, 3) == data.update_confidence(0.5, 0.5, 5, 0)


def test_update_confidence_rounds_to_three_dp():
    val = data.update_confidence(0.333, 0.777, 2, 0)
    assert val == round(val, 3)


# ---------------------------------------------------------------- load/save json

def test_save_then_load_round_trips(tmp_path):
    p = tmp_path / "x.json"
    payload = {"a": 1, "b": [1, 2, 3], "c": "ünïcode"}
    data.save_json(str(p), payload)
    assert data.load_json(str(p)) == payload


def test_load_json_missing_file_returns_none(tmp_path):
    assert data.load_json(str(tmp_path / "nope.json")) is None


def test_save_json_is_pretty_printed(tmp_path):
    p = tmp_path / "x.json"
    data.save_json(str(p), {"a": 1})
    assert "\n" in p.read_text()  # indent=2 → multi-line


# ---------------------------------------------------------------- topic iteration / init

def test_get_all_topics_yields_every_topic(synthetic_courses):
    topics = list(data.get_all_topics(synthetic_courses))
    ids = {t["id"] for t, _, _ in topics}
    assert ids == {"t1", "t2"}
    # course_id and course_name are carried through
    assert all(cid == "c1" and cname == "Course One" for _, cid, cname in topics)


def test_init_knowledge_defaults_every_topic(synthetic_courses):
    k = data.init_knowledge(synthetic_courses)
    assert set(k) == {"t1", "t2"}
    entry = k["t1"]
    assert entry["confidence"] == data.DEFAULT_CONFIDENCE
    assert entry["times_tested"] == 0
    assert entry["streak"] == 0
    assert entry["last_tested"] is None
    assert entry["history"] == [data.DEFAULT_CONFIDENCE]


# ---------------------------------------------------------------- silent update

def test_update_topic_confidence_silent_mutates_and_logs_history():
    knowledge = {"t1": {"confidence": 0.4, "times_tested": 2, "history": [0.4]}}
    data.update_topic_confidence_silent(knowledge, "t1", 1.0)
    entry = knowledge["t1"]
    assert entry["confidence"] > 0.4          # moved toward the score
    assert len(entry["history"]) == 2          # appended
    assert entry["last_tested"] is not None


def test_update_topic_confidence_silent_ignores_unknown_topic():
    knowledge = {}
    data.update_topic_confidence_silent(knowledge, "ghost", 1.0)  # must not raise
    assert knowledge == {}


# ---------------------------------------------------------------- dashboard aggregation

def test_get_dashboard_data_aggregates_means(monkeypatch, synthetic_courses):
    monkeypatch.setattr(data, "load_courses", lambda: synthetic_courses)
    monkeypatch.setattr(data, "load_knowledge", lambda: {
        "t1": {"confidence": 0.8, "history": [0.8]},
        "t2": {"confidence": 0.2, "history": [0.2]},
    })
    dash = data.get_dashboard_data()
    assert dash["overall_confidence"] == 0.5
    course = dash["terms"]["michaelmas"]["courses"]["c1"]
    assert course["confidence"] == 0.5
    assert course["topic_count"] == 2


# ---------------------------------------------------------------- record_answer (integration)

@pytest.fixture
def temp_state(monkeypatch, tmp_path, synthetic_courses):
    """Redirect data.py's file globals at temp paths with synthetic courses."""
    courses_file = tmp_path / "courses.json"
    courses_file.write_text(json.dumps(synthetic_courses))
    knowledge_file = tmp_path / "knowledge.json"
    history_file = tmp_path / "history.json"
    monkeypatch.setattr(data, "COURSES_FILE", str(courses_file))
    monkeypatch.setattr(data, "KNOWLEDGE_FILE", str(knowledge_file))
    monkeypatch.setattr(data, "HISTORY_FILE", str(history_file))
    return {"knowledge": knowledge_file, "history": history_file}


def test_record_answer_updates_knowledge_and_history(temp_state):
    new_conf = data.record_answer(
        topic_id="t1", course_id="c1",
        question="Q?", answer="A.", score=1.0,
        feedback="good", model_solution="sol",
    )
    assert 0.0 < new_conf <= 1.0

    knowledge = json.loads(temp_state["knowledge"].read_text())
    assert knowledge["t1"]["times_tested"] == 1
    assert knowledge["t1"]["streak"] == 1
    assert knowledge["t1"]["confidence"] == new_conf

    history = json.loads(temp_state["history"].read_text())
    assert len(history) == 1
    assert history[0]["score"] == 1.0
    assert history[0]["topic_id"] == "t1"
    assert history[0]["confidence_after"] == new_conf


def test_record_answer_unknown_topic_returns_none(temp_state):
    assert data.record_answer(
        topic_id="ghost", course_id="c1",
        question="Q", answer="A", score=1.0,
        feedback="", model_solution="",
    ) is None


def test_record_mcq_answer_smaller_step_than_full(temp_state):
    # Seed knowledge by recording, then compare an MCQ step's magnitude.
    data.record_answer("t1", "c1", "Q", "A", 1.0, "", "")
    before = json.loads(temp_state["knowledge"].read_text())["t1"]["confidence"]
    after = data.record_mcq_answer("t1", "c1", is_correct=True)
    assert after >= before  # correct MCQ nudges confidence up (or holds near ceiling)
