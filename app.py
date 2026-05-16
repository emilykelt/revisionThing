import json
import os
import re
from datetime import datetime, timedelta
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), '.env'))
from flask import Flask, render_template, jsonify, request
from data import (
    load_courses, load_knowledge, load_history,
    get_dashboard_data, record_answer, record_mcq_answer, select_session_topics,
    save_knowledge, save_json, KNOWLEDGE_FILE,
)
from ai import (
    generate_question, evaluate_answer, generate_mcqs, generate_hint,
    generate_flashcards, course_chat, weekly_retrospective,
    parse_supervision_questions, provisional_check_answer,
    evaluate_handwritten_script,
    generate_gap_drill, evaluate_drill_answer,
)
from config import (
    DEFAULT_CONFIDENCE, DATA_DIR,
    OBSIDIAN_API_BASE_URL, OBSIDIAN_API_TOKEN, OBSIDIAN_TRACKER_VAULT_PATH,
)

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
            part_images = part.get('images') or []

            ev = evaluate_answer(q_text, a_text, topic_name, course_name,
                                 part_label=label, marks_available=marks,
                                 full_context=full_context, images=part_images)
            part_results.append({
                'label': label,
                'question_text': q_text,
                'score': ev['score'],
                'marks_awarded': ev.get('marks_awarded'),
                'marks_available': marks,
                'feedback': ev.get('feedback', ''),
                'model_solution': ev.get('model_solution', ''),
                'key_gaps': ev.get('key_gaps', []),
                'needs_drill': ev.get('needs_drill', False),
                'drill_reason': ev.get('drill_reason', ''),
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
            source=data.get('source') or None,
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
        images = data.get('images') or []
        evaluation = evaluate_answer(question, answer, topic_name, course_name, images=images)

        new_confidence = record_answer(
            topic_id, course_id, question, answer,
            evaluation['score'],
            evaluation.get('feedback', ''),
            evaluation.get('model_solution', ''),
            extra_topic_ids=extra_topic_ids,
            source=data.get('source') or None,
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
            'needs_drill': evaluation.get('needs_drill', False),
            'drill_reason': evaluation.get('drill_reason', ''),
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

    # Build course_id → name lookup, plus topic_id → name within each course
    course_names = {}
    course_topic_names = {}
    for term_id, term in courses_data['terms'].items():
        for course_id, course in term['courses'].items():
            course_names[course_id] = course['name']
            course_topic_names[course_id] = {
                t['id']: t['name'] for t in course.get('topics', [])
            }

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
                'completed_elsewhere': prog.get('completed_elsewhere', False),
                'completed_elsewhere_date': prog.get('completed_elsewhere_date'),
                'completed_confidence': prog.get('completed_confidence'),
            })
        questions.sort(key=lambda q: (-q['year'], q['paper'], q['question']))
        result.append({
            'course_id': course_id,
            'course_name': course_names.get(course_id, course_id),
            'topic_frequencies': data.get('topic_frequencies', {}),
            'topic_names': course_topic_names.get(course_id, {}),
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

    if 'toggle_completed_elsewhere' in data:
        # Mark a question as done outside the app (e.g. on paper). Stored with
        # an ISO date so the dashboard heatmap can place it on a calendar cell.
        if entry.get('completed_elsewhere'):
            entry.pop('completed_elsewhere', None)
            entry.pop('completed_elsewhere_date', None)
        else:
            entry['completed_elsewhere'] = True
            entry['completed_elsewhere_date'] = datetime.now().date().isoformat()
            # Optional confidence (0-1) for the heatmap colour.
            conf = data.get('completed_confidence')
            if conf is not None:
                try:
                    entry['completed_confidence'] = max(0.0, min(1.0, float(conf)))
                except (TypeError, ValueError):
                    pass

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


@app.route('/api/chat', methods=['POST'])
def api_chat():
    data = request.get_json() or {}
    message = (data.get('message') or '').strip()
    course_id = data.get('course_id') or None
    history = data.get('history') or []
    if not message:
        return jsonify({'error': 'Empty message'}), 400
    result = course_chat(message, course_id=course_id, history=history)
    if result.get('reply') is None:
        return jsonify({'error': result.get('error', 'Chat failed')}), 500
    return jsonify(result)


RETRO_FILE = os.path.join(DATA_DIR, 'retrospective.json')


def _load_retro_cache():
    try:
        with open(RETRO_FILE) as f:
            return json.load(f)
    except Exception:
        return {}


def _save_retro_cache(data):
    with open(RETRO_FILE, 'w') as f:
        json.dump(data, f, indent=2)


OBSIDIAN_STATUS_FILE = os.path.join(DATA_DIR, 'obsidian_status.json')


def _write_obsidian_status(payload):
    """Persist the latest Obsidian-push outcome so the dashboard / curl can
    read it without re-running the (potentially slow) iCloud filesystem call."""
    try:
        payload = {**payload, 'updated_at': datetime.now().isoformat()}
        with open(OBSIDIAN_STATUS_FILE, 'w') as f:
            json.dump(payload, f, indent=2)
    except OSError as e:
        print(f'[obsidian] cannot write status file: {e}')


def _push_to_obsidian_background(summary, days, entry_count):
    """Spawn a daemon thread that pushes the diagnosis to Obsidian via the
    Local REST API plugin. Backgrounded purely so the retrospective endpoint
    returns immediately — the HTTP call itself has its own timeout."""
    import threading
    _write_obsidian_status({'status': 'pending', 'path': '', 'error': ''})

    def _run():
        try:
            result = _push_retro_to_obsidian(summary, days, entry_count)
        except Exception as e:
            result = {'status': 'error', 'path': '', 'error': f'unexpected: {e!r}'}
        _write_obsidian_status(result)

    threading.Thread(target=_run, daemon=True).start()


@app.route('/api/obsidian/status', methods=['GET'])
def api_obsidian_status():
    """Read the most recent Obsidian-push outcome (pending / ok / error)."""
    try:
        with open(OBSIDIAN_STATUS_FILE) as f:
            return jsonify(json.load(f))
    except (OSError, json.JSONDecodeError):
        return jsonify({'status': 'unknown', 'path': '', 'error': '', 'updated_at': None})


def _push_retro_to_obsidian(summary, days, entry_count):
    """Prepend a dated diagnosis section to the Revision Tracker note via
    Obsidian's Local REST API plugin. Obsidian's process owns iCloud access,
    so Flask never touches the filesystem — it just makes an HTTP round trip
    to 127.0.0.1.

    Returns {'status': 'ok' | 'error', 'path': str, 'error': str}. Never
    raises — failure modes (auth, 404, plugin not running, network) all
    produce a clear error string."""
    import urllib.request
    import urllib.error
    import ssl

    def fail(msg):
        print(f'[obsidian] {msg}')
        return {'status': 'error', 'path': '', 'error': msg}

    if not OBSIDIAN_API_TOKEN:
        return fail('OBSIDIAN_API_TOKEN is empty — set it in .env')

    base = OBSIDIAN_API_BASE_URL.rstrip('/')
    rel = OBSIDIAN_TRACKER_VAULT_PATH.lstrip('/')
    # urllib auto-encodes the path components when we build the URL string
    # via Request — Obsidian wants the vault-relative path raw, with spaces.
    from urllib.parse import quote
    url = f'{base}/vault/{quote(rel)}'

    # HTTPS variant uses a self-signed cert by default; HTTP needs no context.
    ssl_ctx = None
    if url.startswith('https://'):
        ssl_ctx = ssl.create_default_context()
        ssl_ctx.check_hostname = False
        ssl_ctx.verify_mode = ssl.CERT_NONE

    def _request(method, body=None, content_type='application/json'):
        headers = {'Authorization': f'Bearer {OBSIDIAN_API_TOKEN}'}
        if body is not None:
            headers['Content-Type'] = content_type
        req = urllib.request.Request(url, method=method, headers=headers,
                                     data=body.encode('utf-8') if isinstance(body, str) else body)
        return urllib.request.urlopen(req, timeout=10, context=ssl_ctx) if ssl_ctx \
            else urllib.request.urlopen(req, timeout=10)

    # 1. GET the existing note (may be 404 — that's fine, we'll create it).
    existing = ''
    try:
        with _request('GET') as resp:
            existing = resp.read().decode('utf-8', errors='replace')
    except urllib.error.HTTPError as e:
        if e.code == 404:
            existing = ''
        elif e.code == 401:
            return fail('401 Unauthorized — OBSIDIAN_API_TOKEN is wrong')
        else:
            return fail(f'GET {url} failed: {e.code} {e.reason}')
    except urllib.error.URLError as e:
        return fail(f'cannot reach Obsidian REST API at {base} — is the plugin running? ({e.reason})')

    today_iso = datetime.now().date().isoformat()
    today_human = datetime.now().strftime('%A, %d %B %Y')
    new_section = (
        f'## {today_iso} — Diagnosis\n'
        f'*Generated {today_human} · last {days} days · {entry_count} attempts*\n\n'
        f'{summary.strip()}\n\n'
        f'---\n\n'
    )

    if existing:
        # Preserve YAML frontmatter; insert new section under the H1 if there
        # is one, else right after the frontmatter, else at the very top.
        fm_end = 0
        if existing.startswith('---\n'):
            second = existing.find('\n---\n', 4)
            if second != -1:
                fm_end = second + len('\n---\n')
        head, body = existing[:fm_end], existing[fm_end:]
        h1_match = re.search(r'^#\s.+?$', body, re.MULTILINE)
        if h1_match:
            insert_at = body.find('\n\n', h1_match.end())
            insert_at = insert_at + 2 if insert_at != -1 else h1_match.end() + 1
        else:
            insert_at = 0
        new_body = body[:insert_at] + new_section + body[insert_at:]
        content = head + new_body
    else:
        content = (
            '---\n'
            'tags: [revision, cambridge-ib]\n'
            'title: Revision Tracker\n'
            '---\n\n'
            '# Revision Tracker\n\n'
            'Auto-updated by the revision app every time a weekly '
            'retrospective is generated. Newest diagnosis at the top.\n\n'
        ) + new_section

    # 2. PUT the merged content back. The plugin expects text/markdown.
    try:
        with _request('PUT', body=content, content_type='text/markdown') as resp:
            if resp.status >= 400:
                return fail(f'PUT returned {resp.status}')
    except urllib.error.HTTPError as e:
        return fail(f'PUT {url} failed: {e.code} {e.reason}')
    except urllib.error.URLError as e:
        return fail(f'PUT to Obsidian failed: {e.reason}')

    print(f'[obsidian] wrote diagnosis to vault://{rel}')
    return {'status': 'ok', 'path': f'vault://{rel}', 'error': ''}


@app.route('/api/retrospective/push-obsidian', methods=['POST'])
def api_retrospective_push_obsidian():
    """Diagnostic: queue an Obsidian push for the most recently cached
    retrospective. Returns immediately with status='queued'; poll
    /api/obsidian/status (or check obsidian_status.json) for the outcome."""
    cache = _load_retro_cache()
    summary = cache.get('summary')
    if not summary:
        return jsonify({'status': 'error', 'error': 'no cached retrospective to push'}), 400
    _push_to_obsidian_background(summary, cache.get('days', 7), cache.get('entry_count', 0))
    return jsonify({'status': 'queued'})


@app.route('/api/retrospective', methods=['GET'])
def api_retrospective_get():
    """Cached weekly retrospective. Returns the latest cached summary if it's
    less than 24h old, with a flag the frontend can use to show a refresh button."""
    cache = _load_retro_cache()
    summary = cache.get('summary')
    generated_at = cache.get('generated_at')
    age_hours = None
    if generated_at:
        try:
            age = datetime.now() - datetime.fromisoformat(generated_at)
            age_hours = age.total_seconds() / 3600
        except Exception:
            pass
    return jsonify({
        'summary': summary,
        'generated_at': generated_at,
        'age_hours': age_hours,
        'covered_until': cache.get('covered_until'),
    })


@app.route('/api/retrospective', methods=['POST'])
def api_retrospective_generate():
    """Force-generate a new retrospective from the last 7 days of history."""
    days = int((request.get_json() or {}).get('days', 7))
    history = load_history()
    cutoff = datetime.now() - timedelta(days=days)
    recent = []
    for h in history:
        ts = h.get('timestamp')
        if not ts:
            continue
        try:
            t = datetime.fromisoformat(ts)
        except Exception:
            continue
        if t >= cutoff:
            recent.append(h)
    if not recent:
        return jsonify({'summary': None, 'error': 'No attempts in the last week.'}), 200

    result = weekly_retrospective(recent, days=days)
    if result.get('summary') is None:
        return jsonify({'error': result.get('error', 'Retrospective failed')}), 500

    cache = {
        'summary': result['summary'],
        'generated_at': datetime.now().isoformat(),
        'covered_until': datetime.now().date().isoformat(),
        'days': days,
        'entry_count': len(recent),
    }
    _save_retro_cache(cache)

    # Mirror to Obsidian Revision Tracker in a background thread — iCloud's
    # `cloudd` daemon can block filesystem calls for tens of seconds on a
    # cold vault directory, and we don't want the dashboard's "Refresh"
    # button to inherit that latency. Status lands in obsidian_status.json
    # (and the /api/obsidian/status endpoint).
    _push_to_obsidian_background(result['summary'], days, len(recent))
    cache['obsidian'] = {'status': 'pending', 'path': '', 'error': ''}

    return jsonify(cache)


# ---- Gap-drill: targeted follow-up for unfinished analytical chains ----

@app.route('/api/question/drill/generate', methods=['POST'])
def api_drill_generate():
    """Generate a focused follow-up that targets the missing mechanism in a
    previously-graded answer. Caller passes the original question text, the
    student's answer, the feedback they got, and (optionally) the model
    solution and key_gaps from the eval."""
    data = request.get_json() or {}
    topic_id = data.get('topic_id', '')
    course_id = data.get('course_id', '')

    courses = load_courses()
    topic_name, course_name = '', ''
    for term in courses['terms'].values():
        for cid, course in term['courses'].items():
            if cid == course_id:
                course_name = course['name']
                for t in course['topics']:
                    if t['id'] == topic_id:
                        topic_name = t['name']
                        break

    drill = generate_gap_drill(
        original_question=data.get('question', ''),
        student_answer=data.get('answer', ''),
        feedback=data.get('feedback', ''),
        key_gaps=data.get('key_gaps', []) or [],
        topic_name=topic_name,
        course_name=course_name,
        model_solution=data.get('model_solution', ''),
    )
    if not drill:
        return jsonify({'error': 'Could not generate a drill — try again.'}), 500
    return jsonify(drill)


@app.route('/api/question/drill/evaluate', methods=['POST'])
def api_drill_evaluate():
    """Grade a gap-drill answer. Single focus: did the chain get completed?"""
    data = request.get_json() or {}
    topic_id = data.get('topic_id', '')
    course_id = data.get('course_id', '')

    courses = load_courses()
    topic_name, course_name = '', ''
    for term in courses['terms'].values():
        for cid, course in term['courses'].items():
            if cid == course_id:
                course_name = course['name']
                for t in course['topics']:
                    if t['id'] == topic_id:
                        topic_name = t['name']
                        break

    result = evaluate_drill_answer(
        drill_question=data.get('drill_question', ''),
        drill_answer=data.get('drill_answer', ''),
        target_mechanism=data.get('target_mechanism', ''),
        topic_name=topic_name,
        course_name=course_name,
        marks=int(data.get('marks', 4) or 4),
    )
    return jsonify(result)


# ---- Handwritten past-paper script submission ----

def _pdf_to_page_images(pdf_bytes, dpi=180, max_pages=20):
    """Render every page of `pdf_bytes` to a PNG via pdftoppm. Returns a list of
    {'media_type', 'data'} dicts (base64-encoded PNGs) suitable for passing to
    Claude's image input. Capped at `max_pages` to bound API cost."""
    import subprocess
    import tempfile
    import base64
    import glob
    out_images = []
    with tempfile.TemporaryDirectory() as tmpdir:
        pdf_path = os.path.join(tmpdir, 'in.pdf')
        with open(pdf_path, 'wb') as f:
            f.write(pdf_bytes)
        prefix = os.path.join(tmpdir, 'page')
        try:
            subprocess.run(
                ['pdftoppm', '-png', '-r', str(dpi), pdf_path, prefix],
                check=True, capture_output=True, timeout=60,
            )
        except subprocess.CalledProcessError as e:
            print(f'[handwritten] pdftoppm failed: {e.stderr.decode("utf-8", "replace")}')
            return []
        except subprocess.TimeoutExpired:
            print('[handwritten] pdftoppm timed out')
            return []
        for png in sorted(glob.glob(prefix + '-*.png'))[:max_pages]:
            with open(png, 'rb') as f:
                out_images.append({
                    'media_type': 'image/png',
                    'data': base64.b64encode(f.read()).decode('ascii'),
                })
    return out_images


@app.route('/api/question/submit-handwritten', methods=['POST'])
def api_submit_handwritten():
    """Grade a handwritten past-paper script uploaded as a PDF.

    Form data:
        pdf:        the script (multipart file)
        topic_id:   primary topic id (for confidence tracking)
        course_id:  course id
        all_topic_ids: JSON-encoded list of topic ids tagged on this question
        parts:      JSON-encoded list of {label, question, marks}
        source:     e.g. "2022 Paper 6 Q3" (recorded on history entry)
    """
    if 'pdf' not in request.files:
        return jsonify({'error': 'No PDF uploaded'}), 400
    pdf_file = request.files['pdf']
    pdf_bytes = pdf_file.read()
    if not pdf_bytes:
        return jsonify({'error': 'Empty PDF'}), 400

    topic_id = request.form.get('topic_id')
    course_id = request.form.get('course_id')
    if not topic_id:
        return jsonify({'error': 'topic_id required'}), 400

    try:
        parts = json.loads(request.form.get('parts') or '[]')
    except json.JSONDecodeError:
        return jsonify({'error': 'parts must be valid JSON'}), 400
    if not parts:
        return jsonify({'error': 'parts is empty'}), 400

    try:
        all_topic_ids = json.loads(request.form.get('all_topic_ids') or '[]')
    except json.JSONDecodeError:
        all_topic_ids = []
    extra_topic_ids = [t for t in all_topic_ids if t != topic_id]
    source = request.form.get('source') or ''

    # Resolve topic & course names from the catalogue (same lookup as the JSON submit path)
    courses = load_courses()
    topic_name = ''
    course_name = ''
    for term in courses['terms'].values():
        for cid, course in term['courses'].items():
            if cid == course_id:
                course_name = course['name']
                for topic in course['topics']:
                    if topic['id'] == topic_id:
                        topic_name = topic['name']
                        break

    # Render PDF → page PNGs
    page_images = _pdf_to_page_images(pdf_bytes)
    if not page_images:
        return jsonify({'error': 'Could not render PDF to page images. Is it a valid PDF?'}), 400

    # Hand the whole script to Claude in one call
    part_results = evaluate_handwritten_script(
        question_parts=parts,
        page_images=page_images,
        topic_name=topic_name,
        course_name=course_name,
        source=source,
    )

    # Compute aggregates, record history, generate flashcards (mirrors api_submit_answer)
    total_marks_awarded = sum(r.get('marks_awarded', 0) for r in part_results)
    total_marks = sum(r.get('marks_available', 0) for r in part_results)
    overall_score = (total_marks_awarded / total_marks) if total_marks else 0.0
    all_gaps = []
    for r in part_results:
        all_gaps.extend(r.get('key_gaps', []))

    combined_q = ' | '.join(f"({p.get('label','')}) {p.get('question','') or p.get('text','')}" for p in parts)
    combined_a = ' | '.join(f"({r['label']}) (handwritten — see uploaded PDF)" for r in part_results)
    combined_fb = ' | '.join(f"({r['label']}) {r.get('feedback','')}" for r in part_results)
    combined_ms = ' | '.join(f"({r['label']}) {r.get('model_solution','')}" for r in part_results)

    new_confidence = record_answer(
        topic_id, course_id, combined_q, combined_a,
        overall_score, combined_fb, combined_ms,
        extra_topic_ids=extra_topic_ids,
        source=source or None,
    )

    # Queue flashcards for parts answered poorly
    for r in part_results:
        if r.get('score', 0) < 0.5 and r.get('model_solution'):
            _queue_flashcards(
                next((p.get('question', '') for p in parts if p.get('label') == r['label']), ''),
                r['model_solution'],
                topic_name, course_name, topic_id, course_id,
            )

    return jsonify({
        'part_results': [{
            'label': r['label'],
            'question_text': next((p.get('question', '') for p in parts if p.get('label') == r['label']), ''),
            'score': r['score'],
            'marks_awarded': r['marks_awarded'],
            'marks_available': r['marks_available'],
            'feedback': r['feedback'],
            'model_solution': r['model_solution'],
            'key_gaps': r['key_gaps'],
        } for r in part_results],
        'overall_score': overall_score,
        'total_marks_awarded': total_marks_awarded,
        'total_marks': total_marks,
        'new_confidence': new_confidence,
        'key_gaps': list(dict.fromkeys(all_gaps)),
        'page_count': len(page_images),
    })


# ---- Supervision Work ----

SUPERVISIONS_DIR = os.path.join(DATA_DIR, 'supervisions')


def _ensure_supervisions_dir():
    os.makedirs(SUPERVISIONS_DIR, exist_ok=True)


def _supervision_path(supo_id):
    safe = re.sub(r'[^a-zA-Z0-9_-]', '', supo_id)
    if not safe:
        return None
    return os.path.join(SUPERVISIONS_DIR, f'{safe}.json')


def _list_supervisions():
    _ensure_supervisions_dir()
    out = []
    for fn in os.listdir(SUPERVISIONS_DIR):
        if not fn.endswith('.json'):
            continue
        try:
            with open(os.path.join(SUPERVISIONS_DIR, fn)) as f:
                data = json.load(f)
            out.append({
                'id': data.get('id'),
                'title': data.get('title') or 'Untitled supervision',
                'course_name': data.get('course_name', ''),
                'created_at': data.get('created_at'),
                'updated_at': data.get('updated_at'),
                'question_count': len(data.get('questions', [])),
                'selected_count': sum(1 for q in data.get('questions', []) if q.get('selected')),
            })
        except Exception:
            continue
    out.sort(key=lambda s: s.get('updated_at') or '', reverse=True)
    return out


def _pdf_to_text(pdf_bytes):
    """Run pdftotext on the bytes; return extracted text or empty string."""
    import subprocess
    import tempfile
    with tempfile.NamedTemporaryFile(suffix='.pdf', delete=False) as tmp:
        tmp.write(pdf_bytes)
        tmp_path = tmp.name
    try:
        result = subprocess.run(
            ['pdftotext', '-layout', tmp_path, '-'],
            capture_output=True, timeout=30,
        )
        return result.stdout.decode('utf-8', errors='replace')
    except Exception as e:
        print(f'[supervision] pdftotext failed: {e}')
        return ''
    finally:
        try:
            os.unlink(tmp_path)
        except Exception:
            pass


@app.route('/api/supervision/parse', methods=['POST'])
def api_supervision_parse():
    """Accept a PDF upload, extract questions via LLM."""
    if 'pdf' not in request.files:
        return jsonify({'error': 'No PDF file uploaded'}), 400
    f = request.files['pdf']
    pdf_bytes = f.read()
    if not pdf_bytes:
        return jsonify({'error': 'Empty PDF'}), 400

    text = _pdf_to_text(pdf_bytes)
    if not text.strip():
        return jsonify({'error': 'Could not extract text from PDF'}), 400

    parsed = parse_supervision_questions(text, filename=f.filename)
    return jsonify(parsed)


@app.route('/api/supervision/sessions', methods=['GET'])
def api_supervision_list():
    return jsonify({'sessions': _list_supervisions()})


@app.route('/api/supervision/sessions', methods=['POST'])
def api_supervision_save():
    """Create or update a supervision session."""
    data = request.get_json() or {}
    supo_id = data.get('id')
    if not supo_id:
        # Generate an id: timestamp + slug of title
        slug = re.sub(r'[^a-z0-9]+', '-', (data.get('title') or 'supo').lower()).strip('-')[:30]
        supo_id = f'{datetime.now().strftime("%Y%m%d-%H%M%S")}-{slug}'

    path = _supervision_path(supo_id)
    if not path:
        return jsonify({'error': 'Invalid id'}), 400

    _ensure_supervisions_dir()

    existing = {}
    if os.path.exists(path):
        try:
            with open(path) as fp:
                existing = json.load(fp)
        except Exception:
            existing = {}

    now = datetime.now().isoformat()
    record = {
        'id': supo_id,
        'title': data.get('title', existing.get('title', 'Supervision')),
        'course_name': data.get('course_name', existing.get('course_name', '')),
        'student_name': data.get('student_name', existing.get('student_name', 'Emily Kelt')),
        'college': data.get('college', existing.get('college', 'Pembroke College')),
        'questions': data.get('questions', existing.get('questions', [])),
        'created_at': existing.get('created_at') or now,
        'updated_at': now,
    }
    with open(path, 'w') as fp:
        json.dump(record, fp, indent=2)
    return jsonify(record)


@app.route('/api/supervision/sessions/<supo_id>', methods=['GET'])
def api_supervision_get(supo_id):
    path = _supervision_path(supo_id)
    if not path or not os.path.exists(path):
        return jsonify({'error': 'Not found'}), 404
    with open(path) as fp:
        return jsonify(json.load(fp))


@app.route('/api/supervision/sessions/<supo_id>', methods=['DELETE'])
def api_supervision_delete(supo_id):
    path = _supervision_path(supo_id)
    if not path or not os.path.exists(path):
        return jsonify({'error': 'Not found'}), 404
    os.unlink(path)
    return jsonify({'ok': True})


@app.route('/api/supervision/check', methods=['POST'])
def api_supervision_check():
    """Provisional check for one supervision answer."""
    data = request.get_json() or {}
    question_text = data.get('question_text', '')
    answer_text = data.get('answer_text', '')
    course_name = data.get('course_name', '')
    sheet_title = data.get('sheet_title', '')
    if not question_text:
        return jsonify({'error': 'question_text required'}), 400
    result = provisional_check_answer(question_text, answer_text, course_name, sheet_title)
    return jsonify(result)


if __name__ == '__main__':
    import os
    # Default to 5001: macOS reserves port 5000 for AirPlay Receiver.
    port = int(os.environ.get('PORT', 5001))
    host = os.environ.get('HOST', '127.0.0.1')
    debug = os.environ.get('FLASK_DEBUG', '1') != '0'
    app.run(debug=debug, port=port, host=host)
