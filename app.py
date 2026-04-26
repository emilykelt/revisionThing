import json
import os
import re
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), '.env'))
from flask import Flask, render_template, jsonify, request
from data import (
    load_courses, load_knowledge, load_history,
    get_dashboard_data, record_answer, record_mcq_answer, select_session_topics,
    save_knowledge, save_json, KNOWLEDGE_FILE,
)
from ai import generate_question, evaluate_answer, generate_mcqs, generate_hint, generate_flashcards
from config import DEFAULT_CONFIDENCE, DATA_DIR

app = Flask(__name__)


@app.route('/')
def index():
    return render_template('index.html')


@app.route('/api/dashboard')
def api_dashboard():
    return jsonify(get_dashboard_data())


@app.route('/api/graph')
def api_graph():
    courses = load_courses()
    knowledge = load_knowledge()

    relations_path = os.path.join(DATA_DIR, 'topic_relations.json')
    try:
        with open(relations_path) as f:
            cross_links = json.load(f).get('links', [])
    except (FileNotFoundError, json.JSONDecodeError):
        cross_links = []

    nodes, links, courses_meta = [], [], {}
    color_idx = 0

    for term_id, term in courses['terms'].items():
        for course_id, course in term['courses'].items():
            courses_meta[course_id] = {
                'name': course['name'], 'term': term_id, 'color_index': color_idx
            }
            color_idx += 1
            topics = course['topics']
            for i, topic in enumerate(topics):
                k = knowledge.get(topic['id'], {})
                nodes.append({
                    'id': topic['id'], 'name': topic['name'],
                    'course_id': course_id, 'course_name': course['name'],
                    'term': term_id,
                    'confidence': k.get('confidence', DEFAULT_CONFIDENCE),
                    'times_tested': k.get('times_tested', 0),
                    'difficult': k.get('difficult', False),
                })
                if i < len(topics) - 1:
                    links.append({
                        'source': topic['id'], 'target': topics[i + 1]['id'],
                        'type': 'intra', 'strength': 0.5, 'label': ''
                    })

    node_ids = {n['id'] for n in nodes}
    for rel in cross_links:
        if rel['source'] in node_ids and rel['target'] in node_ids:
            links.append({
                'source': rel['source'], 'target': rel['target'],
                'type': 'cross', 'strength': rel.get('strength', 0.5),
                'label': rel.get('label', '')
            })

    return jsonify({'nodes': nodes, 'links': links, 'courses': courses_meta})


@app.route('/api/course/<course_id>')
def api_course(course_id):
    dashboard = get_dashboard_data()
    for term_id, term in dashboard['terms'].items():
        if course_id in term['courses']:
            return jsonify({
                'term': term['label'],
                'course_id': course_id,
                **term['courses'][course_id],
            })
    return jsonify({'error': 'Course not found'}), 404


@app.route('/api/history')
def api_history():
    history = load_history()
    limit = request.args.get('limit', 50, type=int)
    offset = request.args.get('offset', 0, type=int)
    # Return most recent first
    reversed_history = list(reversed(history))
    return jsonify({
        'items': reversed_history[offset:offset + limit],
        'total': len(history),
    })


@app.route('/api/question/hint', methods=['POST'])
def api_question_hint():
    data = request.get_json()
    question = data.get('question', '')
    topic_name = data.get('topic_name', '')
    course_name = data.get('course_name', '')
    topic_id = data.get('topic_id', '')
    if not question:
        return jsonify({'error': 'question required'}), 400
    hint = generate_hint(question, topic_name, course_name, topic_id)
    return jsonify({'hint': hint})


@app.route('/api/question/generate', methods=['POST'])
def api_generate_question():
    data = request.get_json()
    topic_id = data.get('topic_id')
    if not topic_id:
        return jsonify({'error': 'topic_id required'}), 400

    courses = load_courses()
    knowledge = load_knowledge()

    # Find the topic and course
    topic_info = None
    course_id = None
    course_name = None
    for term_id, term in courses['terms'].items():
        for cid, course in term['courses'].items():
            for topic in course['topics']:
                if topic['id'] == topic_id:
                    topic_info = topic
                    course_id = cid
                    course_name = course['name']
                    break

    if not topic_info:
        return jsonify({'error': 'Topic not found'}), 404

    k = knowledge.get(topic_id, {})
    confidence = k.get('confidence', DEFAULT_CONFIDENCE)
    attempt = data.get('attempt', 0)
    ai_only = data.get('ai_only', False)

    result = generate_question(
        topic_info['name'],
        topic_info['subtopics'],
        course_name,
        confidence,
        course_id=course_id,
        topic_id=topic_id,
        attempt=attempt,
        ai_only=ai_only,
    )
    result['topic_id'] = topic_id
    result['course_id'] = course_id
    result['topic_name'] = topic_info['name']
    result['course_name'] = course_name
    result['difficult'] = knowledge.get(topic_id, {}).get('difficult', False)

    return jsonify(result)


@app.route('/api/topic/flag-difficult', methods=['POST'])
def api_flag_difficult():
    data = request.get_json()
    topic_id = data.get('topic_id')
    if not topic_id:
        return jsonify({'error': 'topic_id required'}), 400
    knowledge = load_knowledge()
    if topic_id not in knowledge:
        return jsonify({'error': 'Topic not found'}), 404
    new_state = not knowledge[topic_id].get('difficult', False)
    knowledge[topic_id]['difficult'] = new_state
    save_knowledge(knowledge)
    return jsonify({'topic_id': topic_id, 'difficult': new_state})


@app.route('/api/topic/difficult')
def api_difficult_topics():
    """Return all topics flagged as difficult, with course info."""
    courses = load_courses()
    knowledge = load_knowledge()
    results = []
    for term_id, term in courses['terms'].items():
        for course_id, course in term['courses'].items():
            for topic in course['topics']:
                k = knowledge.get(topic['id'], {})
                if k.get('difficult', False):
                    results.append({
                        'topic_id': topic['id'],
                        'topic_name': topic['name'],
                        'course_id': course_id,
                        'course_name': course['name'],
                        'confidence': k.get('confidence', DEFAULT_CONFIDENCE),
                        'times_tested': k.get('times_tested', 0),
                    })
    return jsonify({'topics': results})


@app.route('/api/question/submit', methods=['POST'])
def api_submit_answer():
    data = request.get_json()
    topic_id = data.get('topic_id')
    course_id = data.get('course_id')
    all_topic_ids = data.get('all_topic_ids') or []
    extra_topic_ids = [t for t in all_topic_ids if t != topic_id]

    if not topic_id:
        return jsonify({'error': 'topic_id required'}), 400

    # Find topic and course names
    courses = load_courses()
    topic_name = ''
    course_name = ''
    for term_id, term in courses['terms'].items():
        for cid, course in term['courses'].items():
            if cid == course_id:
                course_name = course['name']
                for topic in course['topics']:
                    if topic['id'] == topic_id:
                        topic_name = topic['name']

    parts = data.get('parts')
    if parts:
        # Multi-part evaluation: evaluate each part separately
        part_results = []
        total_weighted_score = 0
        total_marks = 0
        all_gaps = []
        combined_q = []
        combined_a = []

        # Build full context so each part evaluation has the whole question
        has_diagram = data.get('has_diagram', False)
        source = data.get('source', '')
        context_lines = []
        if source:
            context_lines.append(f'Source: {source}')
        if has_diagram:
            context_lines.append('Note: this question includes a diagram that cannot be shown here. '
                                  'Award marks based on the text parts only; do not penalise for anything '
                                  'that would only be answerable with the diagram unless the student\'s '
                                  'answer makes clear they missed it.')
        for p in parts:
            context_lines.append(f'({p.get("label","")}) {p.get("question","")} [{p.get("marks",0)} marks]')
        full_context = '\n'.join(context_lines) if context_lines else None

        for part in parts:
            label = part.get('label', '')
            q_text = part.get('question', '')
            a_text = part.get('answer', '')
            marks = part.get('marks', 8)

            ev = evaluate_answer(q_text, a_text, topic_name, course_name,
                                 part_label=label, marks_available=marks,
                                 full_context=full_context)
            part_results.append({
                'label': label,
                'question_text': q_text,
                'score': ev['score'],
                'marks_awarded': ev.get('marks_awarded'),
                'marks_available': marks,
                'feedback': ev.get('feedback', ''),
                'model_solution': ev.get('model_solution', ''),
                'key_gaps': ev.get('key_gaps', []),
            })
            total_weighted_score += ev['score'] * marks
            total_marks += marks
            all_gaps.extend(ev.get('key_gaps', []))
            combined_q.append(f'({label}) {q_text}')
            combined_a.append(f'({label}) {a_text}')

        overall_score = total_weighted_score / total_marks if total_marks > 0 else 0

        new_confidence = record_answer(
            topic_id, course_id,
            ' | '.join(combined_q),
            ' | '.join(combined_a),
            overall_score,
            ' | '.join(f"({r['label']}) {r['feedback']}" for r in part_results),
            ' | '.join(f"({r['label']}) {r['model_solution']}" for r in part_results),
            extra_topic_ids=extra_topic_ids,
        )

        total_marks_awarded = sum(r['marks_awarded'] for r in part_results if r.get('marks_awarded') is not None)
        # Generate flashcards for parts answered poorly
        for pr in part_results:
            if pr['score'] < 0.5 and pr.get('model_solution'):
                _queue_flashcards(pr.get('question_text', ''),
                                  pr.get('model_solution', ''),
                                  topic_name, course_name, topic_id, course_id)

        return jsonify({
            'part_results': part_results,
            'overall_score': overall_score,
            'total_marks_awarded': total_marks_awarded,
            'total_marks': total_marks,
            'new_confidence': new_confidence,
            'key_gaps': list(dict.fromkeys(all_gaps)),  # deduplicated, order preserved
        })

    else:
        # Single question
        question = data.get('question', '')
        answer = data.get('answer', '')
        evaluation = evaluate_answer(question, answer, topic_name, course_name)

        new_confidence = record_answer(
            topic_id, course_id, question, answer,
            evaluation['score'],
            evaluation.get('feedback', ''),
            evaluation.get('model_solution', ''),
            extra_topic_ids=extra_topic_ids,
        )

        if evaluation['score'] < 0.5 and evaluation.get('model_solution'):
            _queue_flashcards(question, evaluation['model_solution'],
                              topic_name, course_name, topic_id, course_id)

        return jsonify({
            'score': evaluation['score'],
            'marks_awarded': evaluation.get('marks_awarded'),
            'marks_available': evaluation.get('marks_available'),
            'feedback': evaluation.get('feedback', ''),
            'model_solution': evaluation.get('model_solution', ''),
            'key_gaps': evaluation.get('key_gaps', []),
            'new_confidence': new_confidence,
        })


@app.route('/api/mcq/submit', methods=['POST'])
def api_mcq_submit():
    """Record MCQ results — updates confidence with reduced weight."""
    data = request.get_json() or {}
    results = data.get('results', [])  # [{topic_id, course_id, is_correct}]
    updated = {}
    for r in results:
        tid = r.get('topic_id')
        cid = r.get('course_id', '')
        is_correct = bool(r.get('is_correct'))
        if tid:
            new_conf = record_mcq_answer(tid, cid, is_correct)
            if new_conf is not None:
                updated[tid] = new_conf
    return jsonify({'updated': updated})


@app.route('/api/mcq/generate', methods=['POST'])
def api_generate_mcqs():
    data = request.get_json() or {}
    course_id = data.get('course_id')
    topic_ids = data.get('topic_ids')  # explicit list overrides course_id
    count = min(max(int(data.get('count', 8)), 3), 15)
    past_paper = data.get('past_paper')  # {course_id, year, paper, question_num}

    courses = load_courses()
    knowledge = load_knowledge()

    past_paper_context = None

    if past_paper:
        pp_course_id = past_paper.get('course_id')
        pp_year = past_paper.get('year')
        pp_paper = past_paper.get('paper')
        pp_qnum = past_paper.get('question_num')

        # Load the specific past paper question
        pp_file = os.path.join(DATA_DIR, 'pastpapers.json')
        try:
            with open(pp_file) as f:
                pp_data = json.load(f)
        except Exception:
            pp_data = {}

        pp_course_data = pp_data.get(pp_course_id, {})
        pp_question = None
        for q in pp_course_data.get('tagged_questions', []):
            if q['year'] == pp_year and q['paper'] == pp_paper and q['question'] == pp_qnum:
                pp_question = q
                break

        if pp_question:
            topic_ids = set(pp_question.get('topics', []))
            past_paper_context = {
                'ref': f"{pp_year} Paper {pp_paper} Q{pp_qnum}",
                'parts': pp_question.get('parts', []),
            }
            # Filter topic_infos to only the topics tagged in this question
            course_id = pp_course_id  # restrict to this course
            topic_infos = []
            for term_id, term in courses['terms'].items():
                for cid, course in term['courses'].items():
                    if cid != pp_course_id:
                        continue
                    for topic in course['topics']:
                        if topic['id'] not in topic_ids:
                            continue
                        k = knowledge.get(topic['id'], {})
                        conf = k.get('confidence', DEFAULT_CONFIDENCE)
                        topic_infos.append({
                            'id': topic['id'],
                            'name': topic['name'],
                            'subtopics': topic.get('subtopics', []),
                            'course_name': course['name'],
                            'course_id': cid,
                            'confidence': conf,
                        })
        else:
            # Past paper question not found — fall back to topic_ids sent in body
            topic_ids_set = set(topic_ids) if topic_ids else None
            topic_infos = []
            for term_id, term in courses['terms'].items():
                for cid, course in term['courses'].items():
                    if pp_course_id and cid != pp_course_id:
                        continue
                    for topic in course['topics']:
                        if topic_ids_set and topic['id'] not in topic_ids_set:
                            continue
                        k = knowledge.get(topic['id'], {})
                        conf = k.get('confidence', DEFAULT_CONFIDENCE)
                        topic_infos.append({
                            'id': topic['id'],
                            'name': topic['name'],
                            'subtopics': topic.get('subtopics', []),
                            'course_name': course['name'],
                            'course_id': cid,
                            'confidence': conf,
                        })
    else:
        topic_ids_set = set(topic_ids) if topic_ids else None
        topic_infos = []
        for term_id, term in courses['terms'].items():
            for cid, course in term['courses'].items():
                if course_id and cid != course_id:
                    continue
                for topic in course['topics']:
                    if topic_ids_set and topic['id'] not in topic_ids_set:
                        continue
                    k = knowledge.get(topic['id'], {})
                    conf = k.get('confidence', DEFAULT_CONFIDENCE)
                    topic_infos.append({
                        'id': topic['id'],
                        'name': topic['name'],
                        'subtopics': topic.get('subtopics', []),
                        'course_name': course['name'],
                        'course_id': cid,
                        'confidence': conf,
                    })

    if not topic_infos:
        return jsonify({'mcqs': [], 'total': 0})

    topic_infos.sort(key=lambda t: t['confidence'])

    mcqs = generate_mcqs(topic_infos, count, past_paper_context=past_paper_context)
    return jsonify({'mcqs': mcqs, 'total': len(mcqs)})


@app.route('/api/session/start', methods=['POST'])
def api_start_session():
    data = request.get_json() or {}
    mode = data.get('mode', 'weak')
    course_id = data.get('course_id')
    count = data.get('count', 8)
    topics = select_session_topics(mode, course_id, count)
    return jsonify({'topics': topics})


@app.route('/api/pastpapers/all')
def api_pastpapers_all():
    pp_file = os.path.join(DATA_DIR, 'pastpapers.json')
    courses_data = load_courses()

    # Build course_id → name lookup
    course_names = {}
    for term_id, term in courses_data['terms'].items():
        for course_id, course in term['courses'].items():
            course_names[course_id] = course['name']

    try:
        with open(pp_file) as f:
            pp = json.load(f)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

    progress = _load_pp_progress()
    _diagram_keywords = re.compile(
        r'\b(figure|diagram|below|shown|table|network shown|topology|illustration|graph|circuit)\b',
        re.IGNORECASE
    )

    result = []
    for course_id, data in pp.items():
        qs = data.get('tagged_questions', [])
        if not qs:
            continue
        questions = []
        for q in qs:
            parts = q.get('parts', [])
            total_marks = sum(p.get('marks', 0) for p in parts)
            ref = f"{q['year']} Paper {q['paper']} Q{q['question']}"
            prog = progress.get(ref, {})
            # Detect diagram references in any part text
            all_text = ' '.join(p.get('text', '') for p in parts)
            has_diagram = bool(_diagram_keywords.search(all_text))
            questions.append({
                'year': q['year'],
                'paper': q['paper'],
                'question': q['question'],
                'ref': ref,
                'total_marks': total_marks,
                'topic_ids': q.get('topics', []),
                'parts': parts,
                'pdf_url': q.get('pdf_url'),
                'has_diagram': has_diagram,
                'attempts': prog.get('attempts', 0),
                'best_score': prog.get('best_score', 0),
                'best_marks': prog.get('best_marks'),
                'difficult_parts': prog.get('difficult_parts', []),
            })
        questions.sort(key=lambda q: (-q['year'], q['paper'], q['question']))
        result.append({
            'course_id': course_id,
            'course_name': course_names.get(course_id, course_id),
            'questions': questions,
        })
    result.sort(key=lambda c: c['course_name'])
    return jsonify({'courses': result})


@app.route('/api/pastpapers/<course_id>')
def api_pastpapers(course_id):
    pp_file = os.path.join(DATA_DIR, 'pastpapers.json')
    try:
        with open(pp_file) as f:
            pp = json.load(f)
        data = pp.get(course_id, {})
        return jsonify({
            'total_questions': data.get('total_questions', 0),
            'past_paper_url': data.get('past_paper_url'),
            'topic_frequencies': data.get('topic_frequencies', {}),
            'tagged_questions': data.get('tagged_questions', []),
            'note': data.get('_note'),
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


PP_PROGRESS_FILE = os.path.join(DATA_DIR, 'pp_progress.json')


def _load_pp_progress():
    try:
        with open(PP_PROGRESS_FILE) as f:
            return json.load(f)
    except Exception:
        return {}


def _save_pp_progress(data):
    with open(PP_PROGRESS_FILE, 'w') as f:
        json.dump(data, f, indent=2)


@app.route('/api/pp/progress', methods=['GET'])
def api_pp_progress_get():
    return jsonify(_load_pp_progress())


@app.route('/api/pp/progress', methods=['POST'])
def api_pp_progress_update():
    """Record a past paper attempt or toggle a part's difficult flag."""
    data = request.get_json() or {}
    ref = data.get('ref')  # e.g. "2019 Paper 5 Q6"
    if not ref:
        return jsonify({'error': 'ref required'}), 400

    progress = _load_pp_progress()
    entry = progress.get(ref, {'attempts': 0, 'best_score': 0, 'difficult_parts': []})

    if 'score' in data:
        score = float(data['score'])
        total_marks = int(data.get('total_marks', 0))
        entry['attempts'] = entry.get('attempts', 0) + 1
        entry['best_score'] = max(entry.get('best_score', 0), score)
        if total_marks > 0:
            earned = int(data['marks_awarded']) if 'marks_awarded' in data else round(score * total_marks)
            entry['total_marks'] = total_marks
            entry['best_marks'] = max(entry.get('best_marks', 0), earned)

    if 'toggle_difficult_part' in data:
        part_label = data['toggle_difficult_part']
        parts = entry.get('difficult_parts', [])
        if part_label in parts:
            parts.remove(part_label)
        else:
            parts.append(part_label)
        entry['difficult_parts'] = parts

    progress[ref] = entry
    _save_pp_progress(progress)
    return jsonify({'ref': ref, **entry})


# ---- Anki bank ----

ANKI_BANK_FILE = os.path.join(DATA_DIR, 'anki_bank.json')


def _load_anki_bank():
    try:
        with open(ANKI_BANK_FILE) as f:
            return json.load(f)
    except Exception:
        return []


def _save_anki_bank(cards):
    with open(ANKI_BANK_FILE, 'w') as f:
        json.dump(cards, f, indent=2)


def _queue_flashcards(question, model_solution, topic_name, course_name, topic_id, course_id):
    """Generate flashcards in a background thread so the submit response isn't delayed."""
    import threading
    def _run():
        try:
            cards = generate_flashcards(question, model_solution, topic_name, course_name, topic_id)
            if not cards:
                return
            from datetime import datetime
            bank = _load_anki_bank()
            for c in cards:
                bank.append({
                    'id': f'{topic_id}-{len(bank)}',
                    'front': c['front'],
                    'back': c['back'],
                    'topic_id': topic_id,
                    'topic_name': topic_name,
                    'course_id': course_id,
                    'course_name': course_name,
                    'created_at': datetime.now().isoformat(),
                })
            _save_anki_bank(bank)
        except Exception as e:
            print(f'[anki] flashcard generation failed: {e}')
    threading.Thread(target=_run, daemon=True).start()



@app.route('/api/anki/bank')
def api_anki_bank():
    bank = _load_anki_bank()
    # Group by topic
    topics = {}
    for card in bank:
        tid = card['topic_id']
        if tid not in topics:
            topics[tid] = {
                'topic_id': tid,
                'topic_name': card['topic_name'],
                'course_name': card['course_name'],
                'cards': [],
            }
        topics[tid]['cards'].append(card)
    return jsonify({
        'total': len(bank),
        'topics': list(topics.values()),
    })


@app.route('/api/anki/export', methods=['POST'])
def api_anki_export():
    """Export pending flashcards as an .apkg file and remove them from the bank."""
    import genanki, tempfile, time
    from flask import send_file

    data = request.get_json() or {}
    topic_filter = data.get('topic_id')  # None = export all

    bank = _load_anki_bank()
    to_export = [c for c in bank if not topic_filter or c['topic_id'] == topic_filter]
    remaining = [c for c in bank if topic_filter and c['topic_id'] != topic_filter]

    if not to_export:
        return jsonify({'error': 'No cards to export'}), 400

    # Deterministic model/deck IDs based on a fixed seed so re-exports merge cleanly
    model = genanki.Model(
        1_607_392_319,
        'Cambridge IB Revision',
        fields=[{'name': 'Front'}, {'name': 'Back'}],
        templates=[{
            'name': 'Card',
            'qfmt': '<div style="font-family:Arial;font-size:16px">{{Front}}</div>',
            'afmt': '<div style="font-family:Arial;font-size:16px">{{FrontSide}}<hr>{{Back}}</div>',
        }],
    )

    deck_id = 1_607_392_320
    deck = genanki.Deck(deck_id, 'Cambridge IB::Revision')

    for card in to_export:
        tags = ['ai-generated', card['topic_id'].replace('-', '_'),
                card['course_id'].replace('-', '_')]
        note = genanki.Note(
            model=model,
            fields=[card['front'], card['back']],
            tags=tags,
        )
        deck.add_note(note)

    tmp = tempfile.NamedTemporaryFile(suffix='.apkg', delete=False)
    genanki.Package(deck).write_to_file(tmp.name)
    tmp.close()

    # Clear exported cards
    _save_anki_bank(remaining)

    label = topic_filter or 'all'
    return send_file(
        tmp.name,
        as_attachment=True,
        download_name=f'cambridge-ib-{label}-{int(time.time())}.apkg',
        mimetype='application/octet-stream',
    )


@app.route('/api/anki/delete', methods=['POST'])
def api_anki_delete():
    """Delete specific cards from the bank without exporting."""
    data = request.get_json() or {}
    ids_to_delete = set(data.get('ids', []))
    bank = _load_anki_bank()
    bank = [c for c in bank if c['id'] not in ids_to_delete]
    _save_anki_bank(bank)
    return jsonify({'remaining': len(bank)})


@app.route('/api/reset', methods=['POST'])
def api_reset():
    data = request.get_json() or {}
    scope = data.get('scope', 'all')  # 'all', 'course', 'topic'
    target = data.get('target', '')  # course_id or topic_id

    knowledge = load_knowledge()

    if scope == 'topic' and target:
        if target in knowledge:
            knowledge[target] = {
                'confidence': DEFAULT_CONFIDENCE,
                'last_tested': None,
                'times_tested': 0,
                'streak': 0,
                'history': [DEFAULT_CONFIDENCE],
            }
    elif scope == 'course' and target:
        courses = load_courses()
        for term_id, term in courses['terms'].items():
            if target in term['courses']:
                for topic in term['courses'][target]['topics']:
                    if topic['id'] in knowledge:
                        knowledge[topic['id']] = {
                            'confidence': DEFAULT_CONFIDENCE,
                            'last_tested': None,
                            'times_tested': 0,
                            'streak': 0,
                            'history': [DEFAULT_CONFIDENCE],
                        }
    elif scope == 'all':
        for tid in knowledge:
            knowledge[tid] = {
                'confidence': DEFAULT_CONFIDENCE,
                'last_tested': None,
                'times_tested': 0,
                'streak': 0,
                'history': [DEFAULT_CONFIDENCE],
            }

    save_knowledge(knowledge)
    return jsonify({'ok': True})


if __name__ == '__main__':
    import os
    # Default to 5001: macOS reserves port 5000 for AirPlay Receiver.
    port = int(os.environ.get('PORT', 5001))
    host = os.environ.get('HOST', '127.0.0.1')
    debug = os.environ.get('FLASK_DEBUG', '1') != '0'
    app.run(debug=debug, port=port, host=host)
