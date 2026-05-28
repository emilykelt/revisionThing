# Test Suite

Automated tests for the revision app, covering the parts where a silent bug
would be most expensive: the **confidence model** that owns your revision state,
the **AI response parsing** that turns raw model output into usable data, the
**prompt-caching plumbing**, and the **data-safety hooks**.

**43 tests, all passing** (Python 3.14, pytest 9.0.3).

```
tests/test_ai.py     19 tests
tests/test_data.py   17 tests
tests/test_hooks.py   7 tests
```

---

## Running the tests

```bash
# one-time: install the dev dependency into the venv
.venv/bin/python -m pip install -r requirements-dev.txt

# run everything
.venv/bin/python -m pytest

# verbose / see each test name
.venv/bin/python -m pytest -v

# a single file or test
.venv/bin/python -m pytest tests/test_data.py
.venv/bin/python -m pytest tests/test_data.py::test_streak_bonus_applies_only_on_good_streak
```

Configuration lives in `pytest.ini` (`testpaths = tests`, quiet output by default).

---

## Design principles

1. **Never touch real data.** The app's state lives in hand-edited JSON under
   `data/` (`history.json`, `knowledge.json`, …) and is irreplaceable. Every
   file-touching test redirects `data.py`'s path globals to a pytest `tmp_path`
   via `monkeypatch`, and uses small synthetic course structures. No test reads
   or writes the real `data/` files.
2. **No network calls.** Anything that would hit the Anthropic API is replaced
   with a fake client that records what it was called with. Tests assert on the
   *request we built* (e.g. "was the system prompt wrapped for caching?"), not
   on a live response.
3. **Assert behaviour, not brittle constants.** The confidence-math tests check
   relationships (early answers move more than late ones; the streak bonus is
   exactly +0.03) rather than hard-coding long decimals that would break on a
   harmless rounding tweak. A few clean exact values (e.g. `0.6`, `1.0`, `0.0`)
   are pinned where they're unambiguous.

`tests/conftest.py` puts the repo root on `sys.path` (so `import data` / `ai` /
`config` resolve) and provides the `synthetic_courses` fixture.

---

## `tests/test_data.py` — the confidence model & state layer (17)

This is the highest-value file: `data.py` computes and persists how well you
know each topic.

### `update_confidence` — the core learning algorithm
The exponential-moving-average update with a decaying learning rate and a
streak bonus.

| Test | What it pins down |
|------|-------------------|
| `test_update_confidence_first_answer_uses_high_alpha` | First answer (`times_tested=0`) uses `alpha=0.6`; `0.0 →` score `1.0` lands at exactly `0.6`. |
| `test_update_confidence_clamps_to_unit_interval` | Result is clamped to `[0, 1]` — a streak bonus can't push confidence above `1.0`, and a `0.0` answer from `0.0` stays `0.0`. |
| `test_update_confidence_alpha_decays_with_experience` | The same answer moves a *fresh* topic more than a *seasoned* one (learning rate decays as `times_tested` grows). |
| `test_streak_bonus_applies_only_on_good_streak` | A streak ≥ 3 with a good score adds exactly `+0.03` over the no-streak result. |
| `test_no_streak_bonus_when_score_low` | The bonus is suppressed when the score is below `0.6`, even on a long streak. |
| `test_update_confidence_rounds_to_three_dp` | Output is rounded to 3 decimal places. |

### JSON persistence
| Test | What it pins down |
|------|-------------------|
| `test_save_then_load_round_trips` | `save_json` → `load_json` returns the original payload, including unicode. |
| `test_load_json_missing_file_returns_none` | Loading a non-existent file returns `None` (not an exception). |
| `test_save_json_is_pretty_printed` | Files are written `indent=2` (multi-line, diff-friendly). |

### Topic iteration & initialisation
| Test | What it pins down |
|------|-------------------|
| `test_get_all_topics_yields_every_topic` | `get_all_topics` walks every term/course/topic and carries `course_id` + `course_name` through. |
| `test_init_knowledge_defaults_every_topic` | A fresh knowledge map seeds every topic with default confidence, zero counts, `None` last-tested, and a one-element history. |

### Silent (no-log) confidence updates
| Test | What it pins down |
|------|-------------------|
| `test_update_topic_confidence_silent_mutates_and_logs_history` | Updates confidence in place and appends to that topic's history (used for extra topics tagged in a past-paper question). |
| `test_update_topic_confidence_silent_ignores_unknown_topic` | An unknown topic id is a no-op, not a crash. |

### Dashboard aggregation
| Test | What it pins down |
|------|-------------------|
| `test_get_dashboard_data_aggregates_means` | Course/term/overall confidence are correct means (0.8 & 0.2 → 0.5 overall), and `topic_count` is right. Uses monkeypatched `load_courses`/`load_knowledge`. |

### `record_answer` / `record_mcq_answer` — full integration
Run end-to-end against temp `courses.json` / `knowledge.json` / `history.json`.
| Test | What it pins down |
|------|-------------------|
| `test_record_answer_updates_knowledge_and_history` | Recording an answer bumps `times_tested`, increments the streak, writes the new confidence to knowledge, and appends one history entry with the right score and `confidence_after`. |
| `test_record_answer_unknown_topic_returns_none` | An unknown topic returns `None` and records nothing. |
| `test_record_mcq_answer_smaller_step_than_full` | A correct MCQ nudges confidence in the right direction (lighter weight than a full written answer). |

---

## `tests/test_ai.py` — AI response parsing & caching plumbing (19)

`ai.py` has to survive imperfect model output (LaTeX backslashes, raw newlines
inside JSON strings, code fences) and build correctly-cached requests.

### JSON extraction
| Test | What it pins down |
|------|-------------------|
| `test_extract_json_from_code_fence` | Parses JSON inside a ```` ```json ```` fence. |
| `test_extract_json_from_loose_braces` | Recovers a JSON object embedded in surrounding prose. |
| `test_extract_json_none_and_garbage` | `None` input and no-JSON input both return `None`. |
| `test_extract_json_array` | Extracts a top-level JSON array (used by MCQ/flashcard generation). |

### JSON repair (the relaxed loader)
| Test | What it pins down |
|------|-------------------|
| `test_repair_invalid_latex_backslash` | An invalid escape like `\alpha` (common when the model emits LaTeX) is repaired so the JSON parses, preserving the literal backslash. |
| `test_repair_unescaped_control_chars` | A literal newline inside a JSON string value (illegal JSON, but the model emits it) is escaped and parsed correctly. |

### Inline-math flattening
| Test | What it pins down |
|------|-------------------|
| `test_flatten_inline_math_collapses_row_breaks` | `\\` row-breaks inside inline `$…$` are collapsed (they'd otherwise render as broken stacked fragments mid-sentence in KaTeX). |
| `test_flatten_inline_math_leaves_display_math_alone` | Display math (`$$…$$`) is left untouched — stacking is fine there. |
| `test_flatten_question_math_applies_to_parts` | Flattening is applied across both the question stem and each part. |

### MCQ option shuffling
| Test | What it pins down |
|------|-------------------|
| `test_shuffle_mcq_preserves_correct_answer` | After shuffling (20 runs), the letter marked `correct` always points at the original correct text and no option is lost — so the answer isn't always "A". |

### Notes blocks
| Test | What it pins down |
|------|-------------------|
| `test_notes_block_renders_known_topic` | A topic with notes renders its key facts into the prompt snippet. |
| `test_notes_block_empty_for_unknown_topic` | A topic with no notes yields an empty string (no stray headers). |

### Prompt caching
| Test | What it pins down |
|------|-------------------|
| `test_cached_system_wraps_with_cache_control` | `_cached_system` produces the exact `cache_control: ephemeral` text block. |
| `test_call_claude_caches_system_prompt_by_default` | `call_claude(..., system=...)` sends the system prompt wrapped for caching. |
| `test_call_claude_can_opt_out_of_caching` | `cache_system=False` sends the raw string (no cache block). |
| `test_call_claude_without_system_sends_no_system_key` | With no system prompt, no `system` key is sent at all. |
| `test_log_cache_usage_reports_hits` | Cache hits are logged (`cache_read=512`). |
| `test_log_cache_usage_silent_without_cache_activity` | No log line when nothing was cached (stays quiet). |
| `test_log_cache_usage_handles_missing_usage` | A response with no `usage` object doesn't crash the logger. |

The caching tests use a `_FakeClient` that records the kwargs passed to
`messages.create`, so they verify the *request shape* without any network call.

---

## `tests/test_hooks.py` — data-safety hooks (7)

Exercises the two Claude Code hook scripts as real subprocesses (piping a JSON
payload on stdin, exactly as the harness invokes them).

### Backup hook (`PreToolUse`)
| Test | What it pins down |
|------|-------------------|
| `test_backup_snapshots_existing_data_file` | Editing a `data/*.json` first copies it to `data/.backups/<name>.<timestamp>` with identical contents. |
| `test_backup_ignores_non_data_file` | A non-`data/` file (e.g. `app.py`) is left alone — no backup dir created. |
| `test_backup_skips_new_file` | A not-yet-existing file (a `Write` creating it) is a no-op. |
| `test_backup_prunes_to_keep_limit` | After 13 edits, at most 10 snapshots are kept (older ones pruned). |

### Validation hook (`PostToolUse`)
| Test | What it pins down |
|------|-------------------|
| `test_validate_passes_valid_json` | Valid JSON exits 0. |
| `test_validate_blocks_broken_json` | Broken JSON exits 2 with an `Invalid JSON` message (fed back to Claude to fix). |
| `test_validate_ignores_non_data_file` | A non-`data/` file is ignored. |

---

## Maintenance notes

- **Adding a new AI helper?** If it parses model output, add a case to
  `test_ai.py` covering the messy-input path — that's where bugs hide.
- **Changing the confidence formula?** Update the relationship tests in
  `test_data.py`; prefer asserting direction/bounds over exact decimals.
- **`pytest` not found?** It's a dev-only dependency — `pip install -r
  requirements-dev.txt` into `.venv`. The app itself (`requirements.txt`)
  doesn't need it.
