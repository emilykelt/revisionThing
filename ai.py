import json
import re
import os
import random

import anthropic

QUESTION_MODEL = 'claude-haiku-4-5-20251001'
EVAL_MODEL = 'claude-sonnet-4-6'

_client = None
_PASTPAPERS = None
_NOTES_INDEX = None


def _get_client():
    global _client
    if _client is None:
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
        _client = anthropic.Anthropic(api_key=key)
    return _client


def _load_notes_index():
    global _NOTES_INDEX
    if _NOTES_INDEX is None:
        try:
            path = os.path.join(os.path.dirname(__file__), 'data', 'notes_index.json')
            with open(path) as f:
                _NOTES_INDEX = json.load(f)
        except Exception:
            _NOTES_INDEX = {}
    return _NOTES_INDEX


def _notes_block(topic_id: str) -> str:
    """Return a compact notes snippet for a topic, or empty string if none."""
    idx = _load_notes_index()
    entry = idx.get(topic_id)
    if not entry:
        return ''
    parts = []
    facts = entry.get('key_facts', [])
    if facts:
        parts.append('Key facts: ' + ' | '.join(facts[:4]))
    terms = entry.get('terms', {})
    if terms:
        term_str = ', '.join(f'{k}: {v}' for k, v in list(terms.items())[:4])
        parts.append('Terms: ' + term_str)
    tips = entry.get('exam_tips', [])
    if tips:
        parts.append('Exam focus: ' + ' | '.join(tips[:2]))
    return '\nCourse notes:\n' + '\n'.join(parts) if parts else ''


def _notes_block_multi(topic_ids: list) -> str:
    """Return combined notes for multiple topics (for MCQ generation)."""
    idx = _load_notes_index()
    lines = []
    for tid in topic_ids:
        entry = idx.get(tid)
        if not entry:
            continue
        facts = entry.get('key_facts', [])
        if facts:
            lines.append(f'[{tid}] ' + ' | '.join(facts[:3]))
    return '\nCourse notes excerpts:\n' + '\n'.join(lines) if lines else ''


def _load_pastpapers():
    global _PASTPAPERS
    if _PASTPAPERS is None:
        try:
            pp_path = os.path.join(os.path.dirname(__file__), 'data', 'pastpapers.json')
            with open(pp_path) as f:
                _PASTPAPERS = json.load(f)
        except Exception:
            _PASTPAPERS = {}
    return _PASTPAPERS


def _get_past_paper_questions(course_id, topic_id):
    """
    Return list of real past paper questions that cover this topic.
    Each entry: {ref, year, paper, question_num, pdf_url, parts, style_examples}
    """
    pp = _load_pastpapers()
    course_data = pp.get(course_id, {})
    results = []
    for q in course_data.get('tagged_questions', []):
        relevant_parts = [p for p in q.get('parts', []) if topic_id in p.get('topics', [])]
        if relevant_parts:
            ref = f"{q['year']} Paper {q['paper']} Q{q['question']}"
            results.append({
                'ref': ref,
                'year': q['year'],
                'paper': q['paper'],
                'question_num': q['question'],
                'pdf_url': q.get('pdf_url'),
                'parts': relevant_parts,  # only the parts relevant to this topic
                'all_parts': q.get('parts', []),  # full question
            })
    return results


def call_claude(prompt, model, max_tokens=1024):
    try:
        client = _get_client()
        msg = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            messages=[{'role': 'user', 'content': prompt}],
        )
        return msg.content[0].text
    except Exception as e:
        print(f'[claude] call_claude error: {e}')
        return None


SUPPORTED_IMAGE_MEDIA = {'image/png', 'image/jpeg', 'image/jpg', 'image/webp', 'image/gif'}


def _call_claude_with_images(prompt, images, model, max_tokens=1024):
    """images: list of {'media_type': str, 'data': str (base64, no prefix)}.
    At most ~6 images per call to stay under context limits."""
    try:
        client = _get_client()
        content = []
        for img in (images or [])[:6]:
            mt = img.get('media_type') or 'image/png'
            if mt == 'image/jpg':
                mt = 'image/jpeg'
            if mt not in SUPPORTED_IMAGE_MEDIA:
                continue
            data = img.get('data') or ''
            if not data:
                continue
            content.append({
                'type': 'image',
                'source': {'type': 'base64', 'media_type': mt, 'data': data},
            })
        content.append({'type': 'text', 'text': prompt})
        msg = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            messages=[{'role': 'user', 'content': content}],
        )
        return msg.content[0].text
    except Exception as e:
        print(f'[claude] call_claude_with_images error: {e}')
        return None


_JSON_VALID_ESC = set('"\\/bfnrtu')


def _repair_latex_backslashes(s: str) -> str:
    """AI sometimes emits LaTeX like "$\\frac{x}{y}$" but writes it as "$\frac{x}{y}$"
    in JSON, which makes json.loads choke (\f is form feed, \r is CR, etc.).
    This doubles any backslash that isn't already a valid JSON escape so the
    LaTeX command survives parsing intact."""
    out = []
    i = 0
    while i < len(s):
        c = s[i]
        if c == '\\' and i + 1 < len(s) and s[i + 1] not in _JSON_VALID_ESC:
            out.append('\\\\')
        else:
            out.append(c)
        i += 1
    return ''.join(out)


def _repair_unescaped_control_chars(s: str) -> str:
    """Escape raw newlines, tabs, and CRs that appear inside JSON string values.
    LLMs frequently emit `"feedback": "line one<NL>line two"` with a real newline
    instead of `\\n`, which breaks json.loads (control chars are illegal in JSON
    string literals)."""
    out = []
    in_string = False
    escaped = False
    for c in s:
        if escaped:
            out.append(c)
            escaped = False
            continue
        if c == '\\':
            out.append(c)
            escaped = True
            continue
        if c == '"':
            in_string = not in_string
            out.append(c)
            continue
        if in_string and c == '\n':
            out.append('\\n')
        elif in_string and c == '\r':
            out.append('\\r')
        elif in_string and c == '\t':
            out.append('\\t')
        else:
            out.append(c)
    return ''.join(out)


def _loads_relaxed(s: str):
    for repair in (
        lambda x: x,
        _repair_latex_backslashes,
        _repair_unescaped_control_chars,
        lambda x: _repair_unescaped_control_chars(_repair_latex_backslashes(x)),
    ):
        try:
            return json.loads(repair(s))
        except json.JSONDecodeError:
            continue
    return None


def extract_json_array_from_response(text):
    if text is None:
        return None
    # Try JSON code fence
    match = re.search(r'```(?:json)?\s*\n?(\[.*?\])\s*\n?```', text, re.DOTALL)
    if match:
        result = _loads_relaxed(match.group(1))
        if result is not None:
            return result
    # Find first [ and last ]
    start = text.find('[')
    end = text.rfind(']')
    if start != -1 and end != -1 and end > start:
        result = _loads_relaxed(text[start:end + 1])
        if result is not None:
            return result
    return None


def _salvage_feedback(text):
    """Last-resort extractor for when JSON parsing of an eval response fails.
    Returns the value of the `feedback` field if we can find it textually, or
    the response with the ```json fence stripped, so the UI never shows a raw
    code-fenced JSON blob to the student."""
    if not text:
        return ''
    fence = re.search(r'```(?:json)?\s*\n?(.*?)\n?```', text, re.DOTALL)
    body = fence.group(1) if fence else text
    fb = re.search(r'"feedback"\s*:\s*"((?:[^"\\]|\\.)*)"', body, re.DOTALL)
    if fb:
        return fb.group(1).encode('utf-8').decode('unicode_escape', errors='replace')
    return body.strip()


def extract_json_from_response(text):
    if text is None:
        return None
    # Try JSON code fence
    match = re.search(r'```(?:json)?\s*\n?(.*?)\n?```', text, re.DOTALL)
    if match:
        result = _loads_relaxed(match.group(1))
        if result is not None:
            return result
    # Find first { and last } and parse everything between
    start = text.find('{')
    end = text.rfind('}')
    if start != -1 and end != -1:
        result = _loads_relaxed(text[start:end + 1])
        if result is not None:
            return result
    return None


_STACK_ENV_RE = re.compile(
    r'\\begin\{(matrix|pmatrix|bmatrix|vmatrix|Vmatrix|aligned|align|cases|array|gathered)\*?\}'
    r'(?:\{[^}]*\})?'
    r'(.*?)'
    r'\\end\{\1\*?\}',
    re.DOTALL,
)


def _flatten_inline_math(text: str) -> str:
    """Defensive sanitiser: when the AI puts a matrix/aligned/cases inside an
    inline $...$ span, KaTeX renders each row stacked vertically, which
    visually shatters the surrounding sentence. Inside inline math only,
    unwrap those environments and replace `\\\\` row breaks with a single
    space so the expression collapses onto one line. Display math ($$...$$)
    is left alone — stacked layouts are fine there."""
    if not text or '$' not in text:
        return text

    def fix_inline(match):
        body = match.group(1)
        # Unwrap stack environments, keeping their contents
        body = _STACK_ENV_RE.sub(lambda m: m.group(2), body)
        # Collapse explicit row breaks to spaces
        body = re.sub(r'\\\\\s*', ' ', body)
        # Squeeze whitespace
        body = re.sub(r'\s+', ' ', body).strip()
        return f'${body}$'

    # First protect display math by tokenising
    placeholders = []
    def protect(m):
        placeholders.append(m.group(0))
        return f'\x00DISP{len(placeholders) - 1}\x00'

    protected = re.sub(r'\$\$[\s\S]+?\$\$', protect, text)
    fixed = re.sub(r'\$([^$\n]+?)\$', fix_inline, protected)
    for i, raw in enumerate(placeholders):
        fixed = fixed.replace(f'\x00DISP{i}\x00', raw)
    return fixed


def _flatten_question_math(q):
    """Apply _flatten_inline_math across a generated question dict."""
    if not isinstance(q, dict):
        return q
    if isinstance(q.get('question'), str):
        q['question'] = _flatten_inline_math(q['question'])
    if isinstance(q.get('parts'), list):
        for p in q['parts']:
            if isinstance(p, dict) and isinstance(p.get('text'), str):
                p['text'] = _flatten_inline_math(p['text'])
    return q


def generate_question(topic_name, subtopics, course_name, confidence,
                      course_id=None, topic_id=None, attempt=0, ai_only=False):

    # --- Try to serve a real past paper question first (unless ai_only) ---
    pp_questions = [] if ai_only else (
        _get_past_paper_questions(course_id, topic_id) if (course_id and topic_id) else []
    )
    if pp_questions:
        # Cycle through available real questions on successive attempts
        q = pp_questions[attempt % len(pp_questions)]
        # Use the full question's parts, not just the topic-relevant subset
        parts = q['all_parts']
        return {
            'parts': parts,
            'total_marks': sum(p.get('marks', 0) for p in parts),
            'difficulty': 'high' if sum(p.get('marks', 0) for p in parts) >= 15 else 'medium',
            'source': q['ref'],
            'pdf_url': q.get('pdf_url'),
            'is_actual_past_paper': True,
        }

    # --- Fall back to AI generation with style examples ---
    notes_snippet = _notes_block(topic_id) if topic_id else ''

    # Rotate which subtopics are emphasised on successive attempts
    n = len(subtopics)
    if n > 2 and attempt > 0:
        offset = (attempt * 2) % n
        rotated = subtopics[offset:] + subtopics[:offset]
    else:
        rotated = subtopics

    # Vary the question style hint so re-tries feel different
    style_hints = [
        'definition/explanation',
        'comparison or contrast',
        'worked example or trace',
        'advantages and disadvantages',
        'design or algorithm sketch',
    ]
    style = style_hints[attempt % len(style_hints)]

    math_rule = (
        'Use LaTeX for any maths: inline with $...$ and display with $$...$$. '
        'CRITICAL: keep inline math on one line — never use \\\\ row breaks, '
        '\\begin{matrix}, \\begin{pmatrix}, \\begin{aligned}, \\begin{cases}, '
        '\\begin{array}, or \\begin{gathered} inside $...$ (KaTeX stacks them '
        'vertically and shatters the sentence). For expressions like sums, '
        'products, or set definitions in prose, write $T_1 + T_2$ or '
        '$\\{x : P(x)\\}$ inline. Only use $$...$$ blocks for genuinely '
        'multi-line content (proof trees, derivations, multi-step equations).'
    )

    if confidence < 0.3:
        difficulty = 'low'
        prompt = (
            f'Cambridge Part IB CS examiner. Generate ONE short {style} question.\n'
            f'Course: {course_name} | Topic: {topic_name}\n'
            f'Focus subtopics: {", ".join(rotated[:4])}'
            f'{notes_snippet}\n\n'
            f'Marks must be 1–10 (simple recall = 2–3, explanation = 4–6, analysis = 7–10).\n'
            f'{math_rule}\n'
            f'Respond ONLY with this JSON (no other text):\n'
            f'{{"question": "full question text", "difficulty": "low", "marks": 4}}'
        )
    else:
        difficulty = 'medium' if confidence < 0.7 else 'high'
        n_parts = 2 if confidence < 0.7 else 3
        prompt = (
            f'Cambridge Part IB CS examiner. Generate a {difficulty}-difficulty tripos question '
            f'with {n_parts}–4 labelled parts: (a), (b), (c) etc. Style: {style}.\n'
            f'Course: {course_name} | Topic: {topic_name}\n'
            f'Focus subtopics: {", ".join(rotated[:6])}'
            f'{notes_snippet}\n\n'
            f'Each part must be answerable independently. Marks per part: 1–10. Total marks up to 20.\n'
            f'{math_rule}\n'
            f'Respond ONLY with this JSON (no other text):\n'
            f'{{"parts": [{{"label": "a", "text": "question text for part a", "marks": 4}}, '
            f'{{"label": "b", "text": "question text for part b", "marks": 8}}], '
            f'"difficulty": "{difficulty}"}}'
        )

    response = call_claude(prompt, model=QUESTION_MODEL)
    result = extract_json_from_response(response)

    if result:
        if 'parts' in result:
            result['total_marks'] = sum(p.get('marks', 0) for p in result['parts'])
        return _flatten_question_math(result)

    # Fallback: single question
    return {
        'question': (
            f'({style.capitalize()}) Explain {topic_name} in {course_name}. '
            f'Cover: {", ".join(rotated[:3])}.'
        ),
        'difficulty': difficulty,
        'marks': 8,
        'fallback': True,
    }


def generate_hint(question, topic_name, course_name, topic_id=None):
    notes_snippet = _notes_block(topic_id) if topic_id else ''
    prompt = (
        f'Cambridge CS supervisor. A student is stuck on this question.\n'
        f'Course: {course_name} | Topic: {topic_name}\n'
        f'Question:\n{question}\n'
        f'{notes_snippet}\n\n'
        f'Give a concise hint (3-5 sentences) that helps the student approach the problem. '
        f'If the question asks the student to apply, trace, or use a specific method, algorithm, or '
        f'protocol (e.g. Dijkstra\'s, slow start, spanning tree, type inference), briefly explain the '
        f'key steps of that method so the student knows how to proceed. '
        f'Otherwise, point them toward the key concept or technique to use without giving away the answer. '
        f'Do NOT restate or rephrase the question. Do NOT solve it. '
        f'Respond with only the hint text, no preamble.'
    )
    response = call_claude(prompt, model=QUESTION_MODEL, max_tokens=300)
    return response or ''


def evaluate_answer(question, answer, topic_name, course_name, part_label=None, marks_available=None, full_context=None, images=None):
    images = images or []
    has_text = bool(answer and answer.strip())
    if not has_text and not images:
        return {
            'score': 0.0,
            'marks_awarded': 0,
            'marks_available': marks_available or 0,
            'feedback': 'No answer provided.',
            'model_solution': '',
            'key_gaps': [topic_name],
        }

    part_context = f' (part {part_label})' if part_label else ''
    marks_context = f' [{marks_available} marks available]' if marks_available else ''
    context_block = ''
    if full_context:
        context_block = f'Full question context (for reference):\n{full_context}\n\n'
    image_note = (
        f'\n\nThe student attached {len(images)} image(s) below — these may be diagrams, '
        f'handwritten working, or pasted figures. Treat them as part of the answer.'
        if images else ''
    )
    prompt = (
        f'Cambridge CS supervisor marking a tripos answer.\n'
        f'Course: {course_name} | Topic: {topic_name}{part_context}{marks_context}\n'
        f'{context_block}'
        f'Question being marked: {question}\n'
        f'Student answer (text): {answer or "(see attached image(s))"}'
        f'{image_note}\n\n'
        f'Use LaTeX for any maths ($...$ inline, $$...$$ display).\n'
        f'Respond ONLY with this JSON:\n'
        + (
            f'{{"marks_awarded": 3, "marks_available": {marks_available}, "feedback": "...", "model_solution": "...", "key_gaps": ["concept1"]}}\n'
            f'Award marks_awarded as an integer 0–{marks_available}. Be specific about what was good and what was missing.'
            if marks_available else
            f'{{"score": 0.7, "feedback": "...", "model_solution": "...", "key_gaps": ["concept1"]}}\n'
            f'Score 0.0–1.0. Be specific about what was good and what was missing.'
        )
    )

    response = _call_claude_with_images(prompt, images, model=EVAL_MODEL) if images \
        else call_claude(prompt, model=EVAL_MODEL)
    result = extract_json_from_response(response)

    if result:
        if marks_available and 'marks_awarded' in result:
            awarded = max(0, min(marks_available, int(round(float(result['marks_awarded'])))))
            result['marks_awarded'] = awarded
            result['marks_available'] = marks_available
            result['score'] = awarded / marks_available
            return result
        if 'score' in result:
            score = max(0.0, min(1.0, float(result['score'])))
            result['score'] = score
            if marks_available:
                result['marks_awarded'] = int(round(score * marks_available))
                result['marks_available'] = marks_available
            return result

    # Parsing failed entirely. Try to salvage a readable feedback string from
    # the raw response rather than dumping the broken JSON blob into the UI.
    salvaged = _salvage_feedback(response)
    return {
        'score': 0.5,
        'marks_awarded': int(round(0.5 * marks_available)) if marks_available else None,
        'marks_available': marks_available,
        'feedback': salvaged or 'Unable to evaluate.',
        'model_solution': '',
        'key_gaps': [],
    }


def _shuffle_mcq_options(mcq):
    """Randomly shuffle the A/B/C/D options so the correct answer isn't always A."""
    letters = ['A', 'B', 'C', 'D']
    correct_text = mcq['options'][mcq['correct']]

    # Build a list of all option texts, shuffle them
    texts = [mcq['options'][l] for l in letters]
    random.shuffle(texts)

    # Reassign to A/B/C/D and find where the correct answer landed
    new_options = {l: texts[i] for i, l in enumerate(letters)}
    new_correct = next(l for l in letters if new_options[l] == correct_text)

    return {**mcq, 'options': new_options, 'correct': new_correct}


def generate_mcqs(topic_infos, count=8, past_paper_context=None):
    """
    Generate multiple-choice warm-up questions.
    topic_infos: list of {id, name, subtopics, course_name, course_id, confidence}
    past_paper_context: optional {ref, parts} to focus questions on a specific past paper question
    Returns list of {question, options: {A,B,C,D}, correct, explanation, topic}
    """
    if not topic_infos:
        return []

    weights = [max(0.05, 1.0 - t.get('confidence', 0.0)) for t in topic_infos]
    assigned = random.choices(topic_infos, weights=weights, k=count)

    assignments = '\n'.join(
        f'Q{i + 1}: {t["course_name"]} — {t["name"]}'
        + (f' (e.g. {", ".join(t["subtopics"][:3])})' if t.get('subtopics') else '')
        for i, t in enumerate(assigned)
    )

    notes_snippet = _notes_block_multi([t['id'] for t in assigned])

    if past_paper_context:
        parts_text = '\n'.join(
            f'  {p.get("text", "")}'
            for p in past_paper_context['parts']
        )
        pp_preamble = (
            f'A student is about to attempt a Cambridge Part IB past paper question on '
            f'{past_paper_context["ref"].split(" Q")[0]}. '
            f'The question covers these areas:\n\n'
            f'{parts_text}\n\n'
            f'Generate exactly {count} standalone multiple-choice warm-up questions that '
            f'build the background knowledge needed to approach the above topic area.\n'
        )
        focus_rule = (
            f'- Each MCQ must be fully self-contained — do NOT reference "the question", '
            f'part labels (a/b/c), question numbers, or paper numbers\n'
            f'- Test a specific concept, definition, or technique that is prerequisite knowledge '
            f'for the topic area described above\n'
        )
    else:
        pp_preamble = (
            f'Cambridge Part IB CS examiner. Generate exactly {count} multiple-choice warm-up questions.\n'
        )
        focus_rule = (
            f'- Test a fact, definition, concept, or short reasoning from the assigned topic\n'
        )

    prompt = (
        f'{pp_preamble}'
        f'Each question must cover the SPECIFIC topic assigned to it:\n\n'
        f'{assignments}'
        f'{notes_snippet}\n\n'
        f'Rules:\n'
        f'{focus_rule}'
        f'- Keep each question concise (1-2 sentences)\n'
        f'- 4 options A-D, exactly one correct; wrong options must be plausible distractors\n'
        f'- CRITICAL: all four options must be similar in length and grammatical structure — '
        f'the correct answer must NOT be longer, more detailed, or more specific than the wrong options. '
        f'A student should not be able to guess by looking at option length or complexity.\n'
        f'- One-sentence explanation of why the correct answer is right\n'
        f'- "topic" field: copy the topic name exactly from the assignment above\n'
        f'- Return questions in the same order as the assignments (Q1 first)\n'
        f'- Use LaTeX for any maths: inline $...$ and display $$...$$\n\n'
        f'Respond ONLY with a JSON array, no other text:\n'
        f'[{{"question":"...","options":{{"A":"...","B":"...","C":"...","D":"..."}},'
        f'"correct":"A","explanation":"...","topic":"..."}}]'
    )

    response = call_claude(prompt, model=QUESTION_MODEL, max_tokens=2048)
    result = extract_json_array_from_response(response)

    if result and isinstance(result, list):
        valid = []
        for idx, item in enumerate(result):
            if not isinstance(item, dict):
                continue
            if not all(k in item for k in ('question', 'options', 'correct', 'explanation')):
                continue
            opts = item.get('options', {})
            if not all(k in opts for k in ('A', 'B', 'C', 'D')):
                continue
            if item.get('correct') not in ('A', 'B', 'C', 'D'):
                continue
            t = assigned[idx] if idx < len(assigned) else {}
            mcq = {
                'question': str(item['question']),
                'options': {
                    'A': str(opts['A']),
                    'B': str(opts['B']),
                    'C': str(opts['C']),
                    'D': str(opts['D']),
                },
                'correct': item['correct'],
                'explanation': str(item.get('explanation', '')),
                'topic': str(item.get('topic', '')),
                'topic_id': t.get('id', ''),
                'course_id': t.get('course_id', ''),
            }
            valid.append(_shuffle_mcq_options(mcq))
        return valid[:count]

    return []



def generate_flashcards(question, model_solution, topic_name, course_name, topic_id=None):
    """
    Generate atomic Anki flashcards from a wrong answer using the minimum information principle.
    Returns list of {front, back} dicts.
    """
    notes_snippet = _notes_block(topic_id) if topic_id else ''
    prompt = (
        f'Cambridge CS tutor creating Anki flashcards.\n'
        f'Course: {course_name} | Topic: {topic_name}\n'
        f'Exam question: {question}\n'
        f'Model answer: {model_solution}\n'
        f'{notes_snippet}\n\n'
        f'Apply the minimum information principle: 2-4 atomic flashcards, each testing ONE fact.\n'
        f'Rules:\n'
        f'- Front: ≤12 words, one specific question\n'
        f'- Back: bullet points only, 2-4 bullets max, each ≤10 words. No prose.\n'
        f'- Focus only on what the student got wrong\n'
        f'- No card should require knowing another card\n\n'
        f'Respond ONLY with a JSON array:\n'
        f'[{{"front": "What is X?", "back": "• point 1\\n• point 2"}}]'
    )
    response = call_claude(prompt, model=EVAL_MODEL, max_tokens=1024)
    result = extract_json_array_from_response(response)
    if result and isinstance(result, list):
        return [
            {'front': str(c['front']), 'back': str(c['back'])}
            for c in result
            if isinstance(c, dict) and 'front' in c and 'back' in c
        ]
    return []


CHAT_MODEL = 'claude-sonnet-4-6'

CAMBRIDGE_COURSE_URLS = {
    'data-science':            'https://www.cl.cam.ac.uk/teaching/2425/DataSci/',
    'econ-law-ethics':         'https://www.cl.cam.ac.uk/teaching/2425/EconLawEth/',
    'further-graphics':        'https://www.cl.cam.ac.uk/teaching/2425/FGraphics/',
    'further-hci':             'https://www.cl.cam.ac.uk/teaching/2425/FHCI/',
    'concurrent-distributed':  'https://www.cl.cam.ac.uk/teaching/2425/ConcDisSys/',
    'compiler-construction':   'https://www.cl.cam.ac.uk/teaching/2425/CompConstr/',
    'computation-theory':      'https://www.cl.cam.ac.uk/teaching/2425/CompTheory/',
    'computer-networking':     'https://www.cl.cam.ac.uk/teaching/2425/CompNet/',
    'logic-proof':             'https://www.cl.cam.ac.uk/teaching/2425/LogicProof/',
    'prolog':                  'https://www.cl.cam.ac.uk/teaching/2425/Prolog/',
    'semantics':               'https://www.cl.cam.ac.uk/teaching/2425/Semantics/',
    'artificial-intelligence': 'https://www.cl.cam.ac.uk/teaching/2425/ArtInt/',
    'complexity-theory':       'https://www.cl.cam.ac.uk/teaching/2425/Complexity/',
    'cybersecurity':           'https://www.cl.cam.ac.uk/teaching/2425/CyberSec/',
    'formal-models-language':  'https://www.cl.cam.ac.uk/teaching/2425/FormModLang/',
    'intro-comp-arch':         'https://www.cl.cam.ac.uk/teaching/2425/CompArch/',
    'prog-c-cpp':              'https://www.cl.cam.ac.uk/teaching/2425/ProgC/',
}


def _build_course_summary(course_id: str = None) -> str:
    """Build a compact summary of course topics + key facts for chat context."""
    courses_path = os.path.join(os.path.dirname(__file__), 'data', 'courses.json')
    with open(courses_path) as f:
        courses = json.load(f)
    notes = _load_notes_index()

    lines = []
    for term_id, term in courses['terms'].items():
        for cid, course in term['courses'].items():
            if course_id and cid != course_id:
                continue
            url = CAMBRIDGE_COURSE_URLS.get(cid, '')
            url_str = f' <{url}>' if url else ''
            lines.append(f"\n## {course['name']} ({cid}){url_str}")
            for topic in course['topics']:
                tid = topic['id']
                lines.append(f"- {topic['name']} [{tid}]")
                subs = topic.get('subtopics', [])
                if subs:
                    lines.append(f"    · Subtopics: {', '.join(subs)}")
                entry = notes.get(tid)
                if entry:
                    facts = entry.get('key_facts', [])[:3]
                    terms_d = entry.get('terms', {})
                    for fact in facts:
                        lines.append(f"    • {fact}")
                    if terms_d:
                        names = ', '.join(list(terms_d.keys())[:8])
                        lines.append(f"    • Terms covered: {names}")
    return '\n'.join(lines)


def course_chat(message: str, course_id: 'str | None' = None, history: list = None) -> dict:
    """Answer a question about course content. Returns {'reply': str}."""
    history = history or []
    summary = _build_course_summary(course_id)

    scope_note = (
        f"The user is asking specifically about the '{course_id}' course."
        if course_id else
        "The user has not specified a course — search across all 17 Part IB courses."
    )

    system = (
        "You are a study assistant for a Cambridge Part IB Computer Science student. "
        "Your job is to answer whether topics, concepts, or techniques are part of the courses, "
        "and to give brief explanations of where they fit. " + scope_note + "\n\n"
        "Use the course catalogue below as the authoritative source. Each topic is listed with its "
        "course, a topic id in [brackets], its subtopics (after `· Subtopics:`), and — when notes exist — "
        "key facts from the student's lecture notes (after `•`). A concept counts as covered if it matches "
        "either the topic name OR any subtopic; do not say 'no notes on X' just because X only appears as a "
        "subtopic. If something is in the catalogue, say so confidently and cite the course + topic. "
        "If it is NOT in the catalogue, say it does not appear to be in the Part IB courses, but mention "
        "if it is a prerequisite (Part IA) or extension topic. Be honest about uncertainty.\n\n"
        "Keep answers concise (1-4 sentences usually). Use markdown sparingly. For maths, use $...$ for inline "
        "and $$...$$ for display. Inside table cells and inline contexts, keep math on a single line — never use "
        "\\\\ line breaks, \\begin{matrix}, \\begin{array}, or \\begin{cases} inside $...$ (they render as ugly "
        "stacked fragments mid-paragraph). If you need a stacked layout, use a separate $$...$$ block instead. "
        "The Cambridge course pages (URLs in <angle brackets>) are the canonical syllabi if the student wants to "
        "verify.\n\n"
        "Be precise: do not conflate similarly-named concepts (e.g. 'Hoare logic' vs 'Hoare-style monitor "
        "semantics'). When the catalogue mentions a name in passing, say so but don't claim full coverage. "
        "If the catalogue does not contain a topic, do not invent that another Part IB course covers it — "
        "say 'not in the IB catalogue I have' and suggest the student check the official page if unsure.\n\n"
        "=== COURSE CATALOGUE ===\n" + summary
    )

    messages = []
    for turn in history[-10:]:
        role = turn.get('role')
        content = turn.get('content', '').strip()
        if role in ('user', 'assistant') and content:
            messages.append({'role': role, 'content': content})
    messages.append({'role': 'user', 'content': message})

    try:
        client = _get_client()
        msg = client.messages.create(
            model=CHAT_MODEL,
            max_tokens=800,
            system=system,
            messages=messages,
        )
        return {'reply': msg.content[0].text}
    except Exception as e:
        print(f'[claude] course_chat error: {e}')
        return {'reply': None, 'error': str(e)}


def weekly_retrospective(history_entries: list, days: int = 7) -> dict:
    """Look across the last `days` of answered questions and write a one-paragraph
    diagnosis of the through-line in mistakes. Returns {'summary': str}."""
    if not history_entries:
        return {'summary': None, 'error': 'no recent history'}

    # Compact each entry: keep what matters for diagnosis, drop the verbose fields.
    lines = []
    for h in history_entries:
        ts = h.get('timestamp', '')[:10]
        score = h.get('score')
        score_str = f"{int(score * 100)}%" if isinstance(score, (int, float)) else 'n/a'
        course = h.get('course_id', '?')
        topic = h.get('topic_id', '?')
        q = (h.get('question') or '').replace('\n', ' ')[:280]
        ans = (h.get('answer') or '').replace('\n', ' ')[:240]
        fb = (h.get('feedback') or '').replace('\n', ' ')[:280]
        lines.append(
            f"[{ts}] {course}/{topic} score={score_str}\n  Q: {q}\n  A: {ans}\n  Feedback: {fb}"
        )
    body = '\n\n'.join(lines)

    system = (
        "You are a Cambridge Part IB CS exam tutor reviewing a student's last week of answer history. "
        "Look ACROSS the entries for the conceptual through-line — recurring misconceptions, "
        "weak technique patterns (rushed, vague, missing definitions), or specific topics that keep "
        "tripping them up. Avoid generic advice. Output:\n"
        "1. A 2-3 sentence diagnosis (what's the pattern?)\n"
        "2. 2-4 concrete bullet points of what to drill THIS week.\n"
        "Use markdown. Reference courses by name, not id. Keep it under 180 words. "
        "Do not list every entry — synthesise."
    )

    try:
        client = _get_client()
        msg = client.messages.create(
            model=CHAT_MODEL,
            max_tokens=600,
            system=system,
            messages=[{'role': 'user', 'content': f"Past {days} days of attempts:\n\n{body}"}],
        )
        return {'summary': msg.content[0].text}
    except Exception as e:
        print(f'[claude] weekly_retrospective error: {e}')
        return {'summary': None, 'error': str(e)}


# ---- Supervision work parsing & checking ----

def parse_supervision_questions(pdf_text, filename=None):
    """Given raw text from a supervision PDF, extract a structured list of
    questions. Returns {'title', 'course_hint', 'questions': [{label, text, parts: [...]}]}.
    `parts` is optional — only populated for multi-part questions."""
    prompt = (
        'You are parsing a Cambridge Computer Science supervision exercise sheet. '
        'Extract every numbered exercise question into structured JSON.\n\n'
        f'Filename: {filename or "unknown"}\n\n'
        'Sheet text:\n"""\n'
        f'{pdf_text[:60000]}\n'
        '"""\n\n'
        'Output ONLY JSON with this exact shape — use these EXACT keys:\n'
        '{\n'
        '  "title": "short sheet title (e.g. \'Artificial Intelligence — Exercises\')",\n'
        '  "course_hint": "course name if obvious, else empty",\n'
        '  "questions": [\n'
        '    {"label": "1", "text": "full question text", "parts": []},\n'
        '    {"label": "2", "text": "preamble before parts", "parts": [\n'
        '       {"label": "a", "text": "part (a) text"},\n'
        '       {"label": "b", "text": "part (b) text"}\n'
        '    ]}\n'
        '  ]\n'
        '}\n\n'
        'Critical rules:\n'
        '- Use the key name `label` (NOT `id`, NOT `number`, NOT `section`).\n'
        '- For sheets with numbered sections containing numbered questions, use a '
        '  composite label like "3.1", "3.2" (section.question) so they remain '
        '  uniquely identifiable.\n'
        '- Preserve LaTeX maths verbatim ($...$, $$...$$).\n'
        '- Include any setup context (definitions, given graphs, formulas) inside the '
        '  question text so it can be answered standalone.\n'
        '- If a question has no sub-parts, set "parts" to an empty array [].\n'
        '- Do NOT include section headings as standalone questions.\n'
        '- Do NOT include solutions/answers if the sheet contains them — only questions.\n'
        '- Output JSON ONLY, no preamble, no trailing commentary.'
    )
    response = call_claude(prompt, model=EVAL_MODEL, max_tokens=16000)
    result = extract_json_from_response(response)
    if not result or 'questions' not in result or not isinstance(result.get('questions'), list):
        return {
            'title': filename or 'Supervision',
            'course_hint': '',
            'questions': [],
            '_parse_error': 'Could not extract structured questions from this PDF.',
            '_raw_preview': (response or '')[:400],
        }

    def _label(item, fallback=''):
        # Accept label / id / number / num — first non-empty wins
        for k in ('label', 'id', 'number', 'num'):
            v = item.get(k)
            if v not in (None, ''):
                return str(v)
        return fallback

    questions = []
    for i, q in enumerate(result.get('questions', [])):
        if not isinstance(q, dict):
            continue
        parts_raw = q.get('parts') or q.get('subparts') or []
        if not isinstance(parts_raw, list):
            parts_raw = []
        parts = []
        for j, p in enumerate(parts_raw):
            if not isinstance(p, dict):
                continue
            parts.append({
                'label': _label(p, fallback=chr(ord('a') + j)),
                'text': p.get('text') or p.get('question') or '',
            })
        questions.append({
            'label': _label(q, fallback=str(i + 1)),
            'text': q.get('text') or q.get('question') or '',
            'parts': parts,
        })

    return {
        'title': result.get('title') or (filename or 'Supervision'),
        'course_hint': result.get('course_hint') or result.get('course') or '',
        'questions': questions,
    }


def evaluate_handwritten_script(question_parts, page_images, topic_name, course_name, source=None):
    """Grade an entire handwritten past-paper script in one Claude call.

    Args:
        question_parts: list of {'label', 'text', 'marks'} for every part of the question.
        page_images: list of {'media_type', 'data'} (base64 PNGs) — one per PDF page,
            in order. Claude reads them as image inputs and figures out which
            part each region of writing addresses.

    Returns:
        list of per-part dicts shaped like evaluate_answer's output:
        [{'label', 'marks_awarded', 'marks_available', 'score', 'feedback',
          'model_solution', 'key_gaps'}]
    """
    if not page_images:
        return []

    total_marks = sum(p.get('marks', 0) for p in question_parts)
    parts_block = '\n'.join(
        f'  ({p["label"]}) [{p.get("marks", 0)} marks] {p.get("text", "")}'
        for p in question_parts
    )

    prompt = (
        'Cambridge CS supervisor marking a handwritten exam script. The student '
        'has uploaded a PDF of their answers and we have rendered each page to '
        f'an image (attached, {len(page_images)} page(s), in order).\n\n'
        f'Course: {course_name} | Topic: {topic_name}'
        f'{" | " + source if source else ""}\n\n'
        f'QUESTION (total {total_marks} marks):\n{parts_block}\n\n'
        'Your job:\n'
        '1. Read the handwriting on every page. Treat diagrams, arrows, equations, '
        'and crossings-out as part of the answer.\n'
        '2. Identify which part of the question each region of writing addresses. '
        'Parts may appear out of order or span multiple pages; the student may '
        'have labelled them (a), (b) etc. — use those labels where present.\n'
        '3. For each part, award an integer mark out of that part\'s available '
        'marks, write specific feedback noting what was good and what was missing, '
        'and give a concise model solution.\n'
        '4. If a part has NO answer at all in the script, award 0 and say so '
        'in feedback.\n\n'
        'Be precise about what the handwriting actually says — do NOT charitably '
        'fill in what they "meant" if the writing or diagram is wrong or absent. '
        'For diagrams: describe what the student drew, then compare to what was '
        'expected.\n\n'
        'Use LaTeX for any maths in feedback/model_solution ($...$ inline, $$...$$ display).\n\n'
        'Respond ONLY with JSON of this shape (one object per part, same order as above):\n'
        '{\n'
        '  "parts": [\n'
        '    {"label": "a", "marks_awarded": 6, "marks_available": 8, '
        '"feedback": "...", "model_solution": "...", "key_gaps": ["concept"]},\n'
        '    ...\n'
        '  ]\n'
        '}'
    )

    response = _call_claude_with_images(prompt, page_images, model=EVAL_MODEL, max_tokens=6000)
    result = extract_json_from_response(response)
    if not result or 'parts' not in result or not isinstance(result['parts'], list):
        # Defensive fallback — return zero marks per part with a hint to retry.
        return [{
            'label': p.get('label', ''),
            'marks_awarded': 0,
            'marks_available': p.get('marks', 0),
            'score': 0.0,
            'feedback': 'Evaluation failed — could not parse the script. Try re-uploading.',
            'model_solution': '',
            'key_gaps': [],
        } for p in question_parts]

    # Normalise: align Claude's output with the requested parts by label.
    by_label = {str(p.get('label', '')).strip().lower(): p for p in result['parts'] if isinstance(p, dict)}
    out = []
    for req in question_parts:
        label = str(req.get('label', '')).strip()
        available = req.get('marks', 0)
        ev = by_label.get(label.lower()) or {}
        awarded = ev.get('marks_awarded', 0)
        try:
            awarded = max(0, min(available, int(round(float(awarded)))))
        except (TypeError, ValueError):
            awarded = 0
        score = (awarded / available) if available else 0.0
        out.append({
            'label': label,
            'marks_awarded': awarded,
            'marks_available': available,
            'score': score,
            'feedback': ev.get('feedback', '') or '',
            'model_solution': ev.get('model_solution', '') or '',
            'key_gaps': ev.get('key_gaps', []) or [],
        })
    return out


def provisional_check_answer(question_text, answer_text, course_name=None, sheet_title=None):
    """Lightweight sanity check on a supervision answer — flag glaring errors,
    missed parts, or off-topic answers, but DO NOT mark or give a full critique.
    Returns {'flags': [str], 'overall': str}."""
    if not (answer_text or '').strip():
        return {'flags': ['No answer written yet.'], 'overall': 'empty'}

    context = []
    if sheet_title:
        context.append(f'Supervision: {sheet_title}')
    if course_name:
        context.append(f'Course: {course_name}')
    context_block = ' | '.join(context) + '\n' if context else ''

    prompt = (
        'Cambridge CS supervisor doing a quick provisional sanity-check on a '
        'student\'s draft supervision answer before they submit it. You are NOT '
        'marking — you are flagging glaring problems only.\n\n'
        f'{context_block}'
        f'Question:\n"""\n{question_text}\n"""\n\n'
        f'Student draft answer:\n"""\n{answer_text}\n"""\n\n'
        'Flag ONLY things that are clearly wrong, clearly missing, or clearly '
        'off-topic. Things to look for:\n'
        '- factual / technical errors (e.g. wrong definition, wrong complexity class)\n'
        '- missed sub-parts of the question\n'
        '- the answer addresses a different question than asked\n'
        '- a critical step or justification is missing\n\n'
        'Do NOT comment on style, length, or polish. If the answer looks broadly '
        'sound, return an empty flags array.\n\n'
        'Respond ONLY with JSON:\n'
        '{"flags": ["short specific issue", "another issue"], "overall": "ok" | "minor" | "major"}'
    )
    response = call_claude(prompt, model=EVAL_MODEL, max_tokens=600)
    result = extract_json_from_response(response)
    if not result:
        return {'flags': [], 'overall': 'ok'}
    flags = result.get('flags') or []
    overall = result.get('overall') or 'ok'
    return {'flags': [str(f) for f in flags][:8], 'overall': overall}
