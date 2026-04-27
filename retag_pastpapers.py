#!/usr/bin/env python3
"""
Re-tag past paper question parts based on their actual extracted text.

Many questions had topics inherited from the original AI-reconstructed text and
no longer match the real PDF content. This script asks Claude to re-pick topic
ids per part from the course's full topic list, given the real text.

Usage:
    python3 retag_pastpapers.py                    # all courses
    python3 retag_pastpapers.py --course logic-proof
    python3 retag_pastpapers.py --course logic-proof --year 2023
    python3 retag_pastpapers.py --dry-run          # preview without writing
"""
import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(__file__))
from ai import _get_client, extract_json_from_response

DATA_DIR = os.path.join(os.path.dirname(__file__), 'data')
PP_FILE = os.path.join(DATA_DIR, 'pastpapers.json')
COURSES_FILE = os.path.join(DATA_DIR, 'courses.json')
NOTES_FILE = os.path.join(DATA_DIR, 'notes_index.json')
MODEL = 'claude-haiku-4-5-20251001'


def load_json(path):
    with open(path) as f:
        return json.load(f)


def save_json(path, data):
    with open(path, 'w') as f:
        json.dump(data, f, indent=2)


def get_course_topics(course_id, courses):
    for term in courses['terms'].values():
        if course_id in term['courses']:
            return [{'id': t['id'], 'name': t['name']} for t in term['courses'][course_id]['topics']]
    return []


def topic_hint_block(topic_ids, notes):
    """Short blurb of each topic's name + a couple of key facts to help disambiguation."""
    lines = []
    for tid in topic_ids:
        n = notes.get(tid)
        if not n:
            continue
        facts = n.get('key_facts', [])[:2]
        if facts:
            lines.append(f'  [{tid}]: ' + ' / '.join(f.replace("\n", " ") for f in facts))
    return '\n'.join(lines)


def retag_question(client, course_id, course_name, topics, notes_idx, q):
    """Ask Claude for new topic tags per part. Returns dict {label: [topic_ids]}."""
    topic_list = '\n'.join(f'  {t["id"]}: {t["name"]}' for t in topics)
    valid_ids = [t['id'] for t in topics]
    hints = topic_hint_block(valid_ids, notes_idx)

    parts_block = []
    for p in q.get('parts', []):
        label = p.get('label') or p.get('part') or '?'
        text = (p.get('text') or '').strip()
        parts_block.append(f'  ({label}) [{p.get("marks", 0)} marks] {text}')
    parts_text = '\n'.join(parts_block)

    prompt = (
        f'You are tagging a Cambridge Part IB Computer Science past paper question with the correct '
        f'topic ids from its course.\n\n'
        f'Course: {course_name} ({course_id})\n'
        f'Year/Paper/Q: {q.get("year")} P{q.get("paper")} Q{q.get("question")}\n\n'
        f'Available topic ids (use ONLY these — do not invent):\n{topic_list}\n\n'
        f'{("Topic hints (key facts):\n" + hints + "\n\n") if hints else ""}'
        f'Question parts (real text from the PDF):\n{parts_text}\n\n'
        f'For each part, pick 1–3 topic ids that BEST match what the part actually tests.\n'
        f'Be strict: only include a topic if the part genuinely covers it. Prefer fewer, more accurate tags.\n\n'
        f'Respond ONLY with a JSON object mapping part label → list of topic ids:\n'
        f'{{"a": ["topic-id-1"], "b": ["topic-id-1", "topic-id-2"]}}'
    )

    try:
        msg = client.messages.create(
            model=MODEL,
            max_tokens=512,
            messages=[{'role': 'user', 'content': prompt}],
        )
        result = extract_json_from_response(msg.content[0].text)
        if not isinstance(result, dict):
            return None
        # Filter to valid topic ids only
        cleaned = {}
        for label, ids in result.items():
            if not isinstance(ids, list):
                continue
            cleaned[str(label)] = [t for t in ids if isinstance(t, str) and t in valid_ids]
        return cleaned
    except Exception as e:
        print(f'    ERROR: {e}')
        return None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--course', help='Only process this course_id')
    parser.add_argument('--year', type=int, help='Only process questions from this year')
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()

    pp = load_json(PP_FILE)
    courses = load_json(COURSES_FILE)
    try:
        notes = load_json(NOTES_FILE)
    except FileNotFoundError:
        notes = {}

    client = _get_client()

    total_changed_parts = 0
    total_questions = 0

    for course_id, data in pp.items():
        if args.course and course_id != args.course:
            continue

        topics = get_course_topics(course_id, courses)
        if not topics:
            print(f'[SKIP] {course_id} — no topics in courses.json')
            continue

        for q in data.get('tagged_questions', []):
            if args.year and q.get('year') != args.year:
                continue

            ref = f"{q.get('year')} P{q.get('paper')} Q{q.get('question')}"
            print(f'  {course_id} | {ref}', end=' ', flush=True)
            total_questions += 1

            new_tags = retag_question(client, course_id,
                                       data.get('course_name', course_id),
                                       topics, notes, q)
            if new_tags is None:
                print('— retag failed')
                continue

            changed = 0
            new_question_topic_set = set()
            for p in q.get('parts', []):
                label = p.get('label') or p.get('part') or '?'
                old = sorted(p.get('topics', []))
                new = sorted(new_tags.get(str(label), []))
                if new and new != old:
                    p['topics'] = new
                    changed += 1
                new_question_topic_set.update(p.get('topics', []))

            # Refresh question-level topics list to union of part topics.
            if new_question_topic_set:
                q['topics'] = sorted(new_question_topic_set)

            print(f'— {changed} part(s) updated')
            total_changed_parts += changed

            if not args.dry_run:
                save_json(PP_FILE, pp)
                time.sleep(0.25)

    if not args.dry_run:
        # Recompute per-course topic frequency counts.
        for course_id, data in pp.items():
            freq = {}
            for q in data.get('tagged_questions', []):
                for p in q.get('parts', []):
                    for t in p.get('topics', []):
                        freq[t] = freq.get(t, 0) + 1
            data['topic_frequencies'] = freq
        save_json(PP_FILE, pp)

    print(f'\nDone. {total_questions} question(s) checked, '
          f'{total_changed_parts} part(s) re-tagged.')


if __name__ == '__main__':
    main()
