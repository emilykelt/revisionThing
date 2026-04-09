import json
import re
import os
import random

import anthropic

QUESTION_MODEL = 'claude-haiku-4-5-20251001'
EVAL_MODEL = 'claude-sonnet-4-6'

_client = None
_PASTPAPERS = None


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
    except Exception:
        return None


def extract_json_array_from_response(text):
    if text is None:
        return None
    # Try JSON code fence
    match = re.search(r'```(?:json)?\s*\n?(\[.*?\])\s*\n?```', text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            pass
    # Find first [ and last ]
    start = text.find('[')
    end = text.rfind(']')
    if start != -1 and end != -1 and end > start:
        try:
            return json.loads(text[start:end + 1])
        except json.JSONDecodeError:
            pass
    return None


def extract_json_from_response(text):
    if text is None:
        return None
    # Try JSON code fence
    match = re.search(r'```(?:json)?\s*\n?(.*?)\n?```', text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            pass
    # Find first { and last } and parse everything between
    start = text.find('{')
    end = text.rfind('}')
    if start != -1 and end != -1:
        try:
            return json.loads(text[start:end + 1])
        except json.JSONDecodeError:
            pass
    return None


def generate_question(topic_name, subtopics, course_name, confidence,
                      course_id=None, topic_id=None, attempt=0):

    # --- Try to serve a real past paper question first ---
    pp_questions = _get_past_paper_questions(course_id, topic_id) if (course_id and topic_id) else []
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
    example_block = ''

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

    if confidence < 0.3:
        difficulty = 'low'
        prompt = (
            f'Cambridge Part IB CS examiner. Generate ONE short {style} question.\n'
            f'Course: {course_name} | Topic: {topic_name}\n'
            f'Focus subtopics: {", ".join(rotated[:4])}'
            f'{example_block}\n\n'
            f'Marks must be 1–10 (simple recall = 2–3, explanation = 4–6, analysis = 7–10).\n'
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
            f'{example_block}\n\n'
            f'Each part must be answerable independently. Marks per part: 1–10. Total marks up to 20.\n'
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
        return result

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


def evaluate_answer(question, answer, topic_name, course_name, part_label=None):
    if not answer or not answer.strip():
        return {
            'score': 0.0,
            'feedback': 'No answer provided.',
            'model_solution': '',
            'key_gaps': [topic_name],
        }

    part_context = f' (part {part_label})' if part_label else ''
    prompt = (
        f'Cambridge CS supervisor marking a tripos answer.\n'
        f'Course: {course_name} | Topic: {topic_name}{part_context}\n'
        f'Question: {question}\n'
        f'Student answer: {answer}\n\n'
        f'Respond ONLY with this JSON:\n'
        '{{"score": 0.7, "feedback": "...", "model_solution": "...", "key_gaps": ["concept1"]}}\n'
        f'Score 0.0–1.0. Be specific about what was good and what was missing.'
    )

    response = call_claude(prompt, model=EVAL_MODEL)
    result = extract_json_from_response(response)

    if result and 'score' in result:
        result['score'] = max(0.0, min(1.0, float(result['score'])))
        return result

    return {
        'score': 0.5,
        'feedback': response or 'Unable to evaluate.',
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


def generate_mcqs(topic_infos, count=8):
    """
    Generate multiple-choice warm-up questions.
    topic_infos: list of {id, name, subtopics, course_name, course_id, confidence}
    Returns list of {question, options: {A,B,C,D}, correct, explanation, topic}
    """
    if not topic_infos:
        return []

    # --- Weighted random topic assignment ---
    # Each question slot gets a topic sampled independently with probability
    # proportional to (1 - confidence), so weaker topics come up more often
    # but ANY topic can appear. Using random.choices (with replacement) means
    # each warm-up draw is different even when the topic pool is small.
    weights = [max(0.05, 1.0 - t.get('confidence', 0.0)) for t in topic_infos]
    assigned = random.choices(topic_infos, weights=weights, k=count)

    # Build explicit per-question assignments for the prompt
    assignments = '\n'.join(
        f'Q{i + 1}: {t["course_name"]} — {t["name"]}'
        + (f' (e.g. {", ".join(t["subtopics"][:3])})' if t.get('subtopics') else '')
        for i, t in enumerate(assigned)
    )

    prompt = (
        f'Cambridge Part IB CS examiner. Generate exactly {count} multiple-choice warm-up questions.\n'
        f'Each question must cover the SPECIFIC topic assigned to it:\n\n'
        f'{assignments}\n\n'
        f'Rules:\n'
        f'- Test a fact, definition, concept, or short reasoning from the assigned topic\n'
        f'- Keep each question concise (1-2 sentences)\n'
        f'- 4 options A-D, exactly one correct; wrong options must be plausible distractors\n'
        f'- One-sentence explanation of why the correct answer is right\n'
        f'- "topic" field: copy the topic name exactly from the assignment above\n'
        f'- Return questions in the same order as the assignments (Q1 first)\n\n'
        f'Respond ONLY with a JSON array, no other text:\n'
        f'[{{"question":"...","options":{{"A":"...","B":"...","C":"...","D":"..."}},'
        f'"correct":"A","explanation":"...","topic":"..."}}]'
    )

    response = call_claude(prompt, model=QUESTION_MODEL, max_tokens=2048)
    result = extract_json_array_from_response(response)

    if result and isinstance(result, list):
        valid = []
        for item in result:
            if not isinstance(item, dict):
                continue
            if not all(k in item for k in ('question', 'options', 'correct', 'explanation')):
                continue
            opts = item.get('options', {})
            if not all(k in opts for k in ('A', 'B', 'C', 'D')):
                continue
            if item.get('correct') not in ('A', 'B', 'C', 'D'):
                continue
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
            }
            valid.append(_shuffle_mcq_options(mcq))
        return valid[:count]

    return []
