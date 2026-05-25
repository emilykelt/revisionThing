"""Ingest the questions that `data/tripos_coverage.json` flagged as
`missing_locally` but which tripos.pro confirms exist (some old PDFs aren't
listed on the per-course index page that `ingest_missing.py` walks, but the
PDFs themselves are still hosted at the canonical y{Y}p{P}q{Q}.pdf URL).

Only ingests questions whose tripos `course` name matches a course we
currently track in `data/courses.json` — retired courses (Further Java,
Security, Computer Design, etc.) are skipped.
"""

import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from scripts.ingest_missing import pdf_to_text, parse_question

DATA_DIR = os.path.normpath(os.path.join(os.path.dirname(__file__), '..', 'data'))
PP_PATH = os.path.join(DATA_DIR, 'pastpapers.json')
COURSES_PATH = os.path.join(DATA_DIR, 'courses.json')
COV_PATH = os.path.join(DATA_DIR, 'tripos_coverage.json')


def recompute_course_aggregates(course):
    course['tagged_questions'].sort(key=lambda q: (-q['year'], q['paper'], q['question']))
    course['total_questions'] = len(course['tagged_questions'])
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


def main():
    with open(PP_PATH) as f:
        pp = json.load(f)
    with open(COURSES_PATH) as f:
        cdata = json.load(f)
    with open(COV_PATH) as f:
        cov = json.load(f)

    name_to_id = {}
    topic_map = {}
    course_name = {}
    for term in cdata['terms'].values():
        for cid, c in term['courses'].items():
            name_to_id[c['name']] = cid
            course_name[cid] = c['name']
            topic_map[cid] = [(t['id'], t['name']) for t in c.get('topics', [])]

    missing = cov.get('missing_locally', [])
    targets = []
    for m in missing:
        cid = name_to_id.get(m['course'])
        if not cid or cid not in pp:
            continue  # retired/untracked course
        existing = {(q['year'], q['paper'], q['question'])
                    for q in pp[cid].get('tagged_questions', [])}
        if (m['year'], m['paper'], m['question']) in existing:
            continue  # already in (e.g. from a prior run)
        targets.append((cid, m['year'], int(m['paper']), m['question']))

    if not targets:
        print('Nothing to ingest — every tripos-only question is either already '
              'present or from a retired course.')
        return

    bak = PP_PATH + '.bak-pre-tripos-ingest'
    with open(bak, 'w') as f:
        json.dump(pp, f, indent=2)
    print(f'Backup: {bak}')
    print(f'Ingesting {len(targets)} question(s)…\n')

    touched_courses = set()
    added = failed = 0
    for (cid, year, paper, qnum) in targets:
        cname = course_name.get(cid, cid)
        print(f'  {cname}  {year} P{paper} Q{qnum} … ', end='', flush=True)
        text, url = pdf_to_text(year, paper, qnum)
        if not text:
            print('FAILED (no text)')
            failed += 1
            continue
        topics = topic_map.get(cid, [])
        if not topics:
            print('SKIP (no topics for course)')
            continue
        parsed = parse_question(text, cid, cname, topics, year, paper, qnum)
        if not parsed or 'parts' not in parsed:
            print('FAILED (no JSON from Claude)')
            failed += 1
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
        pp[cid].setdefault('tagged_questions', []).append({
            'year': year,
            'paper': paper,
            'question': qnum,
            'pdf_url': url,
            'topics': course_topics_top,
            'parts': parts,
        })
        touched_courses.add(cid)
        added += 1
        print(f'OK ({len(parts)} parts, topics: {", ".join(course_topics_top)})')
        time.sleep(0.4)

    for cid in touched_courses:
        recompute_course_aggregates(pp[cid])

    with open(PP_PATH, 'w') as f:
        json.dump(pp, f, indent=2)
    print(f'\nADDED:  {added}')
    print(f'FAILED: {failed}')
    print(f'Backup: {bak}')


if __name__ == '__main__':
    main()
