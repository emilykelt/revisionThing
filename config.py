import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, 'data')

COURSES_FILE = os.path.join(DATA_DIR, 'courses.json')
KNOWLEDGE_FILE = os.path.join(DATA_DIR, 'knowledge.json')
HISTORY_FILE = os.path.join(DATA_DIR, 'history.json')

DEFAULT_CONFIDENCE = 0.0
