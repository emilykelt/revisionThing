# Building a Revision PDF — the 80/20 method

A repeatable recipe for the `*_8020.tex/pdf` series (`c_cpp_8020`, `comp_arch_8020`, `compilers_8020`, …). The whole point is to produce something **frequency-weighted by the actual exam paper**, not a generic textbook recap.

---

## Inputs you need

| What | Where | Why |
| --- | --- | --- |
| Course notes / slides | `data/notes/<course-slug>*.pdf` | Source content. Use `Read` with `pages:` for big slide decks. |
| Course topic tree | `data/courses.json` → `terms.<term>.courses.<id>` | Tells you which subtopics the course officially covers. |
| Past-paper coverage | `data/tripos_coverage.json` → `by_question` | The frequency signal — exam-tagged topics per question. |

## Workflow

### 1. Pull the topic tree

```bash
python3 -c "
import json
data = json.load(open('data/courses.json'))
for term, t in data['terms'].items():
    for cid, c in t['courses'].items():
        if cid == '<course-id>':   # e.g. 'compiler-construction'
            for topic in c['topics']:
                print(topic['id'], ':', topic['name'])
                for st in topic.get('subtopics', []):
                    print('  *', st)
"
```

Use this to know what's officially in scope.

### 2. Tally exam topic frequency

```bash
python3 -c "
import json
data = json.load(open('data/tripos_coverage.json'))
by_q = data['by_question']
target = {k: v for k, v in by_q.items() if v.get('course_code') == '<CODE>'}
# course codes seen so far: ProgC, IntComArch, CompConstr
print('Total questions:', len(target))
for qid, q in sorted(target.items()):
    print(qid, '|', q.get('topics') or q.get('tags') or [])
"
```

Then tally by hand (or with `collections.Counter`) and rank topics by count. **Anything appearing in ≥30% of questions is Tier 1.** Anything in 10–30% is Tier 2. Below 10% is Tier 3 (background flavour).

### 3. Read the source notes

PDFs are in `data/notes/`. For multi-lecture courses (each lecture its own PDF):

```bash
cd data/notes && for i in 1 2 3 ... ; do
  echo "=== LEC $i ==="
  pdftotext "compiler-construction-lec${i}.pdf" -
done > /tmp/<course>.txt
```

For single big slide decks, use `Read` with the `pages:` parameter (20-page max per call). Don't try to skim everything — let the frequency table tell you which lectures to focus on.

### 4. Write the LaTeX

Use the established template (open `c_cpp_8020.tex` for the reference structure):

- **Preamble** sets up `tcolorbox` for the three callout types (`must` / `useful` / `gotcha`), `listings` with a code style matching the language, KaTeX-style colours.
- **Opening section** is *always* the frequency table and the 80/20 verdict.
- **Section 1** = Tier 1 topics with full worked examples and code.
- **Section 2** = Tier 2 topics, concise but complete.
- **Section 3** = Tier 3 supporting material (one paragraph each).
- **Closing section** = one-page exam checklist as an enumerated list inside a `must` box.

Maintain the visual style across the series — Emily prefers premium / consistent. Don't redesign callouts per document; reuse the same `tcolorbox` definitions.

#### Callout conventions

- `must` — the actual examinable material. Pink/`hi`.
- `useful` — Tier 2 material that appears every few years. Blue/`accent`.
- `gotcha` — pitfalls / classic exam traps. Red/`warn`.
- `plain{Title}` — supporting boxes inside Tier 1 (e.g. the three GC schemes in `c_cpp_8020`).

#### Code style

Each LaTeX file defines a `listings` style suitable for the course's language:
- `c_cpp_8020`: C/C++ keywords
- `comp_arch_8020`: SystemVerilog + RISC-V assembly (two language defs)
- `compilers_8020`: OCaml

Don't reuse the default `language=C` style for assembly or SV — write a `\lstdefinelanguage` block with the right keyword list.

### 5. Compile

`pdflatex` is not installed locally. Use **tectonic** (already in `/opt/homebrew/bin/tectonic`):

```bash
tectonic <course>_8020.tex
```

First run downloads packages to the tectonic cache; subsequent runs are fast. Two passes happen automatically (`aux` file changes trigger a rerun).

Fix any overfull `\hbox` warnings >20pt by shortening the offending `\verb` or `tabular` cell. Anything under 10pt is cosmetic — leave it.

### 6. Naming convention

- `<course-slug>_8020.tex` and `<course-slug>_8020.pdf` in the repo root.
- Course slug matches `data/courses.json` ID when possible (e.g. `prog-c-cpp` → `c_cpp`, `intro-comp-arch` → `comp_arch`).

---

## What "80/20" means in this series

The frequency table at the top isn't decoration — it's the spine of the document. Section lengths are roughly proportional to topic share:
- A topic appearing in 50% of questions gets a long Tier 1 subsection with worked examples.
- A topic appearing in 20% of questions gets a Tier 2 subsection of half the depth.
- A topic appearing in 10% of questions gets a paragraph in Tier 3.

**Don't write a balanced overview.** Write something that maximises marks per hour of reading.

## Topic coverage so far

| Course | File | Source frequency span |
| --- | --- | --- |
| Programming in C and C++ | `c_cpp_8020.tex` | 2018–2025, 16 questions |
| Introduction to Computer Architecture | `comp_arch_8020.tex` | 2022–2025, 12 questions |
| Compiler Construction | `compilers_8020.tex` | 2018–2025, 16 questions |
| Formal Models of Language | `fml_8020.tex` | 2018–2025, 15 questions |
| Further HCI | `fhci_8020.tex` | 2018–2025, 16 questions |
| Economics, Law and Ethics | `ele_8020.tex` | 2018–2025, 15 questions |
| Logic and Proof | `logic_8020.tex` | 2018–2025, 16 questions (algorithms how-to + rule reference) |
| Computation Theory | `comp_theory_8020.tex` | 2018–2025, 16 questions |
| Computer Networking | `comp_net_8020.tex` | 2025/26 lecturer syllabus (new setter — NOT old-paper frequency) |

When the past-paper coverage shifts (e.g. new year added to `data/tripos_coverage.json`), regenerate the frequency tally and update the opening section before reusing the same LaTeX.
