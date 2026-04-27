#!/usr/bin/env python3
"""
Audit data/pastpapers.json against Cambridge's tripos topic-index pages.

Each course has an authoritative index page at
  https://www.cl.cam.ac.uk/teaching/exams/pastpapers/t-<Slug>.html
listing the real (year, paper, question) tuples. We fetch those and
compare against what we've stored.

Usage:
    python3 audit_pastpapers.py             # report only
    python3 audit_pastpapers.py --repair    # rewrite mis-numbered entries

Repair mode does NOT regenerate question text. After repairing the numbers
re-run extract_pp_text.py --force on the affected course/year to pull the
correct PDF text.
"""
import argparse
import json
import os
import re
import sys
import urllib.request
from collections import defaultdict

DATA_DIR = os.path.join(os.path.dirname(__file__), 'data')
PP_FILE = os.path.join(DATA_DIR, 'pastpapers.json')

# course_id → Cambridge tripos topic page slug
COURSE_SLUG = {
    'concurrent-distributed':   'ConcurrentandDistributedSystems',
    'data-science':             'DataScience',
    'econ-law-ethics':          'EconomicsLawandEthics',
    'further-graphics':         'FurtherGraphics',
    'intro-comp-arch':          'IntroductiontoComputerArchitecture',
    'prog-c-cpp':               'ProgramminginCandC++',
    'unix-tools':               'IntroductiontoUnix',
    'compiler-construction':    'CompilerConstruction',
    'computation-theory':       'ComputationTheory',
    'computer-networking':      'ComputerNetworking',
    'further-hci':              'FurtherHuman-ComputerInteraction',
    'logic-proof':              'LogicandProof',
    'prolog':                   'Prolog',
    'semantics':                'SemanticsofProgrammingLanguages',
    'artificial-intelligence':  'ArtificialIntelligence',
    'complexity-theory':        'ComplexityTheory',
    'cybersecurity':            'Cybersecurity',
    'formal-models-language':   'FormalModelsofLanguage',
}

UA = {'User-Agent': 'Mozilla/5.0 (revision-tool audit)'}
MIN_YEAR = 2018


def load_json(path):
    with open(path) as f:
        return json.load(f)


def save_json(path, data):
    with open(path, 'w') as f:
        json.dump(data, f, indent=2)


def fetch_tripos_links(slug):
    """Return set of (year, paper, question) tuples from a topic page."""
    url = f'https://www.cl.cam.ac.uk/teaching/exams/pastpapers/t-{slug}.html'
    req = urllib.request.Request(url, headers=UA)
    html = urllib.request.urlopen(req, timeout=15).read().decode('utf-8', errors='replace')
    return {
        (int(y), int(p), int(q))
        for y, p, q in re.findall(r'y(\d{4})p(\d+)q(\d+)\.pdf', html)
        if int(y) >= MIN_YEAR
    }


def repair_question(q, new_paper, new_question):
    """Update an entry's paper/question and PDF URL. Wipe stale text-extraction
    flags so extract_pp_text.py will re-fetch on its next run."""
    year = q['year']
    q['paper'] = new_paper
    q['question'] = new_question
    q['pdf_url'] = (
        f'https://www.cl.cam.ac.uk/teaching/exams/pastpapers/'
        f'y{year}p{new_paper}q{new_question}.pdf'
    )
    q.pop('_text_extracted', None)
    # Clear part text — it now points at the wrong question. Keep labels/marks
    # as a hint for extract_pp_text.py to refill.
    for p in q.get('parts', []):
        p['text'] = ''


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--repair', action='store_true',
                        help='Rewrite paper/question numbers where unambiguous')
    parser.add_argument('--course', help='Only audit this course_id')
    args = parser.parse_args()

    pp = load_json(PP_FILE)

    # 1. Detect duplicate (year,paper,question) claims across courses
    claims = defaultdict(list)
    for cid, data in pp.items():
        for q in data.get('tagged_questions', []):
            claims[(q['year'], q['paper'], q['question'])].append(cid)
    dups = {k: v for k, v in claims.items() if len(v) > 1}
    if dups:
        print(f'\n=== {len(dups)} duplicate (year,paper,question) tuples ===')
        for k, v in sorted(dups.items()):
            print(f'  {k}: claimed by {v}')

    # 2. Per-course reconciliation against Cambridge
    print(f'\n=== Per-course reconciliation ===')
    total_repaired = 0
    for cid in sorted(pp.keys()):
        if args.course and cid != args.course:
            continue
        slug = COURSE_SLUG.get(cid)
        if not slug:
            print(f'[SKIP] {cid}: no Cambridge slug')
            continue

        try:
            authoritative = fetch_tripos_links(slug)
        except Exception as e:
            print(f'[ERR ] {cid}: fetch failed — {e}')
            continue

        if not authoritative:
            print(f'[SKIP] {cid}: tripos page has no IB-era entries')
            continue

        local = pp[cid].get('tagged_questions', [])
        local_by_year = defaultdict(list)
        for q in local:
            local_by_year[q['year']].append(q)

        auth_by_year = defaultdict(list)
        for (y, p, qn) in authoritative:
            auth_by_year[y].append((p, qn))

        # For each year present locally, see whether our (paper,q) tuples
        # match the authoritative set. If not, try to remap.
        course_repaired = 0
        course_deleted = 0
        problems = []
        to_delete_ids = []  # python id() of entries to drop
        for year, qs in sorted(local_by_year.items()):
            qs_sorted = sorted(qs, key=lambda q: (q['paper'], q['question']))
            local_pq = [(q['paper'], q['question']) for q in qs_sorted]
            auth_pq = sorted(auth_by_year.get(year, []))

            if local_pq == auth_pq:
                continue

            problems.append(f'  {year}: local {local_pq} vs Cambridge {auth_pq}')

            if not args.repair:
                continue

            if not auth_pq:
                # Cambridge has no exam → all local entries are fabrications
                for q_entry in qs_sorted:
                    to_delete_ids.append(id(q_entry))
                    course_deleted += 1
                    print(f'    DELETE  {cid} {year} '
                          f'P{q_entry["paper"]}Q{q_entry["question"]} '
                          f'(no Cambridge exam)')
                continue

            if len(local_pq) > len(auth_pq):
                # Trim extras — keep first len(auth_pq) and remap them
                keep, drop = qs_sorted[:len(auth_pq)], qs_sorted[len(auth_pq):]
                for q_entry, (new_p, new_qn) in zip(keep, auth_pq):
                    if (q_entry['paper'], q_entry['question']) != (new_p, new_qn):
                        repair_question(q_entry, new_p, new_qn)
                        course_repaired += 1
                        print(f'    REPAIR  {cid} {year} → P{new_p}Q{new_qn}')
                for q_entry in drop:
                    to_delete_ids.append(id(q_entry))
                    course_deleted += 1
                    print(f'    DELETE  {cid} {year} '
                          f'P{q_entry["paper"]}Q{q_entry["question"]} (extra)')
                continue

            if len(local_pq) < len(auth_pq):
                # Remap what we have, leave gap for missing entries
                for q_entry, (new_p, new_qn) in zip(qs_sorted, auth_pq):
                    if (q_entry['paper'], q_entry['question']) != (new_p, new_qn):
                        repair_question(q_entry, new_p, new_qn)
                        course_repaired += 1
                        print(f'    REPAIR  {cid} {year} → P{new_p}Q{new_qn}')
                missing = auth_pq[len(local_pq):]
                print(f'    NOTE    {cid} {year} missing {missing} '
                      '— add via fetch_pastpapers.py')
                continue

            # Equal count, different tuples — pair in order
            for q_entry, (new_p, new_qn) in zip(qs_sorted, auth_pq):
                if (q_entry['paper'], q_entry['question']) != (new_p, new_qn):
                    repair_question(q_entry, new_p, new_qn)
                    course_repaired += 1
                    print(f'    REPAIR  {cid} {year} → P{new_p}Q{new_qn}')

        if to_delete_ids:
            kept = [q for q in pp[cid]['tagged_questions'] if id(q) not in to_delete_ids]
            pp[cid]['tagged_questions'] = kept

        if problems:
            print(f'\n[{cid}]')
            for p in problems:
                print(p)
            if course_repaired or course_deleted:
                print(f'  → repaired {course_repaired}, deleted {course_deleted}')
                total_repaired += course_repaired + course_deleted

    if args.repair and total_repaired > 0:
        # Re-sort & recompute totals
        for cid in pp:
            qs = pp[cid].get('tagged_questions', [])
            qs.sort(key=lambda q: (-q['year'], q['paper'], q['question']))
            pp[cid]['tagged_questions'] = qs
            pp[cid]['total_questions'] = len(qs)
        save_json(PP_FILE, pp)
        print(f'\nWrote {total_repaired} repaired entries to {PP_FILE}.')
        print('Next: re-run extract_pp_text.py --force on affected courses '
              'to pull the corrected PDF text.')
    elif args.repair:
        print('\nNo repairs needed.')


if __name__ == '__main__':
    main()
