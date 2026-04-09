# Cambridge Part IB CS Revision App

A personal revision web app for the Cambridge Computer Science Part IB course (2025–26), covering all 20 examinable courses across Michaelmas, Lent, and Easter terms.

## Features

- **Dashboard** — confidence progress bars across all courses and topics, with sparkline history
- **Practice sessions** — AI-generated questions weighted toward weak topics, with per-part feedback, model solutions, and confidence tracking
- **Past papers** — real past paper questions tagged to topics, with PDF links
- **Warm-up MCQs** — multiple-choice questions sampled across all topics, with instant feedback and explanations
- **Knowledge graph** — interactive D3.js force-directed graph of all 135 topics across 18 courses, coloured by confidence, with cross-course semantic links
- **Flag as difficult** — mark topics you find difficult; they appear highlighted in the topic list and graph

## Tech Stack

- **Backend**: Python / Flask
- **AI**: Anthropic Claude API (Sonnet for questions & evaluation, Haiku for MCQs)
- **Frontend**: Vanilla JS, D3.js v7
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

Then open [http://localhost:5000](http://localhost:5000) in your browser.

## Data

- `data/courses.json` — all courses, topics, and subtopics
- `data/pastpapers.json` — past paper questions tagged to topics
- `data/topic_relations.json` — cross-course semantic links for the knowledge graph
- `data/knowledge.json` — your confidence scores and streaks *(gitignored — personal)*
- `data/history.json` — your answer history *(gitignored — personal)*

`knowledge.json` and `history.json` are created automatically on first run.

## Courses Covered

| Term | Courses |
|------|---------|
| Michaelmas | Concurrent & Distributed Systems, Compiler Construction, Artificial Intelligence, Introduction to Computer Architecture, Computer Networking |
| Lent | Cybersecurity, Computation Theory, Complexity Theory, Logic & Proof, Programming Language Concepts |
| Easter | Further Graphics, Further Human-Computer Interaction, Data Science, Semantics of Programming Languages, Prolog, Formal Models of Language, Discrete Mathematics by Example, Econometrics (Linear), C++ |
