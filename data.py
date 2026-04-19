import json
import os
from datetime import datetime
from config import COURSES_FILE, KNOWLEDGE_FILE, HISTORY_FILE, DEFAULT_CONFIDENCE


def load_json(path):
    if not os.path.exists(path):
        return None
    with open(path, 'r') as f:
        return json.load(f)


def save_json(path, data):
    with open(path, 'w') as f:
        json.dump(data, f, indent=2)


def load_courses():
    return load_json(COURSES_FILE)


def get_all_topics(courses):
    """Yield (topic, course_id, course_name) for every topic across all terms."""
    for term_id, term in courses['terms'].items():
        for course_id, course in term['courses'].items():
            for topic in course['topics']:
                yield topic, course_id, course['name']


def init_knowledge(courses):
    """Create knowledge.json with default confidence for every topic."""
    knowledge = {}
    for topic, course_id, course_name in get_all_topics(courses):
        knowledge[topic['id']] = {
            'confidence': DEFAULT_CONFIDENCE,
            'last_tested': None,
            'times_tested': 0,
            'streak': 0,
            'history': [DEFAULT_CONFIDENCE],
        }
    return knowledge


def load_knowledge():
    courses = load_courses()
    knowledge = load_json(KNOWLEDGE_FILE)
    if knowledge is None:
        knowledge = init_knowledge(courses)
        save_json(KNOWLEDGE_FILE, knowledge)
    else:
        # Ensure any new topics get initialized
        changed = False
        for topic, _, _ in get_all_topics(courses):
            if topic['id'] not in knowledge:
                knowledge[topic['id']] = {
                    'confidence': DEFAULT_CONFIDENCE,
                    'last_tested': None,
                    'times_tested': 0,
                    'streak': 0,
                    'history': [DEFAULT_CONFIDENCE],
                }
                changed = True
        if changed:
            save_json(KNOWLEDGE_FILE, knowledge)
    return knowledge


def save_knowledge(knowledge):
    save_json(KNOWLEDGE_FILE, knowledge)


def load_history():
    history = load_json(HISTORY_FILE)
    if history is None:
        history = []
        save_json(HISTORY_FILE, history)
    return history


def save_history(history):
    save_json(HISTORY_FILE, history)


def update_confidence(current, score, times_tested, streak):
    """
    Exponential moving average with decaying learning rate.
    Early answers have high impact; converges to stable updates.
    """
    alpha = max(0.15, 0.6 / (1 + 0.3 * times_tested))
    new_conf = (1 - alpha) * current + alpha * score

    # Streak bonus: 3+ correct in a row adds a small bump
    if streak >= 3 and score >= 0.6:
        new_conf += 0.03

    return round(max(0.0, min(1.0, new_conf)), 3)


def record_answer(topic_id, course_id, question, answer, score, feedback, model_solution, extra_topic_ids=None):
    """Record an answer and update confidence. Returns the new confidence."""
    knowledge = load_knowledge()
    history = load_history()

    entry = knowledge.get(topic_id)
    if entry is None:
        return None

    confidence_before = entry['confidence']

    # Update streak
    if score >= 0.6:
        entry['streak'] = entry.get('streak', 0) + 1
    else:
        entry['streak'] = 0

    entry['times_tested'] += 1
    entry['confidence'] = update_confidence(
        confidence_before, score, entry['times_tested'], entry['streak']
    )
    entry['last_tested'] = datetime.now().isoformat()
    entry['history'].append(entry['confidence'])

    # Update any extra topics (e.g. all topics tagged in a past paper question) without extra log entries
    for extra_id in (extra_topic_ids or []):
        if extra_id != topic_id:
            update_topic_confidence_silent(knowledge, extra_id, score)

    save_knowledge(knowledge)

    # Append to history log
    history.append({
        'timestamp': datetime.now().isoformat(),
        'topic_id': topic_id,
        'course_id': course_id,
        'question': question,
        'answer': answer,
        'score': score,
        'feedback': feedback,
        'model_solution': model_solution,
        'confidence_before': confidence_before,
        'confidence_after': entry['confidence'],
    })
    save_history(history)

    return entry['confidence']


def update_topic_confidence_silent(knowledge, topic_id, score):
    """Update confidence for a topic without writing a history log entry. Modifies knowledge in-place."""
    entry = knowledge.get(topic_id)
    if entry is None:
        return
    times = entry.get('times_tested', 0)
    alpha = max(0.15, 0.6 / (1 + 0.3 * times))
    entry['confidence'] = round(max(0.0, min(1.0, (1 - alpha) * entry['confidence'] + alpha * score)), 3)
    entry['history'].append(entry['confidence'])
    entry['last_tested'] = datetime.now().isoformat()


def record_mcq_answer(topic_id, course_id, is_correct):
    """Lightweight confidence update for a single MCQ result (half the weight of a full question)."""
    knowledge = load_knowledge()
    entry = knowledge.get(topic_id)
    if entry is None:
        return None
    score = 1.0 if is_correct else 0.0
    times = entry.get('times_tested', 0)
    # Half the normal alpha — MCQs count less than written answers
    alpha = max(0.07, 0.3 / (1 + 0.3 * times))
    entry['confidence'] = round(max(0.0, min(1.0, (1 - alpha) * entry['confidence'] + alpha * score)), 3)
    entry['history'].append(entry['confidence'])
    entry['last_tested'] = datetime.now().isoformat()
    save_knowledge(knowledge)
    return entry['confidence']


def get_dashboard_data():
    """Build full dashboard data with course-level and term-level aggregates."""
    courses = load_courses()
    knowledge = load_knowledge()

    dashboard = {'terms': {}}
    overall_confidences = []

    for term_id, term in courses['terms'].items():
        term_data = {
            'label': term['label'],
            'courses': {},
            'confidence': 0,
        }
        term_confidences = []

        for course_id, course in term['courses'].items():
            topic_data = []
            course_confidences = []

            for topic in course['topics']:
                k = knowledge.get(topic['id'], {})
                conf = k.get('confidence', DEFAULT_CONFIDENCE)
                course_confidences.append(conf)
                overall_confidences.append(conf)
                topic_data.append({
                    'id': topic['id'],
                    'name': topic['name'],
                    'subtopics': topic['subtopics'],
                    'confidence': conf,
                    'last_tested': k.get('last_tested'),
                    'times_tested': k.get('times_tested', 0),
                    'streak': k.get('streak', 0),
                    'history': k.get('history', [DEFAULT_CONFIDENCE]),
                    'difficult': k.get('difficult', False),
                })

            avg = sum(course_confidences) / len(course_confidences) if course_confidences else 0
            term_confidences.extend(course_confidences)

            term_data['courses'][course_id] = {
                'name': course['name'],
                'lecturer': course.get('lecturer', ''),
                'hours': course.get('hours', 0),
                'confidence': round(avg, 3),
                'topic_count': len(course['topics']),
                'topics': topic_data,
            }

        term_avg = sum(term_confidences) / len(term_confidences) if term_confidences else 0
        term_data['confidence'] = round(term_avg, 3)
        dashboard['terms'][term_id] = term_data

    overall = sum(overall_confidences) / len(overall_confidences) if overall_confidences else 0
    dashboard['overall_confidence'] = round(overall, 3)

    return dashboard


def select_session_topics(mode='weak', course_id=None, count=8):
    """
    Select topics for a revision session, weighted toward weak areas.
    mode: 'weak' (global weakest), 'course' (weakest in specific course), 'random'
    """
    import random

    courses = load_courses()
    knowledge = load_knowledge()

    candidates = []
    for topic, cid, cname in get_all_topics(courses):
        if mode == 'course' and course_id and cid != course_id:
            continue
        k = knowledge.get(topic['id'], {})
        conf = k.get('confidence', DEFAULT_CONFIDENCE)
        last_tested = k.get('last_tested')

        # Weight = (1 - confidence)^2 so low-confidence topics are heavily favored
        weight = (1 - conf) ** 2

        # Recency penalty: tested in last 2 hours gets lower weight
        if last_tested:
            try:
                dt = datetime.fromisoformat(last_tested)
                hours_ago = (datetime.now() - dt).total_seconds() / 3600
                if hours_ago < 2:
                    weight *= 0.3
            except (ValueError, TypeError):
                pass

        candidates.append({
            'topic_id': topic['id'],
            'course_id': cid,
            'topic_name': topic['name'],
            'weight': weight,
        })

    if mode == 'random':
        random.shuffle(candidates)
        return candidates[:count]

    # Weighted random sampling
    selected = []
    remaining = list(candidates)
    courses_used = {}

    for _ in range(min(count, len(remaining))):
        if not remaining:
            break

        total_weight = sum(c['weight'] for c in remaining)
        if total_weight == 0:
            break

        r = random.random() * total_weight
        cumulative = 0
        chosen = remaining[0]
        for c in remaining:
            cumulative += c['weight']
            if cumulative >= r:
                chosen = c
                break

        # Variety: no more than 2 topics from same course
        cid = chosen['course_id']
        courses_used[cid] = courses_used.get(cid, 0) + 1
        if courses_used[cid] > 2:
            # Try to pick a different one
            remaining.remove(chosen)
            continue

        selected.append(chosen)
        remaining.remove(chosen)

    return selected
