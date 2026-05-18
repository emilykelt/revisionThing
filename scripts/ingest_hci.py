"""One-off: pull every Further HCI past paper question and tag it to topics.

Reads the existing pastpapers.json for `further-hci`, fetches every missing
question PDF from cl.cam.ac.uk, extracts text via pdftotext, and asks Claude
to structure each one into parts with topic tags. Writes the merged result
back to pastpapers.json (preserving a `.bak` of the original).
"""

import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.request

# Add parent dir so we can import ai.py
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ai import call_claude, extract_json_from_response, EVAL_MODEL

PP_PATH = os.path.join(os.path.dirname(__file__), '..', 'data', 'pastpapers.json')
PP_PATH = os.path.normpath(PP_PATH)

# Every HCI past-paper question listed at the cl.cam.ac.uk index page.
HCI_QUESTIONS = [
    (2025, 7,  9), (2025, 7, 10),
    (2024, 7,  9), (2024, 7, 10),     # already in file — will skip
    (2023, 7,  9), (2023, 7, 10),
    (2022, 7,  8), (2022, 7,  9),
    (2021, 7,  8), (2021, 7,  9),
    (2020, 7,  8), (2020, 7,  9),
    (2019, 7,  8), (2019, 7,  9),
    (2018, 7,  6), (2018, 7,  7),
]

# Topic IDs from courses.json (further-hci)
HCI_TOPICS = [
    ('fhci-theory',      'Theory-Driven Approaches to HCI'),
    ('fhci-visual',      'Design of Visual Displays'),
    ('fhci-goal',        'Goal-Oriented Interaction'),
    ('fhci-smart',       'Designing Smart Systems'),
    ('fhci-efficient',   'Designing Efficient Systems'),
    ('fhci-meaningful',  'Designing Meaningful Systems'),
    ('fhci-evaluation',  'Evaluating Interactive Systems'),
    ('fhci-complex',     'Designing Complex Systems'),
]


def pdf_url(year, paper, q):
    return f'https://www.cl.cam.ac.uk/teaching/exams/pastpapers/y{year}p{paper}q{q}.pdf'


def fetch_pdf_text(url):
    """Download the PDF and run pdftotext -layout."""
    with tempfile.NamedTemporaryFile(suffix='.pdf', delete=False) as tmp:
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'revision-thing/1.0'})
            with urllib.request.urlopen(req, timeout=20) as resp:
                tmp.write(resp.read())
            tmp.flush()
            result = subprocess.run(
                ['pdftotext', '-layout', tmp.name, '-'],
                capture_output=True, timeout=30, check=True,
            )
            return result.stdout.decode('utf-8', errors='replace')
        finally:
            try:
                os.unlink(tmp.name)
            except OSError:
                pass


def parse_question_with_claude(text, year, paper, qnum):
    """Ask Claude to break the question into parts and tag each to topics."""
    topic_lines = '\n'.join(f'  - {tid}: {name}' for tid, name in HCI_TOPICS)
    prompt = (
        'You are tagging a Cambridge Part IB Computer Science past paper '
        'question from the Further Human-Computer Interaction course.\n\n'
        f'Ref: {year} Paper {paper} Q{qnum}\n\n'
        f'Question text (extracted from PDF):\n"""\n{text[:8000]}\n"""\n\n'
        'Available Further HCI topic IDs:\n' + topic_lines + '\n\n'
        'Return ONLY JSON of this exact shape:\n'
        '{\n'
        '  "parts": [\n'
        '    {"label": "a", "text": "the full text of part (a), preserving any LaTeX maths verbatim", '
        '"marks": 4, "topics": ["fhci-theory"]},\n'
        '    {"label": "b", "text": "...", "marks": 6, "topics": ["fhci-smart", "fhci-efficient"]}\n'
        '  ],\n'
        '  "topics": ["fhci-theory", "fhci-smart", "fhci-efficient"]\n'
        '}\n\n'
        'Rules:\n'
        '- Use the SAME labels (a, b, c…) that appear in the question PDF.\n'
        '- For each part, list 1–3 topic IDs from the list above that the part tests. '
        'Use the most specific match. Do not invent topic IDs.\n'
        '- The top-level "topics" array is the union of every part\'s topics, '
        'deduplicated, in the order they first appear.\n'
        '- Marks are integer values from the [N marks] notation. If marks aren\'t '
        'stated, use null.\n'
        '- Strip page headers/footers like "COMPUTER SCIENCE TRIPOS" or page numbers.\n'
        '- Do NOT include the question heading "9 Further Human-Computer Interaction"; '
        'start straight with the question content / setup.\n'
        '- No commentary outside the JSON.'
    )
    response = call_claude(prompt, model=EVAL_MODEL, max_tokens=4000)
    return extract_json_from_response(response)


def main():
    with open(PP_PATH) as f:
        pp = json.load(f)

    course = pp.setdefault('further-hci', {})
    existing = {(q['year'], q['paper'], q['question']) for q in course.get('tagged_questions', [])}
    print(f'Existing: {len(existing)} tagged HCI questions')

    new_tagged = list(course.get('tagged_questions', []))

    for year, paper, qnum in HCI_QUESTIONS:
        if (year, paper, qnum) in existing:
            print(f'  skip {year} P{paper} Q{qnum} (already present)')
            continue
        url = pdf_url(year, paper, qnum)
        print(f'  fetch {year} P{paper} Q{qnum} … ', end='', flush=True)
        try:
            text = fetch_pdf_text(url)
        except Exception as e:
            print(f'FAILED ({e})')
            continue
        if not text.strip():
            print('FAILED (empty)')
            continue
        parsed = parse_question_with_claude(text, year, paper, qnum)
        if not parsed or 'parts' not in parsed:
            print('FAILED (no JSON)')
            continue
        # Normalise
        parts = []
        all_topics = []
        for p in parsed['parts']:
            ptops = [t for t in (p.get('topics') or []) if t.startswith('fhci-')]
            parts.append({
                'label':  p.get('label', '?'),
                'text':   p.get('text', '').strip(),
                'marks':  p.get('marks') if isinstance(p.get('marks'), int) else 0,
                'topics': ptops,
            })
            for t in ptops:
                if t not in all_topics:
                    all_topics.append(t)
        top_topics = parsed.get('topics') or all_topics
        new_tagged.append({
            'year':     year,
            'paper':    paper,
            'question': qnum,
            'pdf_url':  url,
            'topics':   top_topics,
            'parts':    parts,
        })
        print(f'OK ({len(parts)} parts, topics: {", ".join(top_topics)})')
        time.sleep(0.5)  # be polite to the cl.cam.ac.uk server

    # Sort newest-first like the rest of the file
    new_tagged.sort(key=lambda q: (-q['year'], q['paper'], q['question']))

    # topic_frequencies counts every (topic, part) hit.
    # topic_question_frequencies counts each whole question once against its
    # PRIMARY topic (first entry in q['topics']) so the totals sum to ~n_questions.
    freqs = {}
    qfreqs = {}
    for q in new_tagged:
        qtopics = q.get('topics') or []
        if qtopics:
            primary = qtopics[0]
            qfreqs[primary] = qfreqs.get(primary, 0) + 1
        for p in q.get('parts', []):
            for t in p.get('topics', []) or []:
                freqs[t] = freqs.get(t, 0) + 1

    course['tagged_questions'] = new_tagged
    course['total_questions']  = len(new_tagged)
    course['topic_frequencies'] = freqs
    course['topic_question_frequencies'] = qfreqs
    course['past_paper_url'] = course.get('past_paper_url',
        'https://www.cl.cam.ac.uk/teaching/exams/pastpapers/t-FurtherHuman-ComputerInteraction.html')

    # Backup then write
    bak = PP_PATH + '.bak-hci-ingest'
    with open(bak, 'w') as f:
        json.dump(pp, f, indent=2)
    with open(PP_PATH, 'w') as f:
        json.dump(pp, f, indent=2)

    print(f'\nWrote {len(new_tagged)} tagged questions to {PP_PATH}')
    print(f'Backup: {bak}')
    print(f'Topic frequencies: {json.dumps(freqs, indent=2)}')


if __name__ == '__main__':
    main()
