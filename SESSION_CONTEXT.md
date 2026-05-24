# Session context — frequency ingestion, PDF marking, syllabus checks

Working notes from sessions on 2026-05-17 → 2026-05-21. Captures what changed in code, what data fields exist now, what was learned about the marking pipeline, and which marking remarks turned out to be legitimate vs over-aggressive.

---

## 1. Frequency ingestion — what the numbers mean

Two parallel maps now exist per course in `data/pastpapers.json`:

| Field | Counts | Sums to | Use case |
|---|---|---|---|
| `topic_frequencies` | One per **question part** (a 4-part question touching topic X in 3 parts → +3) | ≫ n_questions | Per-part heat-map (existing UI in `static/app.js`) |
| `topic_question_frequencies` | One per **whole question**, attributed to its **primary topic** (`q['topics'][0]`) | = n_questions exactly | Obsidian `Projects/Frequencies.md` |

### Files updated in 2026-05-17 commits
- `scripts/ingest_missing.py:204` — computes both maps after each course
- `scripts/ingest_hci.py:166` — same (was previously only per-question)
- `retag_pastpapers.py:174` — same
- `app.py:554-555, 573-574` — both maps exposed via `/api/pastpapers/all` and `/api/pastpapers/<course_id>`
- `data/pastpapers.json` — backfilled with `topic_question_frequencies` for every course

### Sanity check (verified)
After backfill, `sum(topic_question_frequencies.values()) == total_questions` for every course:
- `concurrent-distributed`: 16 questions → sum = 16
- `computer-networking`: 24 questions → sum = 24
- All 17 courses match exactly.

### Obsidian Frequencies file
`Projects/Frequencies.md` was regenerated using `topic_question_frequencies`. The 125 hand-written "Key terms to revise" entries from the old per-part version were preserved across the rewrite (parsed out by regex, re-attached after recompute).

---

## 2. PDF marking pipeline — `pdftoppm` fix

### The bug
`_pdf_to_page_images` in `app.py:1161` was rendering at fixed DPI (180), then slicing to `max_pages` *after* the fact. iOS 26 scans have absurdly large physical page sizes (1806 × 3355 pts, ~25 inches tall), so at 180 dpi each page became ~5400 × 10000 px and the subprocess timed out at 60s.

### The fix (2026-05-21)
- Switched from DPI to pixel-scaling: `pdftoppm -png -scale-to 1800 -f 1 -l max_pages …`
- Pixel-scaling caps the longer edge regardless of source page dimensions → render time becomes predictable.
- Bumped timeout 60s → 180s for headroom.
- Function now returns `(images, error_msg)` so the route can distinguish "timed out" from "invalid PDF" in the UI.

### Verified render times for the test PDF (`2025 Paper 7.pdf`, 22 MB, 4 pages, 1806×3355 pts)
- Old code (180 dpi): timed out at 60s (incomplete)
- 100 dpi flat: 64s (still over the old limit)
- `-scale-to 1800`: **13.7s**, output PNGs ~2 MB each, longer edge = 1800 px

### Callers
Single caller (`api_submit_handwritten` route, `app.py:1255`). Signature change `[]` → `(images, error)` confirmed safe.

---

## 3. Marking pipeline — end-to-end verified

Running an upload through `/api/question/submit-handwritten`:

1. **Render** — pdftoppm → list of base64 PNGs (≤ 20 pages).
2. **Evaluate** — `evaluate_handwritten_script(parts, page_images, topic_name, course_name, source)` in `ai.py:980`. Single Claude call with all parts + images.
3. **Aggregate** — sum marks awarded / available across parts.
4. **Persist** — `record_answer(...)` in `data.py:100` updates `data/knowledge.json` (primary topic) and silently updates secondary topics. Appends an entry to `data/history.json`.
5. **Flashcards** — `_queue_flashcards(...)` runs for any part scoring < 0.5 (with a model solution).
6. **PP progress** — the *frontend* posts a follow-up to `/api/pp/progress` with `ref`, `score`, `total_marks`, `marks_awarded`. The marking route itself doesn't update `pp_progress.json`.

### Data shapes (gotcha for future)
- `data/knowledge.json` is a **flat dict keyed by topic_id**, not nested under `topics`. Example: `knowledge['ele-info-econ']['confidence']`, not `knowledge['topics']['ele-info-econ']`.
- `data/anki_bank.json` is a **list of card objects**, not `{cards: [...]}`. Iterate directly.

---

## 4. Two test runs (2026-05-21)

### Run 1 — 2025 Paper 7 Q3 (Economics, Law and Ethics — info economics + game theory)
- **15/20 (75%)** in 77.5s
- Part a (adverse selection / moral hazard examples): 4/5
- Part b (Ouchbridge antivirus & adverse selection): 4/5
- Part c (cybersecurity insurance & moral hazard): 4/5
- Part d (game theory for King's Ache & Break): 3/5
- `ele-info-econ` confidence: 0.326 → 0.46
- `ele-auctions-games` silently bumped to 0.45

### Run 2 — 2025 Paper 7 Q4 ("Too much regulation can stifle…", law & AI regulation)
- **16/20 (80%)** in 81.5s
- Part a (debate on law/tech regulation): 5/6
- Part b (ADM risks + regulatory challenge): 4/5
- Part c (GDPR & training data): 4/5
- Part d (EU AI Act risk-based approach): 3/4
- `ele-law` confidence updated to 0.369
- `ele-internet-law`, `ele-contemporary` silently updated

---

## 5. Diagnosis — pattern across both questions

Emily's answers consistently show:
- **Strong intuition and concrete examples** (Ouchbridge lemons analogy, chatbot self-harm risk for AI Act, Amazon hiring bias for ADM).
- **Weakness in deploying named technical vocabulary** — losing ~1 mark per part by not invoking the specific formal term:
  - Q3: Akerlof unravelling, hidden action / principal-agent, Nash equilibrium, mixed-strategy equilibrium, zero-sum.
  - Q4: EU AI Act 4-tier structure, GDPR Article 22, lawful basis for processing, regulatory sandboxes, COMPAS.

These are flashcard-friendly additions — the substantive understanding is already there.

---

## 6. Sanity check on Claude's feedback vs the actual syllabus

The marking model can be **too aggressive** — it pulls from general field knowledge, not from Emily's lecture notes. Verified each "gap" raised against `data/courses.json` for `econ-law-ethics`:

| Term flagged by marker | On syllabus? | Verdict |
|---|---|---|
| Nash equilibrium | ✅ explicit (`ele-auctions-games` → "Nash equilibrium") | Legitimate deduction |
| Mixed-strategy equilibrium | ✅ implicit under Nash | Legitimate |
| Akerlof / lemons unravelling | ✅ implicit (`ele-market-failure` → "Adverse selection") | Legitimate |
| Hidden action / principal-agent | ✅ implicit (`ele-market-failure` → "Moral hazard") | Legitimate |
| GDPR | ✅ explicit (`ele-internet-law`) | Legitimate |
| **EU AI Act, 4-tier structure** | ❌ **not** in syllabus bullets | **Treat as bonus, not required** — the question itself names the Act |
| **GDPR Article 22** | ⚠️ implicit only (GDPR listed, specific Articles not) | Reasonable to know but harder to justify as a *required* mark |
| **COMPAS** | ⚠️ implicit only (algorithmic bias / predictive policing listed) | Good example to know, not strictly required |
| Regulatory sandboxes | ❌ not in syllabus bullets | Bonus only |

**Takeaway:** treat marker "key_gaps" as *strong-answer enrichment*, not as *examiner deductions*. Cross-check against `data/courses.json` subtopics + Emily's actual Obsidian lecture notes before assuming a marker comment maps to a lost mark.

---

## 7. Obsidian — Networking Fundamentals notes addendum

Separate task (2026-05-18): added a `## Claude's review notes (key terms & exam-ready definitions)` section to `Uni/Topics/Networking Fundamentals.md` in the vault. Filled gaps vs the Frequencies.md key-terms list:
- End-to-end principle, encapsulation, layering trade-off
- OSI ↔ TCP/IP four-layer comparison table
- Circuit vs packet switching (incl. statistical multiplexing, datagram vs virtual-circuit)
- Four delay components (processing, queuing, transmission L/R, propagation d/s) + RTT formula
- Throughput vs link rate, bottleneck-link argument, latency × bandwidth product
- Packet loss causes (with congestion flagged as the wired-network dominant cause)
- TCP / UDP / IP protocol one-liners, three-way handshake, IPv4 vs IPv6 address counts
- OSI mnemonic
- Five likely exam framings

User's original "Notes (not checked!)" section left untouched; addendum inserted before `## See also`.

Also corrected two errors in the original notes:
- TCP = Transmission Control Protocol (not "transport control protocol")
- "Data Layer" → "Data Link Layer"

---

## 8. Open / followups (not done)

- `static/app.js` heat-map at line 2633 still reads `course.topic_frequencies` (per-part). Could be switched to `topic_question_frequencies` for visual consistency with the Obsidian doc — not done; awaiting decision.
- Pylance warnings in `app.py` (`courses['terms']` access pattern) are pre-existing and unrelated to this work.
- No auto-downscale-by-filesize for PDFs yet. If a PDF still times out at the new 180s limit, that's the next lever (e.g. drop `-scale-to` to 1200 for files >40 MB).
- Marking route does not call `pp_progress.json` itself — relies on the frontend. Worth deciding whether the route should do it server-side for robustness (would simplify scripted/manual submits).
