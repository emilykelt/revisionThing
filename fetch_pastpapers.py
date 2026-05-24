#!/usr/bin/env python3
"""
Generate Cambridge Part IB CS past paper questions for years not yet in pastpapers.json.
Uses Claude to reconstruct questions based on course content and existing examples.
Run from the project root: python3 fetch_pastpapers.py [--years 2018-2021]
"""
import json, os, sys, time, argparse
sys.path.insert(0, os.path.dirname(__file__))
from ai import _get_client, extract_json_from_response

DATA_DIR   = os.path.join(os.path.dirname(__file__), 'data')
PP_FILE    = os.path.join(DATA_DIR, 'pastpapers.json')
COURSES_FILE = os.path.join(DATA_DIR, 'courses.json')
MODEL = 'claude-haiku-4-5-20251001'

# paper_num, [question_nums] — kept as consistent as possible with existing data.
# AI moved from Paper 6 (≤2022) to Paper 7 (≥2023), handled in code below.
COURSE_PAPER_MAP = {
    'concurrent-distributed':  (5, [4, 5]),
    'compiler-construction':   (4, [1, 2]),
    'computation-theory':      (6, [3, 4]),
    'complexity-theory':       (6, [1, 2]),
    'artificial-intelligence': (6, [1, 2]),   # Paper 7 from 2023 onward
    'computer-networking':     (5, [1, 2, 3]),
    'intro-comp-arch':         (5, [6, 7, 8]),
    'prog-c-cpp':              (4, [5, 6]),
    'prolog':                  (4, [4]),
    'cybersecurity':           (4, [7, 8]),
    'econ-law-ethics':         (7, [3, 4]),
    'formal-models-language':  (7, [5, 6]),
    'logic-proof':             (6, [7, 8]),
    'semantics':               (6, [9]),
}

# Courses with uncertain early availability — only go back this far
COURSE_MIN_YEAR = {
    'data-science':    2022,
    'further-graphics': 2021,
    'further-hci':     2021,
}


def load_json(path):
    with open(path) as f:
        return json.load(f)


def save_json(path, data):
    with open(path, 'w') as f:
        json.dump(data, f, indent=2)


def get_topic_info(course_id):
    """Return list of {id, name} for a course's topics."""
    courses = load_json(COURSES_FILE)
    for term_id, term in courses['terms'].items():
        if course_id in term['courses']:
            return [{'id': t['id'], 'name': t['name']}
                    for t in term['courses'][course_id]['topics']]
    return []


def format_example(q):
    lines = []
    for p in q.get('parts', []):
        label = p.get('part') or p.get('label', '?')
        lines.append(f"  ({label}) [{p.get('marks',0)} marks] {p.get('text','')}")
    return '\n'.join(lines)


def generate_question(course_name, course_id, year, paper_num, question_num,
                      topics, example_q=None):
    client = _get_client()
    topic_list = '\n'.join(f'  {t["id"]}: {t["name"]}' for t in topics)
    example_block = ''
    if example_q:
        example_block = (
            f'\nStyle reference — {example_q["year"]} Paper {example_q["paper"]} Q{example_q["question"]}:\n'
            + format_example(example_q)
            + '\n'
        )

    prompt = (
        f'You are helping build a Cambridge Part IB Computer Science revision tool.\n'
        f'Generate a realistic past-paper question for:\n'
        f'  Course: {course_name}\n'
        f'  Year: {year}, Paper {paper_num}, Question {question_num}\n\n'
        f'Available topic IDs for tagging:\n{topic_list}\n'
        f'{example_block}\n'
        f'Requirements:\n'
        f'- Match the style and difficulty of actual Cambridge Part IB tripos questions\n'
        f'- Multi-part: parts (a), (b), (c) … totalling ~20 marks\n'
        f'- Each part tests a focused concept and is independently answerable\n'
        f'- Use realistic Cambridge phrasing ("Define", "Explain", "Give an example", "Compare", etc.)\n\n'
        f'Respond ONLY with JSON (no other text):\n'
        f'{{"parts": [{{"part":"a","marks":4,"topics":["topic-id"],"text":"..."}}, ...]}}'
    )

    try:
        msg = client.messages.create(
            model=MODEL,
            max_tokens=1024,
            messages=[{'role': 'user', 'content': prompt}],
        )
        result = extract_json_from_response(msg.content[0].text)
        if result and 'parts' in result:
            all_topic_ids = {t['id'] for t in topics}
            # Validate / clean topic IDs
            for p in result['parts']:
                p['topics'] = [tid for tid in p.get('topics', []) if tid in all_topic_ids]
            return result['parts']
    except Exception as e:
        print(f'    ERROR: {e}')
    return None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--years', default='2018-2021',
                        help='Year range to fetch, e.g. 2018-2021 or 2019')
    parser.add_argument('--courses', default='',
                        help='Comma-separated course IDs to process (default: all in map)')
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()

    # Parse year range
    if '-' in args.years:
        y0, y1 = args.years.split('-')
        target_years = list(range(int(y0), int(y1) + 1))
    else:
        target_years = [int(args.years)]

    target_courses = [c.strip() for c in args.courses.split(',') if c.strip()] \
                     or list(COURSE_PAPER_MAP.keys())

    pp = load_json(PP_FILE)

    for course_id in target_courses:
        if course_id not in COURSE_PAPER_MAP:
            print(f'[SKIP] {course_id} — not in COURSE_PAPER_MAP')
            continue

        topics = get_topic_info(course_id)
        if not topics:
            print(f'[SKIP] {course_id} — no topics found in courses.json')
            continue

        paper_num, question_nums = COURSE_PAPER_MAP[course_id]
        existing = pp.get(course_id, {})
        tagged = existing.get('tagged_questions', [])
        existing_years = {q['year'] for q in tagged}

        course_min = COURSE_MIN_YEAR.get(course_id, 2018)

        for year in sorted(target_years):
            if year < course_min:
                continue
            if year in existing_years:
                print(f'[SKIP] {course_id} {year} — already present')
                continue

            # AI switched papers in 2023
            if course_id == 'artificial-intelligence' and year >= 2023:
                p = 7
            else:
                p = paper_num

            # Logic and Proof: Q9+Q10 up to 2022, then Q7+Q8 from 2023.
            if course_id == 'logic-proof' and year <= 2022:
                question_nums = [9, 10]
            elif course_id == 'logic-proof':
                question_nums = [7, 8]

            # Fetch an example question from the same course for style
            example_q = tagged[0] if tagged else None
            course_name_lookup = load_json(COURSES_FILE)
            course_name = ''
            for term_id, term in course_name_lookup['terms'].items():
                if course_id in term['courses']:
                    course_name = term['courses'][course_id]['name']
                    break

            for q_num in question_nums:
                print(f'  Generating {course_id} {year} P{p} Q{q_num}…', end=' ', flush=True)
                if args.dry_run:
                    print('(dry-run, skipped)')
                    continue

                parts = generate_question(
                    course_name, course_id, year, p, q_num,
                    topics, example_q
                )
                if parts:
                    all_topic_ids = list({tid for part in parts for tid in part.get('topics', [])})
                    entry = {
                        'year': year,
                        'paper': p,
                        'question': q_num,
                        'pdf_url': f'https://www.cl.cam.ac.uk/teaching/exams/pastpapers/y{year}p{p}q{q_num}.pdf',
                        'topics': all_topic_ids,
                        'parts': parts,
                    }
                    pp.setdefault(course_id, {}).setdefault('tagged_questions', []).append(entry)
                    print('OK')
                else:
                    print('FAILED')

                time.sleep(0.3)  # gentle rate limit

    if not args.dry_run:
        # Sort each course's questions newest-first
        for cid in pp:
            qs = pp[cid].get('tagged_questions', [])
            qs.sort(key=lambda q: (-q['year'], q['paper'], q['question']))
            pp[cid]['tagged_questions'] = qs
            pp[cid]['total_questions'] = len(qs)

        save_json(PP_FILE, pp)
        total = sum(len(pp[c].get('tagged_questions', [])) for c in pp)
        print(f'\nDone. Total questions in pastpapers.json: {total}')
    else:
        print('\nDry run complete — no changes written.')


if __name__ == '__main__':
    main()
