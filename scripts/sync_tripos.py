"""Cross-reference local past-paper coverage against tripos.pro.

Talks to the tripos.pro MCP endpoint over plain HTTP (JSON-RPC, SSE response),
so this can be run any time without Claude Code being open.

Produces `data/tripos_coverage.json` with:
  - `by_question`: enrichment from tripos (solutionUrl, examinerComment,
    median/min/max marks, attempts, topics, tripos question id) keyed by
    "YEAR-PAPER-QUESTION" for every local question that tripos also has.
  - `missing_locally`: questions tripos has for Part IB papers 4-7 that we
    don't have in `data/pastpapers.json`.
  - `missing_on_tripos`: local questions tripos doesn't know about.
  - `synced_at`: ISO timestamp.

Usage:
    python3 scripts/sync_tripos.py            # full sync
    python3 scripts/sync_tripos.py --quick    # only enrich existing questions
"""

import json
import os
import sys
import time
import urllib.request
import urllib.error
from datetime import datetime

MCP_URL = 'https://www.tripos.pro/api/mcp/mcp'
DATA_DIR = os.path.normpath(os.path.join(os.path.dirname(__file__), '..', 'data'))
PP_PATH = os.path.join(DATA_DIR, 'pastpapers.json')
OUT_PATH = os.path.join(DATA_DIR, 'tripos_coverage.json')

# Cambridge Part IB CS scope. Older years had different paper structure /
# course names, so we cap the back-window to where Papers 4-7 are stable.
PART_IB_PAPERS = ['4', '5', '6', '7']
MIN_YEAR = 2018
MAX_YEAR = datetime.now().year
MAX_Q_PROBE = 10  # papers have at most 10 questions

_rpc_id = 0


def _next_id():
    global _rpc_id
    _rpc_id += 1
    return _rpc_id


def mcp_call(method, params=None, timeout=20):
    """One JSON-RPC call against the tripos MCP. Parses the SSE response and
    returns the `result` payload (or raises on error)."""
    body = json.dumps({
        'jsonrpc': '2.0',
        'id': _next_id(),
        'method': method,
        'params': params or {},
    }).encode('utf-8')
    req = urllib.request.Request(MCP_URL, data=body, headers={
        'Content-Type': 'application/json',
        'Accept': 'application/json, text/event-stream',
    }, method='POST')
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read().decode('utf-8', errors='replace')
    # Response is SSE: lines like "event: message" and "data: {...}".
    for line in raw.splitlines():
        if line.startswith('data:'):
            payload = json.loads(line[5:].strip())
            if 'error' in payload:
                raise RuntimeError(f'MCP error: {payload["error"]}')
            return payload.get('result')
    raise RuntimeError(f'No data frame in MCP response: {raw[:200]}')


def call_tool(name, arguments):
    """Invoke an MCP tool. Tripos returns its payload as JSON inside a text
    content block, so we parse that out for the caller."""
    result = mcp_call('tools/call', {'name': name, 'arguments': arguments})
    if not result:
        return None
    blocks = result.get('content', [])
    if not blocks or blocks[0].get('type') != 'text':
        return None
    text = blocks[0].get('text', '')
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text  # tripos sometimes returns plain-text error messages


def get_question(paper, year, q):
    """Returns the tripos question dict, or None if not found."""
    try:
        return call_tool('get_question', {
            'paper': str(paper), 'year': int(year), 'questionNumber': int(q),
        })
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise


def local_question_tuples(pp_data):
    """Yield (year, paper, question, course_id) for every locally tracked
    question. A question may appear under multiple courses; we de-dupe by tuple
    but keep one course_id for cross-reference."""
    seen = {}
    for course_id, course in pp_data.items():
        for q in course.get('tagged_questions', []):
            key = (q['year'], int(q['paper']), int(q['question']))
            seen.setdefault(key, course_id)
    for (year, paper, q), course_id in seen.items():
        yield year, paper, q, course_id


def sync(quick=False):
    with open(PP_PATH) as f:
        pp_data = json.load(f)

    local_keys = set()
    by_question = {}
    missing_on_tripos = []

    print(f'Enriching {sum(len(c.get("tagged_questions", [])) for c in pp_data.values())} '
          f'local question rows against tripos.pro…')

    for year, paper, q, course_id in sorted(local_question_tuples(pp_data)):
        key = f'{year}-{paper}-{q}'
        local_keys.add(key)
        try:
            tq = get_question(paper, year, q)
        except Exception as e:
            print(f'  ! {key}: {e}')
            continue
        if not tq or isinstance(tq, str):
            missing_on_tripos.append({'year': year, 'paper': paper, 'question': q, 'course_id': course_id})
            print(f'  - {key} (local only, course {course_id})')
            continue
        by_question[key] = {
            'tripos_id': tq.get('id'),
            'course': tq.get('course'),
            'course_code': tq.get('courseCode'),
            'tripos_part': tq.get('triposPart'),
            'paper_pdf_url': tq.get('url'),
            'solution_url': tq.get('solutionUrl'),
            'examiner_comment': tq.get('examinerComment'),
            'marks': tq.get('marks'),
            'attempts': tq.get('attempts'),
            'topics': tq.get('topics', []),
            'local_course_id': course_id,
        }
        print(f'  ✓ {key} → tripos #{tq.get("id")} '
              f'({tq.get("attempts", 0)} attempts, median {tq.get("marks", {}).get("median", "?")})')
        time.sleep(0.05)

    missing_locally = []
    if not quick:
        print('\nProbing tripos for Part IB questions we don\'t have locally…')
        for year in range(MIN_YEAR, MAX_YEAR + 1):
            for paper in PART_IB_PAPERS:
                consecutive_misses = 0
                for q in range(1, MAX_Q_PROBE + 1):
                    key = f'{year}-{paper}-{q}'
                    if key in local_keys:
                        consecutive_misses = 0
                        continue
                    try:
                        tq = get_question(paper, year, q)
                    except Exception as e:
                        print(f'  ! {key}: {e}')
                        continue
                    if not tq or isinstance(tq, str):
                        consecutive_misses += 1
                        # Papers usually go 1..N contiguously. Two misses in a
                        # row past q=5 means we're past the end.
                        if consecutive_misses >= 2 and q >= 6:
                            break
                        continue
                    consecutive_misses = 0
                    missing_locally.append({
                        'year': year, 'paper': paper, 'question': q,
                        'tripos_id': tq.get('id'),
                        'course': tq.get('course'),
                        'tripos_part': tq.get('triposPart'),
                        'topics': tq.get('topics', []),
                    })
                    print(f'  + {key} (tripos has, we don\'t) — {tq.get("course")}')
                    time.sleep(0.05)

    out = {
        'synced_at': datetime.now().isoformat(),
        'mcp_url': MCP_URL,
        'by_question': by_question,
        'missing_locally': missing_locally,
        'missing_on_tripos': missing_on_tripos,
        'counts': {
            'enriched': len(by_question),
            'local_only': len(missing_on_tripos),
            'tripos_only': len(missing_locally),
        },
    }
    with open(OUT_PATH, 'w') as f:
        json.dump(out, f, indent=2)
    print(f'\nWrote {OUT_PATH}')
    print(f'  Enriched: {out["counts"]["enriched"]}')
    print(f'  Local-only (tripos missing it): {out["counts"]["local_only"]}')
    print(f'  Tripos-only (we\'re missing it): {out["counts"]["tripos_only"]}')


if __name__ == '__main__':
    sync(quick='--quick' in sys.argv)
