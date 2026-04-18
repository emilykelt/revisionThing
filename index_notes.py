"""
Build data/notes_index.json by extracting per-topic key concepts from course PDFs.
Run once (or re-run to refresh): python3 index_notes.py
"""
import base64
import json
import os
import re
import sys
import time

import anthropic
import httpx

DATA_DIR = os.path.join(os.path.dirname(__file__), 'data')
NOTES_DIR = os.path.join(DATA_DIR, 'notes')
OUTPUT_FILE = os.path.join(DATA_DIR, 'notes_index.json')
COURSES_FILE = os.path.join(DATA_DIR, 'courses.json')
MODEL = 'claude-sonnet-4-6'

# Map course_id -> list of PDF filenames in data/notes/
COURSE_PDFS = {
    'concurrent-distributed': [
        'concurrent-distributed-dist-sys.pdf',
        'concurrent-distributed-conc-sys.pdf',
    ],
    'data-science': [
        'data-science-notes.pdf',
        'data-science-notes-part4.pdf',
    ],
    'econ-law-ethics': [
        'econ-law-ethics-lec1.pdf',
        'econ-law-ethics-lec2.pdf',
        'econ-law-ethics-lec3.pdf',
        'econ-law-ethics-lec4.pdf',
        'econ-law-ethics-lec7.pdf',
        'econ-law-ethics-lec8.pdf',
        'econ-law-ethics-uklaw.pdf',
    ],
    'further-graphics': [
        'further-graphics-lec1.pdf',
        'further-graphics-lec2.pdf',
        'further-graphics-lec3.pdf',
        'further-graphics-lec4.pdf',
        'further-graphics-lec5.pdf',
        'further-graphics-lec6.pdf',
        'further-graphics-lec7.pdf',
        'further-graphics-lec8.pdf',
    ],
    'intro-comp-arch': ['intro-comp-arch-slides.pdf'],
    'prog-c-cpp': ['prog-c-cpp-notes.pdf'],
    'unix-tools': ['unix-tools-notes.pdf'],
    'compiler-construction': [
        'compiler-construction-lec1.pdf',
        'compiler-construction-lec2.pdf',
        'compiler-construction-lec3.pdf',
        'compiler-construction-lec4.pdf',
        'compiler-construction-lec5.pdf',
        'compiler-construction-lec6.pdf',
        'compiler-construction-lec7.pdf',
        'compiler-construction-lec8.pdf',
        'compiler-construction-lec9.pdf',
        'compiler-construction-lec10.pdf',
        'compiler-construction-lec11.pdf',
        'compiler-construction-lec12.pdf',
        'compiler-construction-lec13.pdf',
        'compiler-construction-lec14.pdf',
        'compiler-construction-lec15.pdf',
        'compiler-construction-lec16.pdf',
    ],
    'computation-theory': ['computation-theory-notes.pdf'],
    'computer-networking': ['computer-networking-handouts.pdf'],
    'further-hci': ['further-hci-notes.pdf'],
    'logic-proof': ['logic-proof-notes.pdf'],
    'prolog': ['prolog-intro.pdf', 'prolog-slides.pdf'],
    'semantics': ['semantics-notes.pdf'],
    'complexity-theory': ['complexity-theory-notes.pdf'],
    # No notes yet for: artificial-intelligence, cybersecurity, formal-models-language
}

# For large multi-PDF courses, cap how many PDFs to include to stay within token limits
# (each PDF is read fully; big ones like further-graphics are ~5-15MB each)
MAX_PDFS_PER_COURSE = 4
# Skip any individual PDF larger than this (bytes) to avoid token overflows
MAX_PDF_BYTES = 5 * 1024 * 1024  # 5 MB


def load_pdf_b64(path: str) -> str:
    with open(path, 'rb') as f:
        return base64.standard_b64encode(f.read()).decode('utf-8')


def build_document_block(path: str) -> dict:
    return {
        'type': 'document',
        'source': {
            'type': 'base64',
            'media_type': 'application/pdf',
            'data': load_pdf_b64(path),
        },
    }


def extract_pdf_text(path: str, max_chars: int = 40000) -> str:
    try:
        import pypdf
        reader = pypdf.PdfReader(path)
        parts = []
        total = 0
        for page in reader.pages:
            text = page.extract_text() or ''
            parts.append(text)
            total += len(text)
            if total >= max_chars:
                break
        return '\n'.join(parts)[:max_chars]
    except Exception as e:
        return ''


def extract_json_from_response(text: str):
    if not text:
        return None
    match = re.search(r'```(?:json)?\s*(.*?)\s*```', text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            pass
    start = text.find('{')
    end = text.rfind('}')
    if start != -1 and end != -1:
        try:
            return json.loads(text[start:end + 1])
        except json.JSONDecodeError:
            pass
    return None


def process_course(client, course_id: str, course: dict, pdf_paths: list[str]) -> dict:
    topics = course['topics']
    topic_list = '\n'.join(
        f'- {t["id"]}: {t["name"]} (subtopics: {", ".join(t.get("subtopics", [])[:5])})'
        for t in topics
    )

    # Filter and limit PDFs
    valid_paths = []
    for p in pdf_paths:
        if not os.path.exists(p):
            print(f'  [skip] missing: {os.path.basename(p)}')
            continue
        size = os.path.getsize(p)
        if size < 1000:
            print(f'  [skip] too small ({size}B): {os.path.basename(p)}')
            continue
        if size > MAX_PDF_BYTES:
            print(f'  [skip] too large ({size // 1024}KB): {os.path.basename(p)}')
            continue
        valid_paths.append(p)

    valid_paths = valid_paths[:MAX_PDFS_PER_COURSE]

    if not valid_paths:
        print(f'  [warn] no valid PDFs for {course_id}')
        return {}

    print(f'  Using {len(valid_paths)} PDF(s): {[os.path.basename(p) for p in valid_paths]}')

    # Extract text from PDFs (avoids large base64 uploads that cause API timeouts)
    combined_text = ''
    for p in valid_paths:
        text = extract_pdf_text(p)
        if text:
            combined_text += f'\n\n--- {os.path.basename(p)} ---\n{text}'

    if not combined_text.strip():
        print(f'  [warn] could not extract text from PDFs for {course_id}')
        return {}

    prompt = f"""You are extracting structured revision notes from Cambridge Part IB CS course material.

Course: {course['name']}

Topics to extract notes for (use the exact topic IDs as keys):
{topic_list}

Course notes text:
{combined_text[:35000]}

For EACH topic ID listed above, extract from the provided course notes:
- key_facts: 3-6 specific, testable facts or principles (precise, exam-relevant)
- terms: dict of important technical terms -> concise one-line definitions
- exam_tips: 2-4 points about what examiners commonly test or common mistakes

Respond ONLY with a JSON object, no other text:
{{
  "topic_id_1": {{
    "key_facts": ["fact1", "fact2"],
    "terms": {{"term": "definition"}},
    "exam_tips": ["tip1", "tip2"]
  }},
  ...
}}"""

    try:
        msg = client.messages.create(
            model=MODEL,
            max_tokens=8192,
            messages=[{'role': 'user', 'content': prompt}],
        )
        result = extract_json_from_response(msg.content[0].text)
        if result and isinstance(result, dict):
            return result
        print(f'  [warn] could not parse JSON for {course_id} (stop={msg.stop_reason})')
        print(f'  [debug] first 300 chars: {msg.content[0].text[:300]!r}')
        return {}
    except Exception as e:
        print(f'  [error] {course_id}: {e}')
        return {}


def main():
    key = os.environ.get('ANTHROPIC_API_KEY')
    if not key:
        env_path = os.path.join(os.path.dirname(__file__), '.env')
        try:
            with open(env_path) as f:
                for line in f:
                    line = line.strip()
                    if line.startswith('ANTHROPIC_API_KEY='):
                        key = line.split('=', 1)[1].strip()
                        break
        except Exception:
            pass
    if not key:
        print('ERROR: ANTHROPIC_API_KEY not found')
        sys.exit(1)

    client = anthropic.Anthropic(api_key=key)

    with open(COURSES_FILE) as f:
        courses_data = json.load(f)

    # Load existing index if present (allows resuming)
    if os.path.exists(OUTPUT_FILE):
        with open(OUTPUT_FILE) as f:
            index = json.load(f)
        print(f'Loaded existing index with {len(index)} topics')
    else:
        index = {}

    # Collect all course_ids to process (optionally filter via CLI args)
    filter_courses = set(sys.argv[1:]) if len(sys.argv) > 1 else None

    for term_id, term in courses_data['terms'].items():
        for course_id, course in term['courses'].items():
            if filter_courses and course_id not in filter_courses:
                continue
            if course_id not in COURSE_PDFS:
                print(f'\n[{course_id}] No PDFs mapped — skipping')
                continue

            # Skip if all topics already indexed
            topic_ids = [t['id'] for t in course['topics']]
            if all(tid in index for tid in topic_ids) and not filter_courses:
                print(f'\n[{course_id}] Already indexed — skipping')
                continue

            print(f'\n[{course_id}] {course["name"]}')
            pdf_paths = [os.path.join(NOTES_DIR, p) for p in COURSE_PDFS[course_id]]

            result = process_course(client, course_id, course, pdf_paths)
            if result:
                index.update(result)
                # Save after each course so progress isn't lost
                with open(OUTPUT_FILE, 'w') as f:
                    json.dump(index, f, indent=2)
                print(f'  Saved {len(result)} topic entries (index now has {len(index)})')
            else:
                print(f'  No data returned for {course_id}')

            # Brief pause to respect rate limits
            time.sleep(1)

    print(f'\nDone. Index has {len(index)} topic entries.')
    print(f'Saved to {OUTPUT_FILE}')


if __name__ == '__main__':
    main()
