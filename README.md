# Cambridge Part IB CS Revision App

A personal revision web app for the Cambridge Computer Science Part IB course (2025–26), covering all 18 examinable courses across Michaelmas, Lent, and Easter terms.

## Features

- **Dashboard** — confidence progress bars across all courses and topics, with sparkline history
- **Practice sessions** — AI-generated questions weighted toward weak topics, with per-part feedback, model solutions, and confidence tracking
- **Past papers** — real Cambridge past paper questions with extracted PDF text, tagged to topics, with PDF links
- **Warm-up MCQs** — multiple-choice questions sampled across all topics, with instant feedback and KaTeX-rendered explanations
- **Course chatbot** — slide-in side panel that answers "is X in the course?" using your notes plus the Cambridge course pages
- **Image-paste answers** — paste diagrams, handwritten work, or screenshots straight into any answer textarea; Claude evaluates them alongside your text
- **Knowledge graph** — interactive D3.js force-directed graph of all topics, coloured by confidence, with cross-course semantic links
- **Flag as difficult** — mark topics you find difficult; they appear highlighted in the topic list and graph
- **Editorial bento UI** — Bricolage Grotesque + Schibsted Grotesk + Instrument Serif accents, hard-shadow cards, pill nav

## Tech Stack

- **Backend**: Python / Flask
- **AI**: Anthropic Claude API (Sonnet 4.6 for questions, evaluation, and chat; Haiku 4.5 for MCQs and PDF parsing)
- **Frontend**: Vanilla JS, KaTeX for math, D3.js v7 for the graph
- **PDF extraction**: pypdf (real text from Cambridge tripos PDFs)
- **Data**: JSON files (courses, past papers, knowledge, history)

## Setup

### 1. Clone the repo

```bash
git clone https://github.com/YOUR_USERNAME/YOUR_REPO.git
cd YOUR_REPO
```

### 2. Create a virtual environment and install dependencies

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 3. Add your Anthropic API key

Create a `.env` file in the project root:

```
ANTHROPIC_API_KEY=sk-ant-...
```

You can get a key at [console.anthropic.com](https://console.anthropic.com).

### 4. Run the app

```bash
source venv/bin/activate
python3 app.py
```

Then open [http://localhost:5001](http://localhost:5001) in your browser. (Port 5000 conflicts with macOS AirPlay Receiver.)

## Data

- `data/courses.json` — all courses, topics, and subtopics
- `data/pastpapers.json` — past paper questions with real PDF text, tagged to topics
- `data/notes_index.json` — extracted key facts per topic (used by chatbot + retag)
- `data/topic_relations.json` — cross-course semantic links for the knowledge graph
- `data/knowledge.json` — your confidence scores and streaks *(gitignored — personal)*
- `data/history.json` — your answer history *(gitignored — personal)*
- `data/pp_progress.json` — past-paper attempts *(gitignored — personal)*
- `data/anki_bank.json` — Anki-style flashcards *(gitignored — personal)*

`knowledge.json`, `history.json`, `pp_progress.json`, and `anki_bank.json` are created automatically on first run and stay local.

## Maintenance scripts

These keep the past-paper and topic data in sync with Cambridge's tripos pages.

| Script | Purpose |
|--------|---------|
| `audit_pastpapers.py` | Reconcile `pastpapers.json` against Cambridge's `t-<Course>.html` index pages. Flags wrong (paper, question) numbers, duplicates, and AI-fabricated entries. `--repair` rewrites them in place. |
| `extract_pp_text.py` | Download each question's PDF and pull the real text via pypdf + Claude. Skips entries flagged `_text_extracted`; `--force` re-extracts. |
| `retag_pastpapers.py` | Re-tag question parts against the course's real topic list using their extracted text. |
| `fetch_pastpapers.py` | Generate AI-reconstructed entries for years not yet present (fallback when no real PDF exists). |

Typical refresh flow after Cambridge publishes a new exam year:

```bash
./venv/bin/python audit_pastpapers.py --repair       # fix any drift
./venv/bin/python extract_pp_text.py                 # pull real PDF text
./venv/bin/python retag_pastpapers.py                # tag against real text
```

## Courses Covered

| Term | Courses |
|------|---------|
| Michaelmas | Concurrent & Distributed Systems, Data Science, Economics Law & Ethics, Further Graphics, Introduction to Computer Architecture, Programming in C and C++, Unix Tools |
| Lent | Compiler Construction, Computation Theory, Computer Networking, Further Human-Computer Interaction, Logic & Proof, Prolog, Semantics of Programming Languages |
| Easter | Artificial Intelligence, Complexity Theory, Cybersecurity, Formal Models of Language |
