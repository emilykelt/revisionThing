"""Shared test setup: put the repo root on sys.path so `import data` / `import ai`
/ `import config` resolve, and provide small synthetic fixtures so tests never
touch the real (private, irreplaceable) data/ files."""
import os
import sys

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)


@pytest.fixture
def synthetic_courses():
    """A minimal courses structure matching the shape data.py expects."""
    return {
        "terms": {
            "michaelmas": {
                "label": "Michaelmas",
                "courses": {
                    "c1": {
                        "name": "Course One",
                        "lecturer": "Dr A",
                        "hours": 12,
                        "topics": [
                            {"id": "t1", "name": "Topic 1", "subtopics": ["sub-a", "sub-b"]},
                            {"id": "t2", "name": "Topic 2", "subtopics": []},
                        ],
                    }
                },
            }
        }
    }
