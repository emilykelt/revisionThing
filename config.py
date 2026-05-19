import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, 'data')

COURSES_FILE = os.path.join(DATA_DIR, 'courses.json')
KNOWLEDGE_FILE = os.path.join(DATA_DIR, 'knowledge.json')
HISTORY_FILE = os.path.join(DATA_DIR, 'history.json')

DEFAULT_CONFIDENCE = 0.0

# Obsidian integration: when the weekly retrospective is generated, the app
# prepends a dated section to a 'Revision Tracker' note via Obsidian's Local
# REST API plugin. Obsidian itself owns iCloud filesystem access, so Flask
# never touches cloudd — it just makes a local HTTP call.
OBSIDIAN_API_BASE_URL = os.environ.get(
    'OBSIDIAN_API_BASE_URL',
    'http://127.0.0.1:27123',
)
OBSIDIAN_API_TOKEN = os.environ.get('OBSIDIAN_API_TOKEN', '')
OBSIDIAN_TRACKER_VAULT_PATH = os.environ.get(
    'OBSIDIAN_TRACKER_VAULT_PATH',
    'Projects/Revision Tracker.md',
)
