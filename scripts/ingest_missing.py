"""Fetch every past-paper question that's listed on cl.cam.ac.uk but not yet
in data/pastpapers.json. For each course:
1. Scrape the course's index page for available (year, paper, question) tuples.
2. Subtract from what we have stored.
3. For each missing tuple: download PDF, run pdftotext, ask Claude to parse
   the question into parts with topic tags.
4. Append to pastpapers.json (backup first).
"""

import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ai import call_claude, extract_json_from_response, EVAL_MODEL

PP_PATH = os.path.join(os.path.dirname(__file__), '..', 'data', 'pastpapers.json')
PP_PATH = os.path.normpath(PP_PATH)
COURSES_PATH = os.path.join(os.path.dirname(__file__), '..', 'data', 'courses.json')
COURSES_PATH = os.path.normpath(COURSES_PATH)
HEADERS = {'User-Agent': 'revision-thing/1.0'}


def fetch(url, binary=False, timeout=20):
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = r.read()
    return data if binary else data.decode('utf-8', errors='replace')


MIN_YEAR = 2018  # IB CS paper structure (Papers 4–7) stabilised here; older
                 # years had different course names / question numbering and
                 # would mis-tag to modern topic IDs.


def list_available_questions(index_url):
    """Parse a course's index page for y<year>p<paper>q<qnum>.pdf links.
    Filters out years before MIN_YEAR (older papers used a different
    paper-numbering scheme so the topic mapping wouldn't apply)."""
    try:
        html = fetch(index_url)
    except Exception as e:
        print(f'  ! cannot fetch {index_url}: {e}')
        return []
    refs = set()
    for m in re.finditer(r'y(\d{4})p(\d+)q(\d+)\.pdf', html):
        year = int(m.group(1))
        if year < MIN_YEAR:
            continue
        refs.add((year, int(m.group(2)), int(m.group(3))))
    return sorted(refs)


def pdf_to_text(year, paper, qnum):
    url = f'https://www.cl.cam.ac.uk/teaching/exams/pastpapers/y{year}p{paper}q{qnum}.pdf'
    try:
        pdf_bytes = fetch(url, binary=True)
    except Exception as e:
        print(f'  ! fetch {url}: {e}')
        return None, url
    with tempfile.NamedTemporaryFile(suffix='.pdf', delete=False) as tmp:
        tmp.write(pdf_bytes)
        tmp_path = tmp.name
    try:
        result = subprocess.run(
            ['pdftotext', '-layout', tmp_path, '-'],
            capture_output=True, timeout=30, check=True,
        )
        return result.stdout.decode('utf-8', errors='replace'), url
    except subprocess.SubprocessError as e:
        print(f'  ! pdftotext {url}: {e}')
        return None, url
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


def parse_question(text, course_id, course_name, topics, year, paper, qnum):
    """Ask Claude to break the PDF into parts + tag each to topic IDs."""
    topic_lines = '\n'.join(f'  - {tid}: {name}' for tid, name in topics)
    prompt = (
        f'You are tagging a Cambridge Part IB Computer Science past paper '
        f'question from the {course_name} course.\n\n'
        f'Ref: {year} Paper {paper} Q{qnum}\n\n'
        f'Question text (extracted from PDF):\n"""\n{text[:8000]}\n"""\n\n'
        f'Available {course_name} topic IDs:\n' + topic_lines + '\n\n'
        'Return ONLY JSON of this exact shape:\n'
        '{\n'
        '  "parts": [\n'
        '    {"label": "a", "text": "the full text of part (a), preserving any LaTeX maths verbatim", '
        '"marks": 4, "topics": ["<topic-id>"]},\n'
        '    {"label": "b", "text": "...", "marks": 6, "topics": ["<topic-id>", "<topic-id>"]}\n'
        '  ],\n'
        '  "topics": ["<topic-id>", "<topic-id>"]\n'
        '}\n\n'
        'Rules:\n'
        '- Use the SAME labels (a, b, c…) that appear in the question PDF.\n'
        '- For each part, list 1–3 topic IDs from the list above. Use the '
        'most specific match. Do NOT invent topic IDs.\n'
        '- The top-level "topics" array is the union of every part\'s topics, '
        'deduplicated, in the order they first appear.\n'
        '- Marks are integer values from the [N marks] notation. If marks '
        'aren\'t stated, use 0.\n'
        '- Strip page headers/footers like "COMPUTER SCIENCE TRIPOS" or page '
        'numbers. Strip the question heading like "1 Compiler Construction".\n'
        '- No commentary outside the JSON.'
    )
    response = call_claude(prompt, model=EVAL_MODEL, max_tokens=4000)
    return extract_json_from_response(response)


def main():
    with open(PP_PATH) as f:
        pp = json.load(f)
    with open(COURSES_PATH) as f:
        cdata = json.load(f)

    topic_map = {}
    course_name = {}
    for term in cdata['terms'].values():
        for cid, c in term['courses'].items():
            course_name[cid] = c['name']
            topic_map[cid] = [(t['id'], t['name']) for t in c.get('topics', [])]

    # Backup once at start
    bak = PP_PATH + '.bak-mass-ingest'
    with open(bak, 'w') as f:
        json.dump(pp, f, indent=2)
    print(f'Backup: {bak}\n')

    total_added = total_failed = 0
    for cid in sorted(pp.keys()):
        course = pp[cid]
        existing = {(q['year'], q['paper'], q['question']) for q in course.get('tagged_questions', [])}
        index_url = course.get('past_paper_url')
        cname = course_name.get(cid, cid)
        if not index_url:
            print(f'{cname}: no index URL, skipping')
            continue

        available = list_available_questions(index_url)
        missing = [t for t in available if t not in existing]
        if not missing:
            print(f'{cname}: complete ({len(available)} qs, none missing)')
            continue

        print(f'\n=== {cname} — {len(missing)} missing of {len(available)} ===')
        topics = topic_map.get(cid, [])
        if not topics:
            print(f'  ! no topics in courses.json for {cid}, skipping')
            continue

        added = 0
        for (year, paper, qnum) in missing:
            print(f'  fetch {year} P{paper} Q{qnum} … ', end='', flush=True)
            text, url = pdf_to_text(year, paper, qnum)
            if not text:
                print('FAILED (no text)')
                total_failed += 1
                continue
            parsed = parse_question(text, cid, cname, topics, year, paper, qnum)
            if not parsed or 'parts' not in parsed:
                print('FAILED (no JSON)')
                total_failed += 1
                continue
            allowed_ids = {tid for tid, _ in topics}
            parts = []
            top_topics = []
            for p in parsed['parts']:
                ptops = [t for t in (p.get('topics') or []) if t in allowed_ids]
                parts.append({
                    'label': p.get('label', '?'),
                    'text': (p.get('text') or '').strip(),
                    'marks': p.get('marks') if isinstance(p.get('marks'), int) else 0,
                    'topics': ptops,
                })
                for t in ptops:
                    if t not in top_topics:
                        top_topics.append(t)
            course_topics_top = parsed.get('topics') or top_topics
            course_topics_top = [t for t in course_topics_top if t in allowed_ids]
            course.setdefault('tagged_questions', []).append({
                'year': year,
                'paper': paper,
                'question': qnum,
                'pdf_url': url,
                'topics': course_topics_top,
                'parts': parts,
            })
            added += 1
            print(f'OK ({len(parts)} parts, topics: {", ".join(course_topics_top)})')
            time.sleep(0.4)  # polite to cl.cam.ac.uk

        # Sort + recompute totals after each course
        course['tagged_questions'].sort(key=lambda q: (-q['year'], q['paper'], q['question']))
        course['total_questions'] = len(course['tagged_questions'])
        # Count topic frequencies PER PART (not per question) — a 4-part
        # question that touches the same topic in 3 parts deserves a count
        # of 3, since each part is a separate exam item.
        # qfreqs counts each whole question once against its PRIMARY topic
        # (the first entry in `q['topics']`). Sum of qfreqs ≈ total_questions.
        freqs = {}
        qfreqs = {}
        for q in course['tagged_questions']:
            qtopics = q.get('topics') or []
            if qtopics:
                primary = qtopics[0]
                qfreqs[primary] = qfreqs.get(primary, 0) + 1
            for p in q.get('parts', []):
                for t in p.get('topics', []) or []:
                    freqs[t] = freqs.get(t, 0) + 1
        course['topic_frequencies'] = freqs
        course['topic_question_frequencies'] = qfreqs
        total_added += added

        # Write after each course so a crash mid-run doesn't lose progress
        with open(PP_PATH, 'w') as f:
            json.dump(pp, f, indent=2)
        print(f'  → {cname} now has {course["total_questions"]} tagged questions')

    print()
    print(f'TOTAL ADDED:  {total_added}')
    print(f'TOTAL FAILED: {total_failed}')
    print(f'Backup at:    {bak}')


if __name__ == '__main__':
    main()
