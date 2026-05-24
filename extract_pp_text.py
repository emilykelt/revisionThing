#!/usr/bin/env python3
"""
Extract real question text from Cambridge past paper PDFs and update pastpapers.json.
Each question PDF is a single page containing the full question with sub-parts.
Run from project root: python3 extract_pp_text.py [--course computer-networking] [--dry-run]
"""
import json, os, sys, time, io, re, argparse, urllib.request
sys.path.insert(0, os.path.dirname(__file__))

import pypdf
from ai import _get_client, extract_json_from_response

DATA_DIR = os.path.join(os.path.dirname(__file__), 'data')
PP_FILE  = os.path.join(DATA_DIR, 'pastpapers.json')
MODEL    = 'claude-haiku-4-5-20251001'

BASE_URL = 'https://www.cl.cam.ac.uk/teaching/exams/pastpapers/'


def fetch_pdf_text(pdf_url: str, timeout: int = 20) -> str:
    req = urllib.request.Request(pdf_url, headers={'User-Agent': 'Mozilla/5.0'})
    data = urllib.request.urlopen(req, timeout=timeout).read()
    reader = pypdf.PdfReader(io.BytesIO(data))
    text = '\n'.join(page.extract_text() or '' for page in reader.pages)
    return text.strip()


def parse_question(raw_text: str, course_name: str, year: int, paper: int, qnum: int,
                   existing_topics: dict) -> list:
    """
    Ask Claude to parse raw PDF text into structured parts.
    existing_topics: {part_label: [topic_ids]} from the current data (to preserve tagging)
    Returns list of {label, text, marks, topics}.
    """
    client = _get_client()
    topics_hint = json.dumps(existing_topics) if existing_topics else '{}'
    prompt = (
        f'The following is raw text extracted from a Cambridge Computer Science Tripos past paper PDF.\n'
        f'Course: {course_name} | Year: {year} | Paper: {paper} | Question: {qnum}\n\n'
        f'Raw text:\n{raw_text}\n\n'
        f'Parse this into question parts. For each labelled sub-part (a), (b), (c), etc., extract:\n'
        f'- label: the letter (e.g. "a", "b")\n'
        f'- text: the full question text for that part (preserve all sub-sub-parts like (i), (ii) inline)\n'
        f'- marks: the mark allocation (integer) — look for [N marks] or [N mark]\n'
        f'- topics: use these existing topic tags per part: {topics_hint} (keep them as-is)\n\n'
        f'If there are no labelled parts, return a single entry with label "q", the full text, total marks.\n\n'
        f'Respond ONLY with a JSON array:\n'
        f'[{{"label":"a","text":"...","marks":5,"topics":["topic-id"]}}]'
    )
    msg = client.messages.create(
        model=MODEL,
        max_tokens=2048,
        messages=[{'role': 'user', 'content': prompt}],
    )
    result = extract_json_from_response(msg.content[0].text)
    if result and isinstance(result, list) and len(result) > 0:
        return result
    return []


def load_json(path):
    with open(path) as f:
        return json.load(f)


def save_json(path, data):
    with open(path, 'w') as f:
        json.dump(data, f, indent=2)


def process(course_filter=None, dry_run=False, force=False):
    pp = load_json(PP_FILE)

    for course_id, data in pp.items():
        if course_filter and course_id != course_filter:
            continue

        qs = data.get('tagged_questions', [])
        updated = 0

        for q in qs:
            pdf_url = q.get('pdf_url')
            if not pdf_url:
                continue

            # Skip if already has real text (non-empty parts with actual text content)
            # Force flag bypasses this check
            if not force and q.get('_text_extracted'):
                continue

            ref = f"{q['year']} Paper {q['paper']} Q{q['question']}"
            print(f'  {course_id} | {ref}', end=' ', flush=True)

            # Build existing topics map {label: [topics]} from current parts
            existing_topics = {}
            for p in q.get('parts', []):
                lbl = p.get('label') or p.get('part', '?')
                existing_topics[lbl] = p.get('topics', [])

            try:
                raw_text = fetch_pdf_text(pdf_url)
                if not raw_text or len(raw_text) < 50:
                    print('⚠ empty PDF text, skipping')
                    continue

                if dry_run:
                    print(f'[dry-run] {len(raw_text)} chars extracted')
                    print('  Preview:', repr(raw_text[:200]))
                    continue

                parts = parse_question(raw_text, course_id, q['year'], q['paper'],
                                       q['question'], existing_topics)
                if not parts:
                    print('⚠ parse failed')
                    continue

                # Update the question
                q['parts'] = parts
                q['_text_extracted'] = True
                total_marks = sum(p.get('marks', 0) for p in parts)
                if total_marks > 0:
                    q['total_marks'] = total_marks

                print(f'✓ {len(parts)} parts, {total_marks} marks')
                updated += 1

                # Save after every question so progress is preserved
                save_json(PP_FILE, pp)
                time.sleep(0.3)  # be polite to Cambridge server

            except Exception as e:
                print(f'✗ {e}')
                time.sleep(1)

        if updated and not dry_run:
            print(f'  → {course_id}: {updated} questions updated')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--course', help='Only process this course_id')
    parser.add_argument('--dry-run', action='store_true', help='Download PDFs but do not update JSON')
    parser.add_argument('--force', action='store_true', help='Re-extract even if already done')
    args = parser.parse_args()

    print(f'Extracting real question text from Cambridge PDFs...')
    if args.dry_run:
        print('DRY RUN — no changes will be saved')

    process(course_filter=args.course, dry_run=args.dry_run, force=args.force)
    print('Done.')


if __name__ == '__main__':
    main()
