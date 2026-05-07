/* ============================================
   Part IB Revision — Frontend Application
   ============================================ */

// Muted course colours — 18 entries matching the 18 courses in order
const COURSE_PALETTE = [
    '#8B7355','#6B8B73','#7B6B8B','#8B6B73','#6B7B8B','#8B8B6B',
    '#7B8B6B','#6B8B8B','#8B736B','#736B8B','#6B8B6B','#8B7B6B',
    '#8B6B8B','#6B7B6B','#7B8B8B','#8B8B7B','#6B6B8B','#8B7B8B',
];

const app = {
    dashboardData: null,
    currentCourseId: null,
    currentQuestion: null,
    sessionTopics: [],
    sessionIndex: 0,
    historyOffset: 0,
    historyFilter: '',
    skipCount: 0,
    sessionAiOnly: false,

    // Graph state
    graphData: null,
    graphSvg: null,
    graphZoom: null,
    graphSimulation: null,
    _graphTooltipTimer: null,

    // Past Papers state
    _ppData: null,
    warmupReturnToPastPaper: null,

    // Warm-up state
    warmupMcqs: [],
    warmupIndex: 0,
    warmupCorrect: 0,
    warmupAnswered: [],
    warmupCount: 8,
    warmupMode: 'general',
    warmupPastPapers: null,
    warmupSelectedTopics: new Set(),

    // ---- Initialization ----
    async init() {
        await this.loadDashboard();
    },

    // ---- View Switching ----
    showView(viewId) {
        document.querySelectorAll('.view').forEach(v => v.classList.remove('active'));
        document.getElementById(`view-${viewId}`).classList.add('active');
        document.querySelectorAll('.nav-btn').forEach(b => {
            b.classList.toggle('active', b.dataset.view === viewId);
        });
    },

    // ---- Dashboard ----
    async loadDashboard() {
        const res = await fetch('/api/dashboard');
        this.dashboardData = await res.json();
        this.graphData = null; // invalidate so graph re-fetches fresh confidence values
        this.renderDashboard();
    },

    // First exam date — Cambridge Part IB CS Paper 1, June 8 2026.
    // Add more entries as dates are confirmed; the countdown shows the next one.
    EXAM_SCHEDULE: [
        { name: 'Paper 1', date: '2026-06-08' },
    ],

    _isoToday() {
        const d = new Date();
        return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
    },

    _solutionsUrl(pdfUrl) {
        if (!pdfUrl) return null;
        const m = pdfUrl.match(/y(\d{4})p(\d+)q(\d+)\.pdf/);
        if (!m) return null;
        const [, y, p, q] = m;
        const pp = p.padStart(2, '0');
        const qq = q.padStart(2, '0');
        return `https://www.cl.cam.ac.uk/teaching/exams/solutions/${y}/${y}-p${pp}-q${qq}-solutions.pdf`;
    },

    _reportUrl(yearOrPdfUrl) {
        // Examiners' reports are per-year (covering all papers). Accept either
        // a year or a pdf_url we can extract one from.
        let y = yearOrPdfUrl;
        if (typeof y === 'string') {
            const m = y.match(/y?(\d{4})/);
            if (!m) return null;
            y = m[1];
        }
        if (!y) return null;
        return `https://www.cl.cam.ac.uk/teaching/exams/reports/${y}.pdf`;
    },

    _renderExamCountdown() {
        const el = document.getElementById('dash-countdown');
        if (!el) return;
        const today = new Date();
        today.setHours(0, 0, 0, 0);
        const upcoming = this.EXAM_SCHEDULE
            .map(e => ({ ...e, ts: new Date(e.date + 'T00:00:00').getTime() }))
            .filter(e => e.ts >= today.getTime())
            .sort((a, b) => a.ts - b.ts);
        if (!upcoming.length) {
            el.innerHTML = `<div class="dash-card-label">Exams</div><div class="dash-countdown-num">done</div>`;
            return;
        }
        const next = upcoming[0];
        const days = Math.round((next.ts - today.getTime()) / 86400000);
        const dateStr = new Date(next.date + 'T00:00:00')
            .toLocaleDateString('en-GB', { day: 'numeric', month: 'short' });
        el.innerHTML = `
            <div class="dash-card-label">Next exam</div>
            <div class="dash-countdown-num">${days}<span class="dash-countdown-unit">days</span></div>
            <div class="dash-countdown-meta">${this.escapeHtml(next.name)} · ${dateStr}</div>
        `;
    },

    _takingCourseIds() {
        // Course IDs the user has selected at least one question for in the
        // exam planner. Returns null if nothing is selected (so callers fall
        // back to all courses).
        const sel = JSON.parse(localStorage.getItem('plannerSelections') || '{}');
        const PAPER_COURSES = {
            p4: { 1: 'compiler-construction', 2: 'compiler-construction', 3: 'semantics',
                  4: 'prolog', 5: 'prog-c-cpp', 6: 'prog-c-cpp', 7: 'cybersecurity', 8: 'cybersecurity' },
            p5: { 1: 'computer-networking', 2: 'computer-networking', 3: 'computer-networking',
                  4: 'concurrent-distributed', 5: 'concurrent-distributed',
                  6: 'intro-comp-arch', 7: 'intro-comp-arch', 8: 'intro-comp-arch' },
            p6: { 1: 'complexity-theory', 2: 'complexity-theory', 3: 'computation-theory',
                  4: 'computation-theory', 5: 'data-science', 6: 'data-science',
                  7: 'logic-proof', 8: 'logic-proof', 9: 'semantics' },
            p7: { 1: 'artificial-intelligence', 2: 'artificial-intelligence',
                  3: 'econ-law-ethics', 4: 'econ-law-ethics',
                  5: 'formal-models-language', 6: 'formal-models-language',
                  7: 'further-graphics', 8: 'further-graphics',
                  9: 'further-hci', 10: 'further-hci' },
        };
        const taking = new Set();
        for (const [pKey, qs] of Object.entries(sel)) {
            for (const [qn, on] of Object.entries(qs || {})) {
                if (on && PAPER_COURSES[pKey]?.[qn]) taking.add(PAPER_COURSES[pKey][qn]);
            }
        }
        return taking.size ? taking : null;
    },

    _pastPaperBudget(daysTillExam) {
        if (daysTillExam == null) return 1;
        if (daysTillExam <= 7)  return 4;
        if (daysTillExam <= 14) return 3;
        if (daysTillExam <= 29) return 2;
        return 1;
    },

    _daysTillNextExam() {
        const today = new Date(); today.setHours(0,0,0,0);
        const upcoming = this.EXAM_SCHEDULE
            .map(e => new Date(e.date + 'T00:00:00').getTime())
            .filter(ts => ts >= today.getTime())
            .sort();
        return upcoming.length ? Math.round((upcoming[0] - today.getTime()) / 86400000) : null;
    },

    _todoForToday() {
        // Flatten topics and score by urgency.
        const taking = this._takingCourseIds();
        const topics = [];
        for (const [termId, term] of Object.entries(this.dashboardData.terms || {})) {
            for (const [courseId, course] of Object.entries(term.courses)) {
                if (taking && !taking.has(courseId)) continue;
                const courseTopics = course.topics || [];
                courseTopics.forEach((t, idx) => {
                    topics.push({
                        ...t,
                        course_id: courseId,
                        course_name: course.name,
                        term_id: termId,
                        topic_index: idx,
                        course_topic_count: courseTopics.length,
                    });
                });
            }
        }
        const now = Date.now();
        const todayIso = this._isoToday();
        // Cheap deterministic hash → [0,1). Same id+date always returns the same
        // value, so reloads within a day are stable but tomorrow rotates.
        const dayJitter = (id) => {
            const s = id + '|' + todayIso;
            let h = 2166136261;
            for (let i = 0; i < s.length; i++) { h ^= s.charCodeAt(i); h = (h * 16777619) >>> 0; }
            return (h >>> 0) / 4294967296;
        };
        const daysTillForScore = this._daysTillNextExam();
        const dayOf = (iso) => iso ? Math.min(28, Math.floor((now - new Date(iso).getTime()) / 86400000)) : null;
        // Easter content is still being lectured, so deprioritise it relative
        // to Michaelmas/Lent. Ramps back to parity as exams approach.
        const termFactor = (termId) => {
            if (termId !== 'easter') return 1.0;
            if (daysTillForScore == null || daysTillForScore <= 14) return 1.0;
            if (daysTillForScore <= 21) return 0.7;
            return 0.4;
        };
        const score = (t) => {
            const conf = t.confidence ?? 0.5;
            let s = (1 - conf) * 1.0;
            const d = dayOf(t.last_tested);
            if (d === null) s += 0.4;          // never tested → moderate boost
            else s += Math.min(d / 14, 1) * 0.6;
            if (t.difficult) s += 0.35;
            const tf = termFactor(t.term_id);
            s *= tf;
            // Within deprioritised Easter content, surface earlier-in-course
            // topics first — those reflect what has already been lectured.
            if (t.term_id === 'easter' && tf < 1.0) {
                const denom = Math.max(1, (t.course_topic_count ?? 1) - 1);
                const pos = (t.topic_index ?? 0) / denom;
                s += (1 - pos) * 0.2;
            }
            // Small daily rotation so similarly-urgent topics swap in and out.
            s += dayJitter(t.id) * 0.25;
            return s;
        };
        topics.sort((a, b) => score(b) - score(a));

        // Daily budget: past papers ramp up; topics fill the rest.
        const daysTill = this._daysTillNextExam();
        const ppBudget = this._pastPaperBudget(daysTill);
        const TOTAL = 4;                      // items per day, incl. Anki
        const topicSlots = Math.max(0, TOTAL - 1 - ppBudget);
        const focus = topics.slice(0, topicSlots);

        // Past paper picks: prefer taking-courses, weak-or-unattempted first.
        const allPapers = this._allPapers || [];
        const ppCandidates = [];
        for (const c of allPapers) {
            if (taking && !taking.has(c.course_id)) continue;
            for (const q of (c.questions || [])) {
                ppCandidates.push({ ...q, course_id: c.course_id, course_name: c.course_name });
            }
        }
        const ppKey = (q) => `${q.course_id}-${q.year}-${q.paper}-${q.question}`;
        ppCandidates.sort((a, b) => {
            // never attempted first
            if (!a.attempts && b.attempts) return -1;
            if (a.attempts && !b.attempts) return 1;
            // then lowest best_score, with daily jitter to break ties
            const sa = (a.best_score ?? 0) + dayJitter(ppKey(a)) * 0.05;
            const sb = (b.best_score ?? 0) + dayJitter(ppKey(b)) * 0.05;
            return sa - sb;
        });
        // Avoid dumping multiple from the same course.
        const seenCourses = new Set();
        const pastPapers = [];
        for (const q of ppCandidates) {
            if (pastPapers.length >= ppBudget) break;
            if (seenCourses.has(q.course_id)) continue;
            seenCourses.add(q.course_id);
            pastPapers.push(q);
        }

        // Daily Anki rotation: deterministic by date so the same course
        // appears all day but cycles tomorrow. Restrict to taking courses.
        const courseList = [];
        for (const term of Object.values(this.dashboardData.terms || {})) {
            for (const [cid, c] of Object.entries(term.courses)) {
                if (taking && !taking.has(cid)) continue;
                courseList.push({ id: cid, name: c.name });
            }
        }
        const seed = todayIso.split('-').reduce((a, p) => a + parseInt(p), 0);
        const ankiCourse = courseList.length ? courseList[seed % courseList.length] : null;

        return { focus, pastPapers, ankiCourse, dayKey: todayIso, daysTill, ppBudget };
    },

    _renderTodoToday() {
        const el = document.getElementById('dash-todo');
        if (!el || !this.dashboardData) return;

        // Lazy-load past paper data once per session.
        if (!this._allPapers && !this._allPapersLoading) {
            this._allPapersLoading = true;
            fetch('/api/pastpapers/all')
                .then(r => r.json())
                .then(d => { this._allPapers = d.courses || []; this._renderTodoToday(); })
                .catch(() => { this._allPapers = []; })
                .finally(() => { this._allPapersLoading = false; });
        }

        const { focus, pastPapers, ankiCourse, dayKey } = this._todoForToday();
        const checkKey = `todoChecks-${dayKey}`;
        const checks = JSON.parse(localStorage.getItem(checkKey) || '{}');

        const items = [];
        for (const t of focus) {
            const id = `topic:${t.id}`;
            items.push({
                id,
                kind: 'practice',
                label: `Practice <strong>${this.escapeHtml(t.name)}</strong>`,
                meta: `${this.escapeHtml(t.course_name)} · ${Math.round((t.confidence ?? 0) * 100)}%`,
                action: `app.practiceTopicDirect('${this.escapeAttr(t.id)}','${this.escapeAttr(t.course_id)}')`,
            });
        }
        for (const q of pastPapers) {
            const id = `pp:${q.course_id}:${q.year}p${q.paper}q${q.question}`;
            items.push({
                id,
                kind: 'pp',
                label: `Past paper — <strong>${this.escapeHtml(q.ref)}</strong>`,
                meta: `${this.escapeHtml(q.course_name)} · ${q.total_marks} marks`,
                action: `app.practicePastPaper('${this.escapeAttr(q.course_id)}',${q.year},${q.paper},${q.question})`,
            });
        }
        if (ankiCourse) {
            items.push({
                id: `anki:${ankiCourse.id}`,
                kind: 'anki',
                label: `Review Anki — <strong>${this.escapeHtml(ankiCourse.name)}</strong>`,
                meta: 'Today\u2019s spaced-repetition rotation',
                action: null,
            });
        }

        const done = items.filter(i => checks[i.id]).length;

        el.innerHTML = `
            <div class="dash-todo-header">
                <div class="dash-card-label">To do today</div>
                <span class="dash-todo-count">${done} / ${items.length}</span>
            </div>
            <ul class="dash-todo-list">
                ${items.map(i => `
                    <li class="dash-todo-item${checks[i.id] ? ' checked' : ''}" data-todo-id="${this.escapeAttr(i.id)}">
                        <button class="dash-todo-check" aria-label="Toggle done"></button>
                        <div class="dash-todo-body">
                            <div class="dash-todo-label">${i.label}</div>
                            <div class="dash-todo-meta">${i.meta}</div>
                        </div>
                        ${i.action ? `<button class="dash-todo-go" onclick="${i.action}" aria-label="Open">→</button>` : ''}
                    </li>
                `).join('')}
            </ul>
        `;

        el.querySelectorAll('.dash-todo-item').forEach(li => {
            li.querySelector('.dash-todo-check').addEventListener('click', () => {
                const id = li.dataset.todoId;
                const all = JSON.parse(localStorage.getItem(checkKey) || '{}');
                all[id] = !all[id];
                localStorage.setItem(checkKey, JSON.stringify(all));
                this._renderTodoToday();
            });
        });
    },

    // ---- Past-paper completion heatmap (GitHub-style) ----
    async _renderPpHeatmap() {
        const el = document.getElementById('dash-heatmap');
        if (!el) return;
        if (!this._allPapers) {
            el.innerHTML = '<div class="dash-mini-label">Past-paper activity</div>'
                + '<div class="dash-loading">Loading…</div>';
            try {
                const res = await fetch('/api/pastpapers/all');
                const d = await res.json();
                this._allPapers = d.courses || [];
            } catch {
                el.innerHTML = '<div class="dash-mini-label">Past-paper activity</div>'
                    + '<div class="dash-empty">Failed to load.</div>';
                return;
            }
        }

        // Pull every history entry for the heatmap (in-app attempts on past-paper questions).
        // Plus completed-elsewhere entries by their stored date.
        // Combine: { iso_date → [{ ref, score|null, marked }] }
        const buckets = {};
        const todayIso = this._isoToday();
        // Build a 91-day window ending today.
        const days = [];
        const todayDate = new Date(todayIso + 'T00:00:00');
        for (let i = 90; i >= 0; i--) {
            const d = new Date(todayDate.getTime() - i * 86400000);
            const iso = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
            days.push(iso);
            buckets[iso] = [];
        }

        // Walk pp data — completed-elsewhere uses its own date.
        for (const c of this._allPapers) {
            for (const q of (c.questions || [])) {
                if (q.completed_elsewhere && q.completed_elsewhere_date && buckets[q.completed_elsewhere_date]) {
                    buckets[q.completed_elsewhere_date].push({
                        ref: q.ref,
                        score: q.completed_confidence ?? null,
                        marked: true,
                    });
                }
            }
        }

        // Walk in-app history for past-paper attempts.
        try {
            const histRes = await fetch('/api/history?limit=400');
            const hist = await histRes.json();
            for (const h of (hist.items || [])) {
                const ts = h.timestamp || '';
                const iso = ts.slice(0, 10);
                if (!buckets[iso]) continue;
                // Only count attempts on actual past-paper questions (skip warm-up/practice).
                if (!h.question || !/Paper \d+ Q\d+/i.test(h.question)) continue;
                buckets[iso].push({ ref: h.topic_id, score: h.score ?? null, marked: false });
            }
        } catch {
            // History fetch is best-effort; heatmap still renders with elsewhere-only data.
        }

        // Score per day: best score that day, or null if only "completed elsewhere" without confidence.
        const cellClass = (entries) => {
            if (!entries.length) return 'hm-0';
            const scored = entries.map(e => e.score).filter(s => typeof s === 'number');
            if (!scored.length) return 'hm-marked';   // attempted but unscored
            const best = Math.max(...scored);
            if (best >= 0.75) return 'hm-4';
            if (best >= 0.55) return 'hm-3';
            if (best >= 0.35) return 'hm-2';
            return 'hm-1';
        };

        // Compute weekday offset: align grid so columns are weeks, rows Mon→Sun.
        // Grid: 13 weeks × 7 rows.
        const grid = [];
        const firstDow = new Date(days[0] + 'T00:00:00').getDay(); // 0=Sun
        // Pad start with empty cells so day-of-week aligns vertically.
        const offset = (firstDow + 6) % 7; // shift so Mon=0
        for (let i = 0; i < offset; i++) grid.push(null);
        days.forEach(d => grid.push(d));
        // Pad end so total is multiple of 7
        while (grid.length % 7 !== 0) grid.push(null);

        const weeks = [];
        for (let i = 0; i < grid.length; i += 7) weeks.push(grid.slice(i, i + 7));

        const totalCount = days.reduce((n, d) => n + buckets[d].length, 0);

        const cellsHtml = weeks.map(week =>
            `<div class="hm-week">` +
            week.map(d => {
                if (!d) return `<div class="hm-cell hm-empty"></div>`;
                const entries = buckets[d];
                const cls = cellClass(entries);
                const tip = entries.length
                    ? `${d} — ${entries.length} attempt${entries.length !== 1 ? 's' : ''}`
                    : `${d} — none`;
                return `<div class="hm-cell ${cls}" title="${tip}"></div>`;
            }).join('') +
            `</div>`
        ).join('');

        el.innerHTML = `
            <div class="dash-mini-label">Past-paper activity <span class="dash-mini-sub">last 90 days · ${totalCount} attempt${totalCount !== 1 ? 's' : ''}</span></div>
            <div class="hm-grid">${cellsHtml}</div>
            <div class="hm-legend">
                <span>less</span>
                <span class="hm-cell hm-0"></span>
                <span class="hm-cell hm-1"></span>
                <span class="hm-cell hm-2"></span>
                <span class="hm-cell hm-3"></span>
                <span class="hm-cell hm-4"></span>
                <span>more</span>
            </div>
        `;
    },

    // ---- Weekly retrospective ----
    async _renderRetro() {
        const el = document.getElementById('dash-retro');
        if (!el) return;
        try {
            const res = await fetch('/api/retrospective');
            const d = await res.json();
            const stale = !d.summary || (d.age_hours != null && d.age_hours > 24 * 7);
            const title = `<div class="dash-mini-label">Weekly retrospective
                <button class="dash-mini-btn" onclick="app._refreshRetro()">${d.summary ? 'Refresh' : 'Generate'}</button>
            </div>`;
            if (!d.summary) {
                el.innerHTML = title + '<div class="dash-empty">No retrospective yet. Click Generate to summarise the past week.</div>';
                return;
            }
            const ageStr = d.age_hours != null
                ? (d.age_hours < 1 ? 'just now' : d.age_hours < 24 ? `${Math.round(d.age_hours)}h ago` : `${Math.round(d.age_hours / 24)}d ago`)
                : '';
            el.innerHTML = title
                + `<div class="dash-retro-body${stale ? ' stale' : ''}">${this._renderChatMarkdown(d.summary)}</div>`
                + `<div class="dash-retro-meta">Generated ${ageStr}</div>`;
        } catch {
            el.innerHTML = '<div class="dash-mini-label">Weekly retrospective</div>'
                + '<div class="dash-empty">Failed to load.</div>';
        }
    },

    async _refreshRetro() {
        const el = document.getElementById('dash-retro');
        if (el) el.innerHTML = '<div class="dash-mini-label">Weekly retrospective</div>'
            + '<div class="dash-loading">Analysing the last 7 days…</div>';
        try {
            const res = await fetch('/api/retrospective', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ days: 7 }),
            });
            const d = await res.json();
            if (d.error) {
                if (el) el.innerHTML = '<div class="dash-mini-label">Weekly retrospective</div>'
                    + `<div class="dash-empty">${this.escapeHtml(d.error)}</div>`
                    + `<button class="dash-mini-btn" onclick="app._refreshRetro()">Retry</button>`;
                return;
            }
            this._renderRetro();
        } catch (e) {
            if (el) el.innerHTML = '<div class="dash-mini-label">Weekly retrospective</div>'
                + '<div class="dash-empty">Network error.</div>';
        }
    },

    // ---- Past paper 30-minute timer ----
    PP_TIMER_DURATION_S: 30 * 60,
    _ppTimerHandle: null,
    _ppTimerEndsAt: null,    // ms epoch when paused: remaining-ms; running: end timestamp
    _ppTimerRunning: false,
    _ppTimerRemaining: null, // seconds left when paused

    togglePpTimer() {
        const display = document.getElementById('pp-timer-display');
        const btn = document.getElementById('pp-timer-btn');
        if (!display || !btn) return;

        if (this._ppTimerRunning) {
            // pause: capture remaining
            const left = Math.max(0, Math.round((this._ppTimerEndsAt - Date.now()) / 1000));
            this._ppTimerRemaining = left;
            this._ppTimerRunning = false;
            clearInterval(this._ppTimerHandle);
            this._ppTimerHandle = null;
            btn.textContent = left > 0 ? 'Resume' : 'Restart';
            return;
        }

        // start or resume
        const startSecs = this._ppTimerRemaining ?? this.PP_TIMER_DURATION_S;
        if (startSecs <= 0) {
            // Restart from full
            this._ppTimerRemaining = null;
            this._ppTimerEndsAt = Date.now() + this.PP_TIMER_DURATION_S * 1000;
            display.classList.remove('expired');
        } else {
            this._ppTimerEndsAt = Date.now() + startSecs * 1000;
        }
        this._ppTimerRunning = true;
        btn.textContent = 'Pause';
        this._tickPpTimer();
        this._ppTimerHandle = setInterval(() => this._tickPpTimer(), 250);
    },

    _tickPpTimer() {
        const display = document.getElementById('pp-timer-display');
        if (!display) { this._stopPpTimer(); return; }
        let remaining = Math.max(0, Math.round((this._ppTimerEndsAt - Date.now()) / 1000));
        const m = Math.floor(remaining / 60);
        const s = remaining % 60;
        display.textContent = `${m}:${String(s).padStart(2, '0')}`;
        display.classList.toggle('low', remaining > 0 && remaining <= 60);

        if (remaining <= 0) {
            clearInterval(this._ppTimerHandle);
            this._ppTimerHandle = null;
            this._ppTimerRunning = false;
            this._ppTimerRemaining = 0;
            display.classList.add('expired');
            const btn = document.getElementById('pp-timer-btn');
            if (btn) btn.textContent = 'Restart';
        }
    },

    _stopPpTimer() {
        if (this._ppTimerHandle) clearInterval(this._ppTimerHandle);
        this._ppTimerHandle = null;
        this._ppTimerRunning = false;
        this._ppTimerEndsAt = null;
        this._ppTimerRemaining = null;
    },

    renderDashboard() {
        const data = this.dashboardData;
        if (!data) return;

        this._renderExamCountdown();
        this._renderTodoToday();
        this._renderPpHeatmap();
        this._renderRetro();

        // Overall progress
        const pct = Math.round(data.overall_confidence * 100);
        document.getElementById('overall-pct').textContent = `${pct}%`;
        const bar = document.getElementById('overall-bar');
        bar.style.width = `${pct}%`;
        bar.className = `progress-bar-inner ${this.getConfClass(data.overall_confidence)}`;

        // Terms
        const container = document.getElementById('terms-container');
        container.innerHTML = '';

        const termOrder = ['michaelmas', 'lent', 'easter'];
        const sortedTerms = termOrder
            .filter(id => data.terms[id])
            .map(id => [id, data.terms[id]]);

        for (const [termId, term] of sortedTerms) {
            const section = document.createElement('div');
            section.className = 'term-section';

            const termPct = Math.round(term.confidence * 100);
            section.innerHTML = `
                <div class="term-header">
                    <h2 class="term-title">${term.label}</h2>
                    <span class="term-pct">${termPct}%</span>
                </div>
                <div class="course-grid" id="grid-${termId}"></div>
            `;
            container.appendChild(section);

            const grid = section.querySelector('.course-grid');
            for (const [courseId, course] of Object.entries(term.courses)) {
                const coursePct = Math.round(course.confidence * 100);
                const card = document.createElement('div');
                card.className = 'course-card';
                card.onclick = () => this.showCourse(courseId);
                card.innerHTML = `
                    <div class="course-card-name">${course.name}</div>
                    <div class="course-card-meta">${course.topic_count} topics${course.lecturer ? ' · ' + course.lecturer : ''}</div>
                    <div class="course-card-progress">
                        <div class="progress-bar-outer">
                            <div class="progress-bar-inner ${this.getConfClass(course.confidence)}" style="width: ${coursePct}%"></div>
                        </div>
                        <span class="course-card-pct">${coursePct}%</span>
                    </div>
                `;
                grid.appendChild(card);
            }
        }

        // Populate course selects for modals/filters
        this.populateCourseSelects();
    },

    async showDashboard() {
        await this.loadDashboard();
        this.showView('dashboard');
    },

    // ---- Course Detail ----
    async showCourse(courseId) {
        this.currentCourseId = courseId;

        // Fetch course data and past paper frequencies in parallel
        const [courseRes, ppRes] = await Promise.all([
            fetch(`/api/course/${courseId}`),
            fetch(`/api/pastpapers/${courseId}`),
        ]);
        const course = await courseRes.json();
        const ppData = await ppRes.json();

        if (course.error) return;

        const freqs = ppData.topic_frequencies || {};
        const totalPP = ppData.total_questions || 0;
        const ppUrl = ppData.past_paper_url;
        const ppNote = ppData.note;

        const detail = document.getElementById('course-detail');
        const coursePct = Math.round(course.confidence * 100);

        let topicsHtml = '';
        for (const topic of course.topics) {
            const tPct = Math.round(topic.confidence * 100);
            const lastTested = topic.last_tested
                ? this.timeAgo(topic.last_tested)
                : 'Not yet tested';
            const sparkline = this.renderSparkline(topic.history);
            const freq = freqs[topic.id] || 0;
            const freqBadge = freq > 0
                ? `<span class="pp-freq-badge" title="Appeared in ${freq} past paper question${freq !== 1 ? 's' : ''}">${freq} past q${freq !== 1 ? 's' : ''}</span>`
                : '';

            const difficultBadge = topic.difficult
                ? `<span class="topic-difficult-badge" title="You've flagged this as difficult">⚑</span>`
                : '';

            topicsHtml += `
                <div class="topic-row${topic.difficult ? ' topic-row--difficult' : ''}">
                    <div class="topic-info">
                        <div class="topic-name">${topic.name}${freqBadge}${difficultBadge}</div>
                        <div class="topic-meta">${lastTested}${topic.times_tested > 0 ? ' · ' + topic.times_tested + ' attempts' : ''}</div>
                    </div>
                    <div class="topic-sparkline">${sparkline}</div>
                    <div class="topic-pct" style="color: ${this.getConfColor(topic.confidence)}">${tPct}%</div>
                    <div class="topic-actions">
                        <button class="btn-practice" onclick="app.practiceTopicDirect('${topic.id}', '${courseId}')">Practice</button>
                    </div>
                </div>
            `;
        }

        let ppSummaryHtml = '';
        if (ppNote) {
            ppSummaryHtml = `<div class="pp-summary pp-note">${ppNote}</div>`;
        } else if (totalPP > 0) {
            const ppLinkHtml = ppUrl
                ? `<a href="${ppUrl}" target="_blank" rel="noopener" class="pp-link">View all past papers ↗</a>`
                : '';
            ppSummaryHtml = `<div class="pp-summary">${totalPP} past paper questions found for this course. ${ppLinkHtml}</div>`;
        }

        detail.innerHTML = `
            <div class="course-detail-header">
                <h2 class="course-detail-name">${course.name}</h2>
                <div class="course-detail-meta">${course.term} · ${course.hours ? course.hours + ' hours' : 'Practical'} · ${course.lecturer}</div>
                <div class="course-card-progress" style="max-width: 400px; margin-top: 0.75rem;">
                    <div class="progress-bar-outer large">
                        <div class="progress-bar-inner ${this.getConfClass(course.confidence)}" style="width: ${coursePct}%"></div>
                    </div>
                    <span class="course-card-pct" style="font-size: 1.1rem;">${coursePct}%</span>
                </div>
                ${ppSummaryHtml}
                <div class="course-detail-actions" style="margin-top: 1rem;">
                    <button class="btn btn-primary" onclick="app.startSession('course', '${courseId}')">Practice Weakest Topics</button>
                    <button class="btn btn-secondary" onclick="app.resetConfidence('course', '${courseId}')">Reset Progress</button>
                </div>
            </div>
            <div class="topic-list">${topicsHtml}</div>
        `;

        this.showView('course');
    },

    // ---- Question / Answer Flow ----
    async practiceTopicDirect(topicId, courseId) {
        this.sessionTopics = [{ topic_id: topicId, course_id: courseId }];
        this.sessionIndex = 0;
        this.skipCount = 0;
        await this.generateAndShowQuestion(topicId, 0);
    },

    async generateAndShowQuestion(topicId, skipCount = 0) {
        const container = document.getElementById('question-container');
        container.innerHTML = '<div class="loading"><span class="spinner"></span>Generating question...</div>';

        // Set back button
        const backBtn = document.getElementById('question-back-btn');
        if (this.currentCourseId) {
            backBtn.onclick = () => this.showCourse(this.currentCourseId);
        } else {
            backBtn.onclick = () => this.showDashboard();
        }

        this.showView('question');

        try {
            const res = await fetch('/api/question/generate', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ topic_id: topicId, attempt: skipCount, ai_only: this.sessionAiOnly }),
            });
            const question = await res.json();

            if (question.error) {
                container.innerHTML = `<div class="empty-state">Error: ${question.error}</div>`;
                return;
            }

            this.currentQuestion = question;
            this.renderQuestion(question);
        } catch (err) {
            container.innerHTML = `<div class="empty-state">Failed to generate question. Is the server running?</div>`;
        }
    },

    renderQuestion(q) {
        const container = document.getElementById('question-container');
        this._stopPpTimer();   // wipe any prior timer state

        let sourceHtml;
        if (q.is_actual_past_paper && q.source) {
            const pdfLink = q.pdf_url
                ? ` <a href="${q.pdf_url}" target="_blank" rel="noopener" class="pp-pdf-link">View PDF ↗</a>`
                : '';
            const solUrl = this._solutionsUrl(q.pdf_url);
            const solLink = solUrl
                ? ` <a href="${solUrl}" target="_blank" rel="noopener" class="pp-pdf-link pp-sol-link">Solutions ↗</a>`
                : '';
            const repUrl = this._reportUrl(q.pdf_url);
            const repLink = repUrl
                ? ` <a href="${repUrl}" target="_blank" rel="noopener" class="pp-pdf-link pp-rep-link">Report ↗</a>`
                : '';
            sourceHtml = `
                <div class="question-source-row">
                    <div class="question-source-badge pp-badge">📄 Past Paper &mdash; ${this.escapeHtml(q.source)}${pdfLink}${solLink}${repLink}</div>
                    <div class="pp-timer" id="pp-timer">
                        <span class="pp-timer-display" id="pp-timer-display">30:00</span>
                        <button class="pp-timer-btn" id="pp-timer-btn"
                            onclick="app.togglePpTimer()">Start timer</button>
                    </div>
                </div>`;
        } else if (q.source) {
            sourceHtml = `<div class="question-source-badge pp-style-badge">Based on ${this.escapeHtml(q.source)}</div>`;
        } else {
            sourceHtml = `<div class="question-source-badge ai-badge">AI Generated</div>`;
        }

        let bodyHtml = '';
        const isMultiPart = q.parts && q.parts.length > 0;
        if (isMultiPart) {
            // Progress dots — one per part, fill as user types
            const dots = q.parts.map((part, i) =>
                `<span class="pp-progress-dot" id="pp-dot-${i}" data-label="${this.escapeAttr(part.label)}">(${this.escapeHtml(part.label)})</span>`
            ).join('');
            bodyHtml = `<div class="pp-progress-bar">${dots}</div>`;

            // Multi-part question — each part gets hint + difficult flag buttons
            const difficultParts = new Set(q._difficult_parts || []);
            bodyHtml += q.parts.map((part, i) => {
                const isDifficultPart = difficultParts.has(part.label);
                return `
                <div class="question-part" id="question-part-${i}">
                    <div class="question-part-text">
                        <span class="part-label">(${this.escapeHtml(part.label)})</span>
                        ${this.renderContent(part.text)}
                        <span class="question-marks">[${part.marks} marks]</span>
                    </div>
                    <div class="answer-area">
                        <label>Answer to (${this.escapeHtml(part.label)})</label>
                        <textarea
                            class="answer-textarea part-answer"
                            data-label="${this.escapeHtml(part.label)}"
                            data-question="${this.escapeAttr(part.text)}"
                            data-marks="${part.marks}"
                            data-part-index="${i}"
                            placeholder="Type your answer to part (${this.escapeHtml(part.label)})..."
                            autocomplete="off" spellcheck="true"
                            ${i === 0 ? 'id="first-answer"' : ''}
                        ></textarea>
                        <div class="part-btn-row">
                            <button class="btn btn-ghost hint-part-btn" id="hint-btn-${i}"
                                onclick="app.requestHint(${i})">Hint</button>
                            ${q.is_actual_past_paper ? `<button class="btn btn-ghost part-flag-btn${isDifficultPart ? ' flagged' : ''}" id="part-flag-btn-${i}"
                                onclick="app.togglePartDifficult(${i})" title="${isDifficultPart ? 'Remove difficult flag' : 'Flag this part as difficult'}">
                                ${isDifficultPart ? '⚑ Difficult' : '⚐'}</button>` : ''}
                        </div>
                        <div class="hint-container" id="hint-container-${i}"></div>
                    </div>
                </div>`;
            }).join('');
        } else {
            // Single question
            bodyHtml = `
                <div class="question-box">
                    ${this.renderContent(q.question)}
                    ${q.marks ? `<div class="question-marks">[${q.marks} marks]</div>` : ''}
                </div>
                <div class="answer-area">
                    <label for="answer-input">Your Answer</label>
                    <textarea id="answer-input" class="answer-textarea" placeholder="Type your answer here..." autocomplete="off" spellcheck="true"></textarea>
                </div>
            `;
        }

        const isDifficult = q.difficult || false;
        const diagramHtml = (q.is_actual_past_paper && q.pdf_url && q.has_diagram) ? `
            <div class="diagram-notice">
                <span class="diagram-icon">⊞</span> This question references a diagram.
                <button class="btn btn-ghost diagram-toggle-btn" onclick="app.toggleDiagramEmbed(this, '${this.escapeAttr(q.pdf_url)}')">Show PDF</button>
                <div class="diagram-embed-container" style="display:none"></div>
            </div>` : (q.is_actual_past_paper && q.pdf_url ? `
            <div class="diagram-notice diagram-notice--subtle">
                <button class="btn btn-ghost diagram-toggle-btn" onclick="app.toggleDiagramEmbed(this, '${this.escapeAttr(q.pdf_url)}')">View original PDF</button>
                <div class="diagram-embed-container" style="display:none"></div>
            </div>` : '');
        const ppActionsHtml = q.is_actual_past_paper ? `
            <div class="pp-question-actions">
                <button class="btn btn-ghost pp-warmup-btn" onclick="app.startWarmupFromPastPaper()">☀ Warm up first</button>
                <button class="btn btn-ghost pp-similar-btn" onclick="app.toggleSimilarPastPapers()">Similar papers</button>
            </div>` : '';
        container.innerHTML = `
            <div class="question-header">
                <div class="question-breadcrumb">
                    ${this.escapeHtml(q.course_name)} &rsaquo; ${this.escapeHtml(q.topic_name)}
                    <span class="question-difficulty ${q.difficulty}">${q.difficulty}</span>
                    ${q.total_marks ? `<span class="total-marks">${q.total_marks} marks</span>` : ''}
                </div>
                <div class="question-header-right">
                    ${sourceHtml}
                    <button class="flag-difficult-btn ${isDifficult ? 'flagged' : ''}"
                        id="flag-difficult-btn"
                        onclick="app.flagDifficult('${q.topic_id}')"
                        title="${isDifficult ? 'Remove difficult flag' : 'Flag this topic as difficult'}">
                        ${isDifficult ? '⚑ Difficult' : '⚐ Flag as difficult'}
                    </button>
                </div>
            </div>
            ${ppActionsHtml}
            <div id="similar-pp-container"></div>
            ${diagramHtml}
            ${bodyHtml}
            <div class="question-actions">
                <button class="btn btn-primary" id="submit-btn" onclick="app.submitAnswer()">Submit Answer</button>
                ${!isMultiPart ? '<button class="btn btn-ghost" id="hint-btn" onclick="app.requestHint(null)">Hint</button>' : ''}
                <button class="btn btn-secondary" onclick="app.skipQuestion()">Skip</button>
            </div>
            ${!isMultiPart ? '<div id="hint-container"></div>' : ''}
            <div id="feedback-container"></div>
        `;

        // Focus first textarea
        setTimeout(() => {
            const ta = document.getElementById('first-answer') || document.getElementById('answer-input');
            if (ta) ta.focus();
        }, 100);

        // Reset per-question image storage
        this._answerImages = {};

        // Ctrl+Enter to submit on any textarea; update progress dots on input
        container.querySelectorAll('.answer-textarea').forEach(ta => {
            ta.addEventListener('keydown', (e) => {
                if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') this.submitAnswer();
            });
            if (ta.dataset.partIndex !== undefined) {
                ta.addEventListener('input', () => {
                    const dot = document.getElementById(`pp-dot-${ta.dataset.partIndex}`);
                    if (dot) dot.classList.toggle('filled', ta.value.trim().length > 0);
                });
            }
            this._attachMathPad(ta);
            this._attachImagePaste(ta);
        });
    },

    async requestHint(partIndex) {
        const q = this.currentQuestion;
        const isPartHint = partIndex != null;
        const btnId = isPartHint ? `hint-btn-${partIndex}` : 'hint-btn';
        const containerId = isPartHint ? `hint-container-${partIndex}` : 'hint-container';
        const hintBtn = document.getElementById(btnId);
        const hintContainer = document.getElementById(containerId);
        if (!hintBtn || !hintContainer) return;

        const questionText = isPartHint
            ? (q.parts[partIndex] ? q.parts[partIndex].text : '')
            : (q.question || '');

        hintBtn.disabled = true;
        hintBtn.textContent = 'Getting hint…';
        hintContainer.innerHTML = '<div class="hint-loading"><span class="spinner"></span></div>';

        try {
            const res = await fetch('/api/question/hint', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    question: questionText,
                    topic_name: q.topic_name,
                    course_name: q.course_name,
                    topic_id: q.topic_id,
                }),
            });
            const data = await res.json();
            if (data.hint) {
                const hintHtml = this.renderContent(data.hint).replace(/\n/g, '<br>');
                hintContainer.innerHTML = `
                    <div class="hint-box">
                        <div class="hint-label">Hint</div>
                        <div class="hint-text">${hintHtml}</div>
                    </div>`;
                hintBtn.textContent = 'Another hint';
                hintBtn.disabled = false;
            } else {
                hintContainer.innerHTML = '';
                hintBtn.textContent = 'Hint';
                hintBtn.disabled = false;
            }
        } catch (err) {
            hintContainer.innerHTML = '';
            hintBtn.textContent = 'Hint';
            hintBtn.disabled = false;
        }
    },

    async submitAnswer() {
        const q = this.currentQuestion;
        let body;

        if (q.parts && q.parts.length > 0) {
            // Collect answers for each part
            const partAnswers = [];
            document.querySelectorAll('.part-answer').forEach(ta => {
                const key = ta.id || ta.dataset.label;
                partAnswers.push({
                    label: ta.dataset.label,
                    question: ta.dataset.question,
                    answer: ta.value,
                    marks: parseInt(ta.dataset.marks) || 8,
                    images: (this._answerImages?.[key] || []).map(im => ({
                        media_type: im.media_type, data: im.data,
                    })),
                });
            });
            // Require at least one non-empty answer or attached image
            if (!partAnswers.some(p => p.answer.trim() || (p.images && p.images.length))) return;
            body = { topic_id: q.topic_id, course_id: q.course_id, parts: partAnswers, all_topic_ids: q._all_topic_ids || [],
                     has_diagram: q.has_diagram || false, source: q.source || '' };
        } else {
            const ta = document.getElementById('answer-input');
            const answer = ta.value;
            const images = (this._answerImages?.['answer-input'] || []).map(im => ({
                media_type: im.media_type, data: im.data,
            }));
            if (!answer.trim() && images.length === 0) return;
            body = { topic_id: q.topic_id, course_id: q.course_id, question: q.question, answer, images,
                     all_topic_ids: q._all_topic_ids || [] };
        }

        const submitBtn = document.getElementById('submit-btn');
        submitBtn.disabled = true;
        submitBtn.textContent = 'Evaluating...';

        const feedbackContainer = document.getElementById('feedback-container');
        feedbackContainer.innerHTML = '<div class="loading"><span class="spinner"></span>Evaluating your answer...</div>';

        try {
            const res = await fetch('/api/question/submit', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(body),
            });
            const result = await res.json();
            // Record past paper attempt progress
            if (q.is_actual_past_paper && q.source && result.overall_score != null) {
                fetch('/api/pp/progress', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        ref: q.source,
                        score: result.overall_score,
                        total_marks: result.total_marks || q.total_marks || 0,
                        marks_awarded: result.total_marks_awarded,
                    }),
                }).catch(() => {});
                this._ppData = null; // invalidate so browser shows updated score
            }
            this.renderFeedback(result);
        } catch (err) {
            feedbackContainer.innerHTML = '<div class="empty-state">Failed to evaluate. Check your connection.</div>';
            submitBtn.disabled = false;
            submitBtn.textContent = 'Submit Answer';
        }
    },

    async flagDifficult(topicId) {
        const btn = document.getElementById('flag-difficult-btn');
        if (!btn) return;
        const res = await fetch('/api/topic/flag-difficult', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ topic_id: topicId }),
        });
        const data = await res.json();
        if (data.error) return;
        const flagged = data.difficult;
        btn.classList.toggle('flagged', flagged);
        btn.textContent = flagged ? '⚑ Difficult' : '⚐ Flag as difficult';
        btn.title = flagged ? 'Remove difficult flag' : 'Flag this topic as difficult';
        if (this.currentQuestion) this.currentQuestion.difficult = flagged;
    },

    renderFeedback(result) {
        const feedbackContainer = document.getElementById('feedback-container');
        document.getElementById('submit-btn').style.display = 'none';

        const confNewPct = result.new_confidence != null
            ? Math.round(result.new_confidence * 100) : null;
        const confHtml = confNewPct != null
            ? `<div class="feedback-label" style="margin-top:0.25rem">Confidence updated to ${confNewPct}%</div>` : '';

        const gapsHtml = (result.key_gaps && result.key_gaps.length > 0)
            ? `<div class="feedback-section-title" style="margin-top:1rem">Areas to Review</div>
               <div class="key-gaps">${result.key_gaps.map(g => `<span class="gap-tag">${this.escapeHtml(g)}</span>`).join('')}</div>`
            : '';

        if (result.part_results) {
            // Multi-part feedback
            const hasMarks = result.total_marks > 0;
            const overallLabel = hasMarks
                ? `${result.total_marks_awarded}/${result.total_marks}`
                : `${Math.round(result.overall_score * 100)}%`;
            const overallClass = result.overall_score >= 0.7 ? 'high' : result.overall_score >= 0.4 ? 'medium' : 'low';

            const partsHtml = result.part_results.map(pr => {
                const hasPartMarks = pr.marks_awarded != null && pr.marks_available > 0;
                const partLabel = hasPartMarks
                    ? `${pr.marks_awarded}/${pr.marks_available}`
                    : `${Math.round(pr.score * 100)}%`;
                const pClass = pr.score >= 0.7 ? 'high' : pr.score >= 0.4 ? 'medium' : 'low';
                const modelHtml = pr.model_solution
                    ? `<button class="model-solution-toggle" onclick="this.nextElementSibling.classList.toggle('open'); this.textContent = this.textContent.includes('Show') ? 'Hide model solution' : 'Show model solution'">Show model solution</button>
                       <div class="history-detail"><div class="model-solution">${this.renderContent(pr.model_solution)}</div></div>`
                    : '';
                return `
                    <div class="part-feedback-block">
                        <div class="part-feedback-header">
                            <span class="part-label-feedback">(${this.escapeHtml(pr.label)})</span>
                            <div class="score-gauge-small ${pClass}">${partLabel}</div>
                        </div>
                        <div class="feedback-text">${this.renderContent(pr.feedback || '')}</div>
                        ${modelHtml}
                    </div>
                `;
            }).join('');

            feedbackContainer.innerHTML = `
                <div class="feedback-panel">
                    <div class="feedback-score">
                        <div class="score-gauge ${overallClass}">${overallLabel}</div>
                        <div>
                            <div class="feedback-label">Total marks</div>
                            ${confHtml}
                        </div>
                    </div>
                    <div class="feedback-section-title">Per-part Feedback</div>
                    ${partsHtml}
                    ${gapsHtml}
                    <div style="margin-top: 1.5rem; display: flex; gap: 0.75rem;">
                        ${this.getNextQuestionButton()}
                    </div>
                </div>
            `;
        } else {
            // Single question feedback
            const scoreClass = result.score >= 0.7 ? 'high' : result.score >= 0.4 ? 'medium' : 'low';
            const scorePct = Math.round(result.score * 100);
            const modelSolHtml = result.model_solution
                ? `<button class="model-solution-toggle" onclick="this.nextElementSibling.classList.toggle('open'); this.textContent = this.textContent.includes('Show') ? 'Hide model solution' : 'Show model solution'">Show model solution</button>
                   <div class="history-detail" style="margin-top: 0.5rem;">
                       <div class="model-solution">${this.renderContent(result.model_solution)}</div>
                   </div>`
                : '';

            feedbackContainer.innerHTML = `
                <div class="feedback-panel">
                    <div class="feedback-score">
                        <div class="score-gauge ${scoreClass}">${scorePct}%</div>
                        <div>
                            <div class="feedback-label">Score</div>
                            ${confHtml}
                        </div>
                    </div>
                    <div class="feedback-section-title">Feedback</div>
                    <div class="feedback-text">${this.renderContent(result.feedback || '')}</div>
                    ${gapsHtml}
                    ${modelSolHtml}
                    <div style="margin-top: 1.5rem; display: flex; gap: 0.75rem;">
                        ${this.getNextQuestionButton()}
                    </div>
                </div>
            `;
        }
    },

    getNextQuestionButton() {
        const hasMore = this.sessionIndex < this.sessionTopics.length - 1;
        if (hasMore) {
            return `<button class="btn btn-primary" onclick="app.nextSessionQuestion()">Next Question</button>
                    <button class="btn btn-secondary" onclick="app.showDashboard()">End Session</button>`;
        }
        return `<button class="btn btn-primary" onclick="app.retryCurrentTopic()">Another Question on This Topic</button>
                <button class="btn btn-secondary" onclick="app.showDashboard()">Back to Dashboard</button>`;
    },

    async skipQuestion() {
        const hasMore = this.sessionIndex < this.sessionTopics.length - 1;
        if (hasMore) {
            this.skipCount = 0;
            await this.nextSessionQuestion();
        } else {
            this.skipCount++;
            await this.retryCurrentTopic();
        }
    },

    async nextSessionQuestion() {
        this.sessionIndex++;
        const next = this.sessionTopics[this.sessionIndex];
        await this.generateAndShowQuestion(next.topic_id, 0);
    },

    async retryCurrentTopic() {
        const current = this.sessionTopics[this.sessionIndex] || this.sessionTopics[0];
        await this.generateAndShowQuestion(current.topic_id, this.skipCount);
    },

    // ---- Sessions ----
    startSessionModal() {
        document.getElementById('session-modal').style.display = 'flex';
    },

    closeModal() {
        document.getElementById('session-modal').style.display = 'none';
    },

    async startSession(mode, courseId) {
        this.sessionAiOnly = document.getElementById('session-ai-only')?.checked || false;
        this.closeModal();

        const body = { mode };
        if (courseId) body.course_id = courseId;

        try {
            const res = await fetch('/api/session/start', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(body),
            });
            const data = await res.json();

            if (data.topics && data.topics.length > 0) {
                this.sessionTopics = data.topics;
                this.sessionIndex = 0;
                this.currentCourseId = courseId || null;
                await this.generateAndShowQuestion(data.topics[0].topic_id);
            } else {
                alert('No topics available for practice.');
            }
        } catch (err) {
            alert('Failed to start session.');
        }
    },

    async startSessionCourse() {
        const select = document.getElementById('session-course-select');
        const courseId = select.value;
        if (!courseId) return;
        await this.startSession('course', courseId);
    },

    // ---- History ----
    async showHistory() {
        this.historyOffset = 0;
        this.showView('history');
        await this.loadHistory();
    },

    async loadHistory() {
        const filter = document.getElementById('history-course-filter').value;
        this.historyFilter = filter;
        this.historyOffset = 0;

        const res = await fetch(`/api/history?limit=30&offset=0`);
        const data = await res.json();

        const list = document.getElementById('history-list');

        let items = data.items;
        if (filter) {
            items = items.filter(i => i.course_id === filter);
        }

        if (items.length === 0) {
            list.innerHTML = '<div class="empty-state">No revision history yet. Start practicing to see your progress here.</div>';
            document.getElementById('history-load-more').style.display = 'none';
            return;
        }

        list.innerHTML = items.map((item, idx) => this.renderHistoryItem(item, idx)).join('');
        this.historyOffset = data.items.length;
        document.getElementById('history-load-more').style.display = data.items.length < data.total ? 'block' : 'none';
    },

    async loadMoreHistory() {
        const res = await fetch(`/api/history?limit=30&offset=${this.historyOffset}`);
        const data = await res.json();

        let items = data.items;
        if (this.historyFilter) {
            items = items.filter(i => i.course_id === this.historyFilter);
        }

        const list = document.getElementById('history-list');
        list.innerHTML += items.map((item, idx) => this.renderHistoryItem(item, this.historyOffset + idx)).join('');
        this.historyOffset += data.items.length;
        document.getElementById('history-load-more').style.display = this.historyOffset < data.total ? 'block' : 'none';
    },

    renderHistoryItem(item, idx) {
        const scoreClass = item.score >= 0.7 ? 'high' : item.score >= 0.4 ? 'medium' : 'low';
        const scoreColor = item.score >= 0.7 ? 'var(--success)' : item.score >= 0.4 ? 'var(--warning)' : 'var(--danger)';
        const scorePct = Math.round(item.score * 100);
        const timeStr = item.timestamp ? this.timeAgo(item.timestamp) : '';
        const question = item.question ? (item.question.length > 150 ? item.question.substring(0, 150) + '...' : item.question) : '';

        return `
            <div class="history-item" onclick="this.querySelector('.history-detail').classList.toggle('open')">
                <div class="history-item-header">
                    <span class="history-item-topic">${this.escapeHtml(item.topic_id)}</span>
                    <span class="history-item-score" style="color: ${scoreColor}">${scorePct}%</span>
                </div>
                <div class="history-item-meta">${this.escapeHtml(item.course_id || '')} · ${timeStr}</div>
                <div class="history-item-question">${this.escapeHtml(question)}</div>
                <div class="history-detail">
                    <div class="history-detail-section">
                        <div class="history-detail-label">Your Answer</div>
                        <div class="history-detail-text">${this.escapeHtml(item.answer || '')}</div>
                    </div>
                    <div class="history-detail-section">
                        <div class="history-detail-label">Feedback</div>
                        <div class="history-detail-text">${this.escapeHtml(item.feedback || '')}</div>
                    </div>
                    ${item.model_solution ? `
                    <div class="history-detail-section">
                        <div class="history-detail-label">Model Solution</div>
                        <div class="history-detail-text">${this.escapeHtml(item.model_solution)}</div>
                    </div>` : ''}
                    <div class="history-detail-section">
                        <div class="history-detail-label">Confidence</div>
                        <div class="history-detail-text">${Math.round((item.confidence_before || 0) * 100)}% → ${Math.round((item.confidence_after || 0) * 100)}%</div>
                    </div>
                </div>
            </div>
        `;
    },

    // ---- Reset ----
    async resetConfidence(scope, target) {
        if (!confirm(`Reset progress for ${scope === 'all' ? 'all topics' : 'this ' + scope}?`)) return;
        await fetch('/api/reset', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ scope, target }),
        });
        if (scope === 'course' && target) {
            await this.showCourse(target);
        } else {
            await this.showDashboard();
        }
    },

    // ---- Knowledge Graph ----
    async showGraph() {
        this.showView('graph');
        if (!this.graphData) await this.loadAndRenderGraph();
    },

    async loadAndRenderGraph() {
        document.getElementById('graph-loading').style.display = 'flex';
        // Remove any previous SVG
        const container = document.getElementById('graph-container');
        const oldSvg = container.querySelector('svg');
        if (oldSvg) oldSvg.remove();
        if (this.graphSimulation) { this.graphSimulation.stop(); this.graphSimulation = null; }

        try {
            const res = await fetch('/api/graph');
            this.graphData = await res.json();
        } catch (e) {
            document.getElementById('graph-loading').innerHTML = '<span class="empty-state">Failed to load graph data.</span>';
            return;
        }
        document.getElementById('graph-loading').style.display = 'none';
        this.renderGraph(this.graphData);
        this.renderGraphLegend();
    },

    renderGraph(data) {
        const COURSE_ABBREV = {
            'concurrent-distributed': 'Conc. & Dist. Systems',
            'data-science': 'Data Science',
            'econ-law-ethics': 'Econ, Law & Ethics',
            'further-graphics': 'Further Graphics',
            'intro-comp-arch': 'Computer Architecture',
            'prog-c-cpp': 'Programming C/C++',
            'compiler-construction': 'Compiler Construction',
            'computation-theory': 'Computation Theory',
            'computer-networking': 'Computer Networking',
            'further-hci': 'Further HCI',
            'logic-proof': 'Logic & Proof',
            'prolog': 'Prolog',
            'semantics': 'Semantics',
            'artificial-intelligence': 'Artificial Intelligence',
            'complexity-theory': 'Complexity Theory',
            'cybersecurity': 'Cybersecurity',
            'formal-models-language': 'Formal Models of Language',
        };

        const container = document.getElementById('graph-container');
        container.innerHTML = '';
        const width = container.offsetWidth || 1000;
        const height = Math.max(620, window.innerHeight - 180);

        const svg = d3.select(container).append('svg')
            .attr('width', width).attr('height', height);

        const g = svg.append('g');

        const zoom = d3.zoom().scaleExtent([0.2, 4])
            .on('zoom', e => g.attr('transform', e.transform));
        svg.call(zoom);
        this.graphSvg = svg; this.graphZoom = zoom;

        // ---- Grid layout: one cell per course ----
        const courseIds = Object.keys(data.courses);
        const N = courseIds.length;
        const COLS = 5;
        const ROWS = Math.ceil(N / COLS);
        const GAP = 12;
        const HEADER_H = 26;
        const NODE_PAD = 10;
        const cellW = Math.floor((width - GAP * (COLS + 1)) / COLS);
        const cellH = Math.floor((height - GAP * (ROWS + 1)) / ROWS);

        const cellPos = {};
        courseIds.forEach((cid, i) => {
            const col = i % COLS;
            const row = Math.floor(i / COLS);
            cellPos[cid] = {
                x: GAP + col * (cellW + GAP),
                y: GAP + row * (cellH + GAP),
                w: cellW, h: cellH,
            };
        });

        // Initialise node positions inside their cell
        const nodeR = d => Math.min(5 + (d.times_tested || 0) * 0.5, 9);
        data.nodes.forEach(n => {
            const c = cellPos[n.course_id];
            if (!c) return;
            n.x = c.x + c.w / 2 + (Math.random() - 0.5) * (c.w * 0.45);
            n.y = c.y + HEADER_H + (c.h - HEADER_H) / 2 + (Math.random() - 0.5) * ((c.h - HEADER_H) * 0.5);
        });

        // ---- Cell backgrounds (drawn first, under everything) ----
        const self = this;
        courseIds.forEach(cid => {
            const meta = data.courses[cid];
            const c = cellPos[cid];
            if (!c) return;
            const color = COURSE_PALETTE[meta.color_index % COURSE_PALETTE.length];
            const label = COURSE_ABBREV[cid] || meta.name;

            const cellG = g.append('g').attr('class', 'cell-group');

            // Cell body
            cellG.append('rect')
                .attr('x', c.x).attr('y', c.y)
                .attr('width', c.w).attr('height', c.h).attr('rx', 8)
                .attr('fill', color).attr('fill-opacity', 0.07)
                .attr('stroke', color).attr('stroke-opacity', 0.28).attr('stroke-width', 1.5);

            // Header band
            cellG.append('rect')
                .attr('x', c.x + 1).attr('y', c.y + 1)
                .attr('width', c.w - 2).attr('height', HEADER_H - 1).attr('rx', 7)
                .attr('fill', color).attr('fill-opacity', 0.22);

            // Course label
            const labelText = label.length > 24 ? label.substring(0, 22) + '…' : label;
            cellG.append('text')
                .attr('x', c.x + c.w / 2).attr('y', c.y + HEADER_H / 2 + 4.5)
                .attr('text-anchor', 'middle')
                .attr('fill', color)
                .attr('font-size', '10.5px').attr('font-weight', '600')
                .attr('font-family', 'Georgia, serif')
                .text(labelText);
        });

        // ---- Cross-course links ----
        // Resolve link source/target to node objects for drawing
        const nodeById = Object.fromEntries(data.nodes.map(n => [n.id, n]));
        const crossLinks = data.links
            .filter(l => l.type === 'cross')
            .map(l => ({ ...l, src: nodeById[l.source] || nodeById[l.source?.id], tgt: nodeById[l.target] || nodeById[l.target?.id] }))
            .filter(l => l.src && l.tgt);

        const showCross = document.getElementById('graph-show-cross')?.checked !== false;
        const crossLinkEl = g.append('g').attr('class', 'links-cross')
            .selectAll('line').data(crossLinks).join('line')
            .attr('stroke', '#8B7355').attr('stroke-opacity', 0.18).attr('stroke-width', 1)
            .attr('display', showCross ? null : 'none');

        // ---- Nodes ----
        const nodeEl = g.append('g').attr('class', 'nodes-group')
            .selectAll('circle').data(data.nodes).join('circle')
            .attr('class', d => 'graph-node' + (d.difficult ? ' graph-node--difficult' : ''))
            .attr('r', d => nodeR(d))
            .attr('fill', d => self.getConfColor(d.confidence))
            .attr('fill-opacity', 0.9)
            .attr('stroke', d => d.difficult ? '#A85555' : '#FAF6F0')
            .attr('stroke-width', d => d.difficult ? 2 : 1.5)
            .on('click', (event, d) => { event.stopPropagation(); self.graphNodeClick(d); })
            .on('mouseenter', (event, d) => self.graphNodeHover(event, d))
            .on('mouseleave', () => self.graphHideTooltip())
            .call(d3.drag()
                .on('start', (event, d) => { if (!event.active) sim.alphaTarget(0.3).restart(); d.fx = d.x; d.fy = d.y; })
                .on('drag', (event, d) => { d.fx = event.x; d.fy = event.y; })
                .on('end', (event, d) => { if (!event.active) sim.alphaTarget(0); d.fx = null; d.fy = null; }));

        svg.on('click', () => self.graphHideTooltip());

        // ---- Force simulation: only collision + bounding box ----
        const boundForce = alpha => {
            data.nodes.forEach(n => {
                const c = cellPos[n.course_id];
                if (!c) return;
                const r = nodeR(n) + 2;
                const x0 = c.x + NODE_PAD + r, x1 = c.x + c.w - NODE_PAD - r;
                const y0 = c.y + HEADER_H + NODE_PAD + r, y1 = c.y + c.h - NODE_PAD - r;
                if (n.x < x0) n.vx += (x0 - n.x) * 1.5 * alpha;
                if (n.x > x1) n.vx += (x1 - n.x) * 1.5 * alpha;
                if (n.y < y0) n.vy += (y0 - n.y) * 1.5 * alpha;
                if (n.y > y1) n.vy += (y1 - n.y) * 1.5 * alpha;
            });
        };

        const sim = d3.forceSimulation(data.nodes)
            .force('collide', d3.forceCollide(d => nodeR(d) + 5).strength(1).iterations(4))
            .force('bound', boundForce)
            .alphaDecay(0.025)
            .on('tick', ticked);
        this.graphSimulation = sim;

        function ticked() {
            nodeEl.attr('cx', d => d.x).attr('cy', d => d.y);
            crossLinkEl
                .attr('x1', d => d.src.x).attr('y1', d => d.src.y)
                .attr('x2', d => d.tgt.x).attr('y2', d => d.tgt.y);
        }

        // ---- Controls ----
        document.getElementById('graph-reset-zoom').onclick = () => {
            svg.transition().duration(500).call(zoom.transform, d3.zoomIdentity);
        };
        document.getElementById('graph-show-cross').onchange = e => {
            crossLinkEl.attr('display', e.target.checked ? null : 'none');
        };
    },

    graphNodeClick(d) {
        this.graphHideTooltip();
        this.currentCourseId = d.course_id;
        this.practiceTopicDirect(d.id, d.course_id);
    },

    graphNodeHover(event, d) {
        clearTimeout(this._graphTooltipTimer);
        this._graphTooltipTimer = setTimeout(() => {
            const tt = document.getElementById('graph-tooltip');
            document.getElementById('graph-tooltip-name').textContent = d.name;
            document.getElementById('graph-tooltip-course').textContent = d.course_name;
            const pct = Math.round(d.confidence * 100);
            const confEl = document.getElementById('graph-tooltip-conf');
            confEl.textContent = `Confidence: ${pct}%`;
            confEl.style.color = this.getConfColor(d.confidence);
            document.getElementById('graph-tooltip-btn').onclick = () => this.graphNodeClick(d);

            // Difficult toggle in tooltip
            const flagBtn = document.getElementById('graph-tooltip-flag');
            if (flagBtn) {
                flagBtn.textContent = d.difficult ? '⚑ Difficult' : '⚐ Flag';
                flagBtn.className = 'graph-tooltip-flag' + (d.difficult ? ' flagged' : '');
                flagBtn.onclick = async (e) => {
                    e.stopPropagation();
                    const res = await fetch('/api/topic/flag-difficult', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ topic_id: d.id }),
                    });
                    const result = await res.json();
                    d.difficult = result.difficult;
                    flagBtn.textContent = d.difficult ? '⚑ Difficult' : '⚐ Flag';
                    flagBtn.className = 'graph-tooltip-flag' + (d.difficult ? ' flagged' : '');
                    // Update node stroke live
                    d3.selectAll('.graph-node')
                        .filter(n => n.id === d.id)
                        .attr('stroke', n => n.difficult ? '#A85555' : '#FAF6F0')
                        .attr('stroke-width', n => n.difficult ? 2.5 : 1.5);
                };
            }

            const x = Math.min(event.clientX + 14, window.innerWidth  - 250);
            const y = Math.min(event.clientY + 14, window.innerHeight - 130);
            tt.style.left = x + 'px'; tt.style.top = y + 'px';
            tt.style.display = 'block';
        }, 80);
    },

    graphHideTooltip() {
        clearTimeout(this._graphTooltipTimer);
        this._graphTooltipTimer = setTimeout(() => {
            document.getElementById('graph-tooltip').style.display = 'none';
        }, 150);
    },

    renderGraphLegend() {
        document.getElementById('graph-legend').innerHTML = `
            <span class="graph-legend-item">
                <span class="graph-legend-dot" style="background:var(--danger)"></span>Low &lt;35%
            </span>
            <span class="graph-legend-item">
                <span class="graph-legend-dot" style="background:var(--warning)"></span>Medium
            </span>
            <span class="graph-legend-item">
                <span class="graph-legend-dot" style="background:var(--success)"></span>Good &gt;65%
            </span>
            <span class="graph-legend-sep">|</span>
            <span class="graph-legend-item">
                <span class="graph-legend-line graph-legend-line--intra"></span>within course
            </span>
            <span class="graph-legend-item">
                <span class="graph-legend-line graph-legend-line--cross"></span>cross-course
            </span>
        `;
    },

    // ---- Warm Up / MCQ ----
    async showWarmup() {
        document.getElementById('warmup-setup').style.display = 'block';
        document.getElementById('warmup-quiz').style.display = 'none';
        document.getElementById('warmup-results').style.display = 'none';
        this.showView('warmup');
        this.renderWarmupTopicPicker();
        if (!this.warmupPastPapers) await this.loadWarmupPastPapers();
    },

    renderWarmupTopicPicker() {
        const container = document.getElementById('warmup-topic-picker');
        if (!container || !this.dashboardData) return;

        const termOrder = ['michaelmas', 'lent', 'easter'];
        let sectionsHtml = '';

        for (const termId of termOrder) {
            const term = this.dashboardData.terms[termId];
            if (!term) continue;
            for (const [courseId, course] of Object.entries(term.courses)) {
                const topicIds = course.topics.map(t => t.id);
                const allSelected = topicIds.length > 0 && topicIds.every(id => this.warmupSelectedTopics.has(id));

                const chipsHtml = course.topics.map(topic => {
                    const color = this._warmupTopicColor(topic.confidence);
                    const sel = this.warmupSelectedTopics.has(topic.id);
                    const pct = Math.round(topic.confidence * 100);
                    return `<div class="warmup-topic-chip${sel ? ' selected' : ''}"
                        data-topic-id="${this.escapeHtml(topic.id)}"
                        onclick="app.toggleWarmupTopic('${this.escapeHtml(topic.id)}')">
                        <span class="warmup-topic-dot" style="background:${color}"></span>
                        <span class="warmup-topic-name">${this.escapeHtml(topic.name)}</span>
                        <span class="warmup-topic-pct" style="color:${color}">${pct}%</span>
                    </div>`;
                }).join('');

                sectionsHtml += `
                    <div class="warmup-topic-section">
                        <div class="warmup-topic-section-head">
                            <span>${this.escapeHtml(course.name)}</span>
                            <button class="warmup-course-select-all ${allSelected ? 'active' : ''}"
                                onclick="app.toggleCourseTopics('${this.escapeHtml(courseId)}')">
                                ${allSelected ? 'Deselect all' : 'Select all'}
                            </button>
                        </div>
                        <div class="warmup-topic-chips">${chipsHtml}</div>
                    </div>`;
            }
        }

        const selCount = this.warmupSelectedTopics.size;
        const selLabel = selCount === 0 ? 'All topics' : `${selCount} topic${selCount > 1 ? 's' : ''} selected`;

        container.innerHTML = `
            <div class="warmup-topic-picker-wrap">
                <div class="warmup-picker-header">
                    <span class="warmup-picker-count">${this.escapeHtml(selLabel)}</span>
                    <div class="warmup-picker-actions">
                        <button class="warmup-picker-btn" onclick="app.selectWeakTopics()">Weakest</button>
                        <button class="warmup-picker-btn" onclick="app.clearTopicSelection()">Clear all</button>
                    </div>
                </div>
                <div class="warmup-topic-list">${sectionsHtml}</div>
            </div>`;
    },

    toggleWarmupTopic(topicId) {
        if (this.warmupSelectedTopics.has(topicId)) {
            this.warmupSelectedTopics.delete(topicId);
        } else {
            this.warmupSelectedTopics.add(topicId);
        }
        // Update just the chip and counter without full re-render
        const chip = document.querySelector(`.warmup-topic-chip[data-topic-id="${topicId}"]`);
        if (chip) chip.classList.toggle('selected', this.warmupSelectedTopics.has(topicId));
        const counter = document.querySelector('.warmup-picker-count');
        if (counter) {
            const n = this.warmupSelectedTopics.size;
            counter.textContent = n === 0 ? 'All topics' : `${n} topic${n > 1 ? 's' : ''} selected`;
        }
    },

    selectWeakTopics() {
        this.warmupSelectedTopics.clear();
        for (const [termId, term] of Object.entries(this.dashboardData?.terms || {})) {
            for (const course of Object.values(term.courses)) {
                for (const topic of course.topics) {
                    if (topic.confidence < 0.35) this.warmupSelectedTopics.add(topic.id);
                }
            }
        }
        this.renderWarmupTopicPicker();
    },

    selectAllTopics() {
        this.warmupSelectedTopics.clear();
        this.renderWarmupTopicPicker();
    },

    clearTopicSelection() {
        this.warmupSelectedTopics.clear();
        this.renderWarmupTopicPicker();
    },

    toggleCourseTopics(courseId) {
        let topicIds = [];
        for (const term of Object.values(this.dashboardData?.terms || {})) {
            if (term.courses[courseId]) {
                topicIds = term.courses[courseId].topics.map(t => t.id);
                break;
            }
        }
        const allSelected = topicIds.length > 0 && topicIds.every(id => this.warmupSelectedTopics.has(id));
        topicIds.forEach(id => allSelected
            ? this.warmupSelectedTopics.delete(id)
            : this.warmupSelectedTopics.add(id));
        this.renderWarmupTopicPicker();
    },

    async loadWarmupPastPapers() {
        try {
            const res = await fetch('/api/pastpapers/all');
            const data = await res.json();
            this.warmupPastPapers = data.courses || [];
        } catch (_) {
            this.warmupPastPapers = [];
        }
        this.populateWarmupPpSelect();
    },

    populateWarmupPpSelect() {
        const courseSelect = document.getElementById('warmup-pp-course-select');
        if (!courseSelect) return;
        courseSelect.innerHTML = '<option value="">Select a course…</option>';
        for (const course of (this.warmupPastPapers || [])) {
            const opt = document.createElement('option');
            opt.value = course.course_id;
            opt.textContent = course.course_name;
            courseSelect.appendChild(opt);
        }
        document.getElementById('warmup-pp-q-field').style.display = 'none';
    },

    onWarmupPpCourseChange() {
        const courseId = document.getElementById('warmup-pp-course-select').value;
        const qField = document.getElementById('warmup-pp-q-field');
        const qSelect = document.getElementById('warmup-pp-select');
        if (!courseId) { qField.style.display = 'none'; return; }

        const course = (this.warmupPastPapers || []).find(c => c.course_id === courseId);
        if (!course) { qField.style.display = 'none'; return; }

        qSelect.innerHTML = '<option value="">Select a question…</option>';
        for (const q of course.questions) {
            const opt = document.createElement('option');
            opt.value = JSON.stringify({
                course_id: course.course_id,
                year: q.year,
                paper: q.paper,
                question_num: q.question,
                ref: q.ref,
            });
            opt.textContent = q.ref;
            qSelect.appendChild(opt);
        }
        qField.style.display = '';
    },

    setWarmupMode(mode) {
        this.warmupMode = mode;
        document.querySelectorAll('.warmup-mode-btn').forEach(btn => {
            btn.classList.toggle('active', btn.dataset.mode === mode);
        });
        document.getElementById('warmup-general-opts').style.display = mode === 'general' ? '' : 'none';
        document.getElementById('warmup-pp-opts').style.display = mode === 'pastpaper' ? '' : 'none';
        if (mode === 'pastpaper') {
            const cs = document.getElementById('warmup-pp-course-select');
            if (cs) cs.value = '';
            const qf = document.getElementById('warmup-pp-q-field');
            if (qf) qf.style.display = 'none';
        }
    },

    selectWarmupCount(n) {
        this.warmupCount = n;
        document.querySelectorAll('.warmup-count-btn').forEach(btn => {
            btn.classList.toggle('active', parseInt(btn.dataset.count) === n);
        });
    },

    async startWarmup() {
        this.warmupIndex = 0;
        this.warmupCorrect = 0;
        this.warmupAnswered = [];

        let body = { count: this.warmupCount };
        let titleNote = '';

        if (this.warmupMode === 'pastpaper') {
            const ppSelect = document.getElementById('warmup-pp-select');
            const val = ppSelect ? ppSelect.value : '';
            if (!val) { alert('Please select a past paper question.'); return; }
            const pp = JSON.parse(val);
            body.past_paper = pp;
            titleNote = pp.ref;
        } else {
            if (this.warmupSelectedTopics.size > 0) {
                body.topic_ids = [...this.warmupSelectedTopics];
            }
        }

        document.getElementById('warmup-setup').style.display = 'none';
        document.getElementById('warmup-quiz').style.display = 'block';
        document.getElementById('warmup-results').style.display = 'none';

        const ppLabel = document.getElementById('warmup-pp-label');
        if (ppLabel) {
            if (titleNote) {
                ppLabel.textContent = titleNote;
                ppLabel.style.display = 'block';
            } else {
                ppLabel.style.display = 'none';
            }
        }

        document.getElementById('warmup-card').innerHTML = `
            <div class="loading" style="margin-top: 3rem;"><span class="spinner"></span>Generating questions${titleNote ? ' for ' + this.escapeHtml(titleNote) : ''}…</div>
        `;

        try {

            const res = await fetch('/api/mcq/generate', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(body),
            });
            const data = await res.json();
            this.warmupMcqs = data.mcqs || [];

            if (this.warmupMcqs.length === 0) {
                document.getElementById('warmup-card').innerHTML =
                    '<div class="empty-state">Couldn\'t generate questions. Please try again.</div>';
                return;
            }

            this.warmupCount = this.warmupMcqs.length;
            this.renderWarmupQuestion();
        } catch (err) {
            document.getElementById('warmup-card').innerHTML =
                '<div class="empty-state">Failed to load questions. Is the server running?</div>';
        }
    },

    renderWarmupQuestion() {
        const q = this.warmupMcqs[this.warmupIndex];
        const total = this.warmupMcqs.length;
        const pct = Math.round((this.warmupIndex / total) * 100);

        document.getElementById('warmup-progress-fill').style.width = `${pct}%`;
        document.getElementById('warmup-q-counter').textContent = `${this.warmupIndex + 1} / ${total}`;

        const optionsHtml = ['A', 'B', 'C', 'D'].map(letter => `
            <button class="mcq-option" onclick="app.selectMcqOption('${letter}')" id="mcq-opt-${letter}">
                <span class="option-letter">${letter}.</span>
                <span>${this.renderContent(q.options[letter])}</span>
            </button>
        `).join('');

        const isLast = this.warmupIndex + 1 >= total;

        document.getElementById('warmup-card').innerHTML = `
            <div class="warmup-card">
                ${q.topic ? `<div class="warmup-q-topic">${this.escapeHtml(q.topic)}</div>` : ''}
                <div class="warmup-q-text">${this.renderContent(q.question)}</div>
                <div class="mcq-options" id="mcq-options">${optionsHtml}</div>
                <div class="mcq-explanation" id="mcq-explanation" style="display:none"></div>
                <div id="mcq-next-wrap" style="margin-top:1.25rem; text-align:right; display:none">
                    <button class="btn btn-primary" onclick="app.nextMcqQuestion()">
                        ${isLast ? 'See Results →' : 'Next →'}
                    </button>
                </div>
            </div>
        `;
    },

    selectMcqOption(letter) {
        const q = this.warmupMcqs[this.warmupIndex];
        const correct = q.correct;
        const isCorrect = letter === correct;

        // Disable all options
        document.querySelectorAll('.mcq-option').forEach(btn => { btn.disabled = true; });

        // Highlight selection
        const chosenBtn = document.getElementById(`mcq-opt-${letter}`);
        if (isCorrect) {
            chosenBtn.classList.add('correct');
            this.warmupCorrect++;
        } else {
            chosenBtn.classList.add('wrong');
            const correctBtn = document.getElementById(`mcq-opt-${correct}`);
            if (correctBtn) correctBtn.classList.add('reveal');
        }

        // Show explanation
        const expDiv = document.getElementById('mcq-explanation');
        const resultText = isCorrect ? '✓ Correct!' : `✗ Incorrect — the answer was ${correct}.`;
        expDiv.innerHTML = `<strong>${resultText}</strong> ${this.renderContent(q.explanation)}`;
        expDiv.className = `mcq-explanation ${isCorrect ? 'correct' : 'wrong'}`;
        expDiv.style.display = 'block';

        // Record answer
        this.warmupAnswered.push({
            question: q.question,
            selected: letter,
            correct: correct,
            isCorrect: isCorrect,
            explanation: q.explanation,
            topic: q.topic || '',
        });

        // Show next button
        document.getElementById('mcq-next-wrap').style.display = 'block';
    },

    nextMcqQuestion() {
        this.warmupIndex++;
        if (this.warmupIndex >= this.warmupMcqs.length) {
            this.showWarmupResults();
        } else {
            this.renderWarmupQuestion();
        }
    },

    _warmupTopicColor(ratio) {
        const c = [
            [0x8B, 0x40, 0x40],
            [0xB8, 0x86, 0x0B],
            [0x5A, 0x72, 0x47],
        ];
        let r, g, b;
        if (ratio <= 0.5) {
            const t = ratio * 2;
            r = Math.round(c[0][0] + (c[1][0] - c[0][0]) * t);
            g = Math.round(c[0][1] + (c[1][1] - c[0][1]) * t);
            b = Math.round(c[0][2] + (c[1][2] - c[0][2]) * t);
        } else {
            const t = (ratio - 0.5) * 2;
            r = Math.round(c[1][0] + (c[2][0] - c[1][0]) * t);
            g = Math.round(c[1][1] + (c[2][1] - c[1][1]) * t);
            b = Math.round(c[1][2] + (c[2][2] - c[1][2]) * t);
        }
        return `rgb(${r},${g},${b})`;
    },

    renderWarmupHeatmap(containerId) {
        const container = document.getElementById(containerId);
        if (!container) return;

        const allTopics = [...new Set(this.warmupMcqs.map(q => q.topic || 'Unknown'))];

        const stats = {};
        for (const t of allTopics) stats[t] = { correct: 0, total: 0 };
        for (const a of this.warmupAnswered) {
            const t = a.topic || 'Unknown';
            if (!stats[t]) stats[t] = { correct: 0, total: 0 };
            stats[t].total++;
            if (a.isCorrect) stats[t].correct++;
        }

        const rows = allTopics.map(topic => {
            const { correct, total } = stats[topic];
            const color = total > 0 ? this._warmupTopicColor(correct / total) : 'var(--border)';
            const scoreHtml = total > 0
                ? `<span class="heatmap-score">${correct}/${total}</span>`
                : `<span class="heatmap-score heatmap-score--none">—</span>`;
            return `<div class="heatmap-row">
                <div class="heatmap-swatch" style="background:${color}"></div>
                <span class="heatmap-topic-name">${this.escapeHtml(topic)}</span>
                ${scoreHtml}
            </div>`;
        }).join('');

        container.innerHTML = `<div class="warmup-heatmap-list">${rows}</div>`;
    },

    showWarmupResults() {
        const total = this.warmupMcqs.length;
        const correct = this.warmupCorrect;
        const pct = Math.round((correct / total) * 100);
        const emoji = pct >= 80 ? '🎉' : pct >= 60 ? '👍' : pct >= 40 ? '📚' : '💪';

        document.getElementById('warmup-quiz').style.display = 'none';
        document.getElementById('warmup-results').style.display = 'block';
        document.getElementById('warmup-score-display').textContent = `${correct}/${total}`;
        document.getElementById('warmup-score-pct').textContent = `${pct}%`;
        document.getElementById('warmup-score-emoji').textContent = emoji;
        this.renderWarmupHeatmap('warmup-heatmap-results');

        // Submit MCQ results to update confidence (reduced weight)
        const mcqResults = this.warmupAnswered.map((a, i) => ({
            topic_id: this.warmupMcqs[i]?.topic_id || '',
            course_id: this.warmupMcqs[i]?.course_id || '',
            is_correct: a.isCorrect,
        })).filter(r => r.topic_id);
        if (mcqResults.length > 0) {
            fetch('/api/mcq/submit', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ results: mcqResults }),
            }).catch(() => {});
        }

        const wrong = this.warmupAnswered.filter(a => !a.isCorrect);
        let reviewHtml = '';
        if (wrong.length === 0) {
            reviewHtml = `<p class="warmup-perfect">Perfect score! You got every question right. 🌟</p>`;
        } else {
            const items = wrong.map(a => `
                <div class="warmup-review-item">
                    <div>
                        <div class="warmup-review-q">${this.renderContent(a.question)}</div>
                        <div class="warmup-review-meta">
                            <span class="warmup-review-wrong">You chose ${a.selected}</span>
                            &middot;
                            <span class="warmup-review-correct">Correct: ${a.correct}</span>
                        </div>
                        <div class="warmup-review-exp">${this.renderContent(a.explanation)}</div>
                    </div>
                </div>
            `).join('');
            reviewHtml = `
                <div class="feedback-section-title" style="text-align:left; margin-bottom:0.75rem;">
                    Questions to review
                </div>
                <div class="warmup-review-list">${items}</div>
            `;
        }
        document.getElementById('warmup-review-section').innerHTML = reviewHtml;

        // Show "Back to Question" button if we came from a past paper
        const actionsEl = document.querySelector('.warmup-results-actions');
        if (actionsEl) {
            const existing = actionsEl.querySelector('.btn-back-to-pp');
            if (existing) existing.remove();
            if (this.warmupReturnToPastPaper) {
                const btn = document.createElement('button');
                btn.className = 'btn btn-primary btn-back-to-pp';
                btn.textContent = 'Back to Question →';
                btn.onclick = () => {
                    const r = this.warmupReturnToPastPaper;
                    this.warmupReturnToPastPaper = null;
                    this.practicePastPaper(r.courseId, r.year, r.paper, r.qnum);
                };
                actionsEl.insertBefore(btn, actionsEl.firstChild);
            }
        }
    },

    // ---- Helpers ----
    populateCourseSelects() {
        const data = this.dashboardData;
        if (!data) return;

        const selects = [
            document.getElementById('session-course-select'),
            document.getElementById('history-course-filter'),
            document.getElementById('chat-course-select'),
        ];

        for (const select of selects) {
            if (!select) continue;
            const firstOption = select.querySelector('option');
            select.innerHTML = '';
            select.appendChild(firstOption);

            for (const [termId, term] of Object.entries(data.terms)) {
                for (const [courseId, course] of Object.entries(term.courses)) {
                    const opt = document.createElement('option');
                    opt.value = courseId;
                    opt.textContent = course.name;
                    select.appendChild(opt);
                }
            }
        }
    },

    getConfClass(confidence) {
        if (confidence < 0.35) return 'low';
        if (confidence < 0.65) return 'medium';
        return '';
    },

    getConfColor(confidence) {
        if (confidence < 0.35) return 'var(--danger)';
        if (confidence < 0.65) return 'var(--warning)';
        return 'var(--success)';
    },

    renderSparkline(history) {
        if (!history || history.length < 2) return '';
        // Show last 15 data points
        const points = history.slice(-15);
        return points.map(v => {
            const h = Math.max(2, Math.round(v * 20));
            return `<div class="topic-sparkline-bar" style="height: ${h}px"></div>`;
        }).join('');
    },

    timeAgo(isoString) {
        const date = new Date(isoString);
        const now = new Date();
        const diffMs = now - date;
        const diffMins = Math.floor(diffMs / 60000);
        const diffHours = Math.floor(diffMs / 3600000);
        const diffDays = Math.floor(diffMs / 86400000);

        if (diffMins < 1) return 'Just now';
        if (diffMins < 60) return `${diffMins}m ago`;
        if (diffHours < 24) return `${diffHours}h ago`;
        if (diffDays < 7) return `${diffDays}d ago`;
        return date.toLocaleDateString('en-GB', { day: 'numeric', month: 'short' });
    },

    // ---- Past Papers ----
    async showPastPapers() {
        this.showView('pastpapers');
        if (!this._ppData) {
            document.getElementById('pp-list').innerHTML = '<div class="loading"><span class="spinner"></span>Loading…</div>';
            const res = await fetch('/api/pastpapers/all');
            this._ppData = await res.json();
            // Populate course filter
            const courseSel = document.getElementById('pp-course-filter');
            this._ppData.courses.forEach(c => {
                const opt = document.createElement('option');
                opt.value = c.course_id; opt.textContent = c.course_name;
                courseSel.appendChild(opt);
            });
            // Populate year filter
            const years = new Set();
            for (const c of this._ppData.courses) {
                for (const q of c.questions) years.add(q.year);
            }
            const yearSel = document.getElementById('pp-year-filter');
            [...years].sort((a, b) => b - a).forEach(y => {
                const opt = document.createElement('option');
                opt.value = y; opt.textContent = y;
                yearSel.appendChild(opt);
            });
        }
        this.renderPastPapers();
    },

    renderPastPapers() {
        if (!this._ppData) return;
        const yearFilter = document.getElementById('pp-year-filter').value;
        const courseFilter = document.getElementById('pp-course-filter').value;
        const container = document.getElementById('pp-list');
        const html = this._ppData.courses.map(course => {
            if (courseFilter && course.course_id !== courseFilter) return '';
            const qs = yearFilter
                ? course.questions.filter(q => String(q.year) === yearFilter)
                : course.questions;
            if (!qs.length) return '';
            const rows = qs.map(q => {
                const attempted = q.attempts > 0;
                const marksLabel = (attempted && q.best_marks != null)
                    ? `${q.best_marks}/${q.total_marks}`
                    : attempted ? `${Math.round(q.best_score * 100)}%` : null;
                const scoreClass = attempted
                    ? (q.best_score >= 0.7 ? 'pp-score--good' : q.best_score >= 0.4 ? 'pp-score--mid' : 'pp-score--low')
                    : '';
                const completionHtml = marksLabel
                    ? `<span class="pp-score ${scoreClass}" title="${q.attempts} attempt${q.attempts !== 1 ? 's' : ''}">${marksLabel}</span>`
                    : '';
                const diagHtml = q.has_diagram ? `<span class="pp-diagram-badge" title="Contains diagram">⊞</span>` : '';
                const hardParts = (q.difficult_parts || []);
                const hardHtml = hardParts.length > 0
                    ? `<span class="pp-hard-badge" title="Parts flagged difficult: ${hardParts.join(', ')}">⚑ ${hardParts.join('')}</span>`
                    : '';
                const elsewhereCls = q.completed_elsewhere ? ' pp-row--elsewhere' : '';
                const elsewhereTitle = q.completed_elsewhere
                    ? `Marked done elsewhere${q.completed_elsewhere_date ? ' on ' + q.completed_elsewhere_date : ''}`
                    : 'Mark as completed elsewhere (e.g. on paper)';
                const elsewhereLabel = q.completed_elsewhere ? '✓ Done elsewhere' : '⌂ Done elsewhere';
                return `<div class="pp-row${attempted ? ' pp-row--attempted' : ''}${elsewhereCls}">
                    <div class="pp-row-ref">${this.escapeHtml(q.ref)} ${completionHtml}</div>
                    <div class="pp-row-meta">
                        <span class="pp-marks">${q.total_marks} marks</span>
                        <span class="pp-parts">${q.parts.length} parts</span>
                        ${diagHtml}${hardHtml}
                        ${q.pdf_url ? `<a class="pp-pdf-link" href="${q.pdf_url}" target="_blank" rel="noopener">PDF ↗</a>` : ''}
                        ${this._solutionsUrl(q.pdf_url) ? `<a class="pp-pdf-link pp-sol-link" href="${this._solutionsUrl(q.pdf_url)}" target="_blank" rel="noopener">Solutions ↗</a>` : ''}
                        ${this._reportUrl(q.year) ? `<a class="pp-pdf-link pp-rep-link" href="${this._reportUrl(q.year)}" target="_blank" rel="noopener">Report ↗</a>` : ''}
                        <button class="pp-elsewhere-btn${q.completed_elsewhere ? ' active' : ''}"
                            title="${this.escapeAttr(elsewhereTitle)}"
                            onclick="app.toggleCompletedElsewhere('${this.escapeAttr(q.ref)}')">${elsewhereLabel}</button>
                    </div>
                    <button class="btn btn-primary pp-practice-btn"
                        onclick="app.practicePastPaper('${this.escapeAttr(course.course_id)}', ${q.year}, ${q.paper}, ${q.question})">
                        Practice →
                    </button>
                </div>`;
            }).join('');
            const freqHtml = this._renderTopicFrequency(course);
            return `<div class="pp-course-section">
                <div class="pp-course-header">
                    <span class="pp-course-name">${this.escapeHtml(course.course_name)}</span>
                    <span class="pp-course-count">${qs.length} question${qs.length !== 1 ? 's' : ''}</span>
                </div>
                ${freqHtml}
                <div class="pp-course-rows">${rows}</div>
            </div>`;
        }).join('');
        container.innerHTML = html || '<div class="empty-state">No past papers found.</div>';
    },

    _renderTopicFrequency(course) {
        // Show how often each topic has appeared in past papers (across all years
        // we have data for). Cells are sized by frequency so high-frequency topics
        // visually dominate. Topics absent from this course's frequency map are
        // skipped — the frequency map is the source of truth.
        const freqs = course.topic_frequencies || {};
        const names = course.topic_names || {};
        const entries = Object.entries(freqs);
        if (!entries.length) return '';
        entries.sort((a, b) => b[1] - a[1]);
        const max = entries[0][1] || 1;
        const cells = entries.map(([tid, n]) => {
            const intensity = Math.min(1, n / max);
            // Map intensity → 5 buckets matching the dashboard heatmap palette.
            const bucket = intensity >= 0.85 ? 4
                : intensity >= 0.6 ? 3
                : intensity >= 0.35 ? 2
                : intensity >= 0.15 ? 1 : 0;
            const label = names[tid] || tid;
            return `<span class="pp-freq-cell hm-${bucket}" title="${this.escapeAttr(label)}: ${n} appearance${n !== 1 ? 's' : ''} since 2018">
                <span class="pp-freq-name">${this.escapeHtml(label)}</span>
                <span class="pp-freq-count">${n}</span>
            </span>`;
        }).join('');
        return `<div class="pp-freq">
            <div class="pp-freq-label">Topic frequency in past papers</div>
            <div class="pp-freq-cells">${cells}</div>
        </div>`;
    },

    async toggleCompletedElsewhere(ref) {
        try {
            const res = await fetch('/api/pp/progress', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ ref, toggle_completed_elsewhere: true }),
            });
            const updated = await res.json();
            // Update local cache so re-render reflects the change without refetch.
            for (const c of (this._ppData?.courses || [])) {
                for (const q of (c.questions || [])) {
                    if (q.ref === ref) {
                        q.completed_elsewhere = !!updated.completed_elsewhere;
                        q.completed_elsewhere_date = updated.completed_elsewhere_date || null;
                    }
                }
            }
            // Mirror onto _allPapers (used by dashboard heatmap & todo).
            for (const c of (this._allPapers || [])) {
                for (const q of (c.questions || [])) {
                    if (q.ref === ref) {
                        q.completed_elsewhere = !!updated.completed_elsewhere;
                        q.completed_elsewhere_date = updated.completed_elsewhere_date || null;
                    }
                }
            }
            this.renderPastPapers();
        } catch (e) {
            console.error('toggleCompletedElsewhere failed', e);
        }
    },

    async startWarmupFromPastPaper() {
        const q = this.currentQuestion;
        if (!q) return;

        // Store where to return after warmup
        this.warmupReturnToPastPaper = {
            courseId: q.course_id,
            year: parseInt(q.source?.match(/^(\d{4})/)?.[1]),
            paper: parseInt(q.source?.match(/Paper (\d+)/)?.[1]),
            qnum: parseInt(q.source?.match(/Q(\d+)/)?.[1]),
        };

        // Set up warmup state
        this.warmupIndex = 0;
        this.warmupCorrect = 0;
        this.warmupAnswered = [];
        this.warmupCount = 5;

        // Build the past_paper context for focused warmup
        const year   = parseInt(q.source?.match(/^(\d{4})/)?.[1]);
        const paper  = parseInt(q.source?.match(/Paper (\d+)/)?.[1]);
        const qnum   = parseInt(q.source?.match(/Q(\d+)/)?.[1]);
        const body = {
            count: 5,
            past_paper: {
                course_id:    q.course_id,
                year,
                paper,
                question_num: qnum,
            },
        };
        if ((q._all_topic_ids || []).length > 0) {
            body.topic_ids = q._all_topic_ids;
        }

        // Switch to warmup view and skip setup screen
        this.showView('warmup');
        document.getElementById('warmup-setup').style.display = 'none';
        document.getElementById('warmup-quiz').style.display = 'block';
        document.getElementById('warmup-results').style.display = 'none';

        const ppLabel = document.getElementById('warmup-pp-label');
        if (ppLabel) {
            ppLabel.textContent = q.source || '';
            ppLabel.style.display = 'block';
        }

        document.getElementById('warmup-card').innerHTML = `
            <div class="loading" style="margin-top: 3rem;"><span class="spinner"></span>Generating warm-up questions for ${this.escapeHtml(q.source || 'this question')}…</div>
        `;

        try {
            const res = await fetch('/api/mcq/generate', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(body),
            });
            const data = await res.json();
            this.warmupMcqs = data.mcqs || [];

            if (this.warmupMcqs.length === 0) {
                document.getElementById('warmup-card').innerHTML =
                    '<div class="empty-state">Couldn\'t generate questions. Please try again.</div>';
                return;
            }

            this.warmupCount = this.warmupMcqs.length;
            this.renderWarmupQuestion();
        } catch (err) {
            document.getElementById('warmup-card').innerHTML =
                '<div class="empty-state">Failed to load questions. Is the server running?</div>';
        }
    },

    async toggleSimilarPastPapers() {
        const container = document.getElementById('similar-pp-container');
        if (!container) return;

        // Toggle off if already showing
        if (container.innerHTML.trim() && !container.innerHTML.includes('loading')) {
            container.innerHTML = '';
            return;
        }

        const q = this.currentQuestion;
        const topicIds = q?._all_topic_ids || [];
        if (topicIds.length === 0) {
            container.innerHTML = '<div class="empty-state" style="margin-top:1rem;">No topic tags for this question.</div>';
            return;
        }

        container.innerHTML = '<div class="loading" style="margin-top:1rem;"><span class="spinner"></span>Finding similar papers…</div>';

        // Ensure pp data is loaded
        if (!this._ppData) {
            try {
                const res = await fetch('/api/pastpapers/all');
                this._ppData = await res.json();
            } catch (e) {
                container.innerHTML = '<div class="empty-state" style="margin-top:1rem;">Failed to load past papers.</div>';
                return;
            }
        }

        // Gather knowledge for sorting by weak topics
        const knowledge = {};
        if (this.dashboardData) {
            for (const term of Object.values(this.dashboardData.terms)) {
                for (const course of Object.values(term.courses)) {
                    for (const t of course.topics) {
                        knowledge[t.id] = t.confidence;
                    }
                }
            }
        }

        const currentSource = q.source || '';
        const matches = [];

        for (const course of (this._ppData.courses || [])) {
            for (const pq of (course.questions || [])) {
                const ref = `${pq.year} Paper ${pq.paper} Q${pq.question}`;
                if (ref === currentSource) continue; // skip self

                const pqTopics = pq.topic_ids || pq.topics || [];
                const overlap = pqTopics.filter(t => topicIds.includes(t));
                if (overlap.length === 0) continue;

                // Score: overlap count + weak-topic bonus
                let score = overlap.length;
                for (const tid of overlap) {
                    const conf = knowledge[tid] ?? 0.5;
                    score += (1 - conf); // weak topics boost the score
                }

                matches.push({ ref, courseId: course.course_id, courseName: course.course_name, year: pq.year, paper: pq.paper, qnum: pq.question, overlap: overlap.length, score, pdfUrl: pq.pdf_url });
            }
        }

        matches.sort((a, b) => b.score - a.score);
        const top = matches.slice(0, 8);

        if (top.length === 0) {
            container.innerHTML = '<div class="empty-state" style="margin-top:1rem;">No similar past paper questions found.</div>';
            return;
        }

        const rows = top.map(m => `
            <div class="pp-row similar-pp-row">
                <div class="pp-row-left">
                    <span class="pp-row-ref">${this.escapeHtml(m.ref)}</span>
                    <span class="pp-row-meta">${this.escapeHtml(m.courseName)} &middot; ${m.overlap} shared topic${m.overlap > 1 ? 's' : ''}</span>
                </div>
                <div class="pp-row-right">
                    ${m.pdfUrl ? `<a class="pp-pdf-link" href="${m.pdfUrl}" target="_blank" rel="noopener">PDF ↗</a>` : ''}
                    ${this._solutionsUrl(m.pdfUrl) ? `<a class="pp-pdf-link pp-sol-link" href="${this._solutionsUrl(m.pdfUrl)}" target="_blank" rel="noopener">Solutions ↗</a>` : ''}
                    ${this._reportUrl(m.year) ? `<a class="pp-pdf-link pp-rep-link" href="${this._reportUrl(m.year)}" target="_blank" rel="noopener">Report ↗</a>` : ''}
                    <button class="btn btn-primary pp-practice-btn"
                        onclick="app.practicePastPaper('${this.escapeAttr(m.courseId)}', ${m.year}, ${m.paper}, ${m.qnum})">
                        Practice →
                    </button>
                </div>
            </div>
        `).join('');

        container.innerHTML = `
            <div class="similar-pp-section">
                <div class="feedback-section-title" style="margin-bottom:0.75rem;">Similar past paper questions</div>
                ${rows}
            </div>
        `;
    },

    async practicePastPaper(courseId, year, paper, qnum) {
        const container = document.getElementById('question-container');
        container.innerHTML = '<div class="loading"><span class="spinner"></span>Loading question…</div>';

        const backBtn = document.getElementById('question-back-btn');
        backBtn.onclick = () => this.showPastPapers();
        this.showView('question');

        try {
            const res = await fetch(`/api/pastpapers/${courseId}`);
            const data = await res.json();
            const q = (data.tagged_questions || []).find(
                x => x.year === year && x.paper === paper && x.question === qnum
            );
            if (!q) {
                container.innerHTML = '<div class="empty-state">Question not found.</div>';
                return;
            }

            // Find course name
            const courseEntry = this._ppData && this._ppData.courses.find(c => c.course_id === courseId);
            const courseName = courseEntry ? courseEntry.course_name : courseId;

            // Normalise part key: pastpapers.json uses 'part', renderQuestion expects 'label'
            const parts = (q.parts || []).map(p => ({
                label: p.label || p.part || '?',
                text: p.text || '',
                marks: p.marks || 0,
                topics: p.topics || [],
            }));

            const primaryTopicId = (q.topics || [])[0] || '';

            // Load any saved progress (difficult_parts) from pp data cache
            const ref = `${year} Paper ${paper} Q${qnum}`;
            const ppEntry = this._ppData?.courses
                ?.find(c => c.course_id === courseId)?.questions
                ?.find(pq => pq.ref === ref);
            const has_diagram = ppEntry?.has_diagram || false;
            const difficultParts = ppEntry?.difficult_parts || [];

            const question = {
                parts,
                total_marks: parts.reduce((s, p) => s + p.marks, 0),
                difficulty: 'high',
                is_actual_past_paper: true,
                source: ref,
                pdf_url: q.pdf_url || null,
                has_diagram,
                topic_id: primaryTopicId,
                course_id: courseId,
                topic_name: `Paper ${paper} Q${qnum}`,
                course_name: courseName,
                difficult: false,
                _all_topic_ids: q.topics || [],
                _difficult_parts: difficultParts,
            };

            this.currentQuestion = question;
            this.sessionTopics = [{ topic_id: primaryTopicId, course_id: courseId }];
            this.sessionIndex = 0;
            this.renderQuestion(question);
        } catch (err) {
            container.innerHTML = '<div class="empty-state">Failed to load question.</div>';
        }
    },


    // ---- Exam Planner ----
    showPlanner() {
        this.showView('planner');
        this._renderPlanner();
    },

    _renderPlanner() {
        // Cambridge Part IB CS 2026 paper structure (Papers 4–7).
        // Each course entry lists its question NUMBERS in that paper, so the
        // user can pick any subset (not all-or-nothing per course).
        const PAPERS = [
            {
                num: 4, total: 8, choose: 5,
                courses: [
                    { id: 'compiler-construction', name: 'Compiler Construction',           qs: [1, 2] },
                    { id: 'semantics',             name: 'Semantics of Programming Languages', qs: [3] },
                    { id: 'prolog',                name: 'Prolog',                          qs: [4] },
                    { id: 'prog-c-cpp',            name: 'Programming in C and C++',        qs: [5, 6] },
                    { id: 'cybersecurity',         name: 'Cybersecurity',                   qs: [7, 8] },
                ],
            },
            {
                num: 5, total: 8, choose: 5,
                courses: [
                    { id: 'computer-networking',    name: 'Computer Networking',                qs: [1, 2, 3] },
                    { id: 'concurrent-distributed', name: 'Concurrent and Distributed Systems', qs: [4, 5] },
                    { id: 'intro-comp-arch',        name: 'Introduction to Computer Architecture', qs: [6, 7, 8] },
                ],
            },
            {
                num: 6, total: 9, choose: 5,
                courses: [
                    { id: 'complexity-theory',  name: 'Complexity Theory',                  qs: [1, 2] },
                    { id: 'computation-theory', name: 'Computation Theory',                 qs: [3, 4] },
                    { id: 'data-science',       name: 'Data Science',                       qs: [5, 6] },
                    { id: 'logic-proof',        name: 'Logic and Proof',                    qs: [7, 8] },
                    { id: 'semantics',          name: 'Semantics of Programming Languages', qs: [9] },
                ],
            },
            {
                num: 7, total: 10, choose: 5,
                courses: [
                    { id: 'artificial-intelligence', name: 'Artificial Intelligence',          qs: [1, 2] },
                    { id: 'econ-law-ethics',         name: 'Economics, Law and Ethics',         qs: [3, 4] },
                    { id: 'formal-models-language',  name: 'Formal Models of Language',         qs: [5, 6] },
                    { id: 'further-graphics',        name: 'Further Graphics',                  qs: [7, 8] },
                    { id: 'further-hci',             name: 'Further Human-Computer Interaction', qs: [9, 10] },
                ],
            },
        ];

        // Build confidence map from dashboard data
        const confMap = {};
        if (this.dashboardData) {
            for (const term of Object.values(this.dashboardData.terms)) {
                for (const [courseId, course] of Object.entries(term.courses)) {
                    confMap[courseId] = course.confidence;
                }
            }
        }

        // Load saved selections.
        // Schema v2: { p4: { "1": true, "2": false, ... } } keyed by question number.
        // If we see legacy shape (course-name booleans) drop it.
        const rawSaved = JSON.parse(localStorage.getItem('plannerSelections') || '{}');
        const isLegacy = Object.values(rawSaved).some(p =>
            p && Object.keys(p).some(k => isNaN(parseInt(k))));
        const saved = isLegacy ? {} : rawSaved;
        if (isLegacy) localStorage.setItem('plannerSelections', '{}');

        const countSelected = (paper) => {
            const sel = saved[`p${paper.num}`] || {};
            return paper.courses.reduce(
                (acc, c) => acc + c.qs.filter(qn => sel[qn]).length, 0);
        };

        const updateCountUI = (paper) => {
            const n = countSelected(paper);
            const el = document.getElementById(`planner-count-${paper.num}`);
            const cls = n > paper.choose ? 'over' : n === paper.choose ? 'exact' : 'under';
            el.textContent = `${n} / ${paper.choose} selected`;
            el.className = `planner-count ${cls}`;
        };

        const container = document.getElementById('planner-papers');
        container.innerHTML = '';

        PAPERS.forEach(paper => {
            const paperEl = document.createElement('div');
            paperEl.className = 'planner-paper';
            const selectedNow = countSelected(paper);
            const overClass = selectedNow > paper.choose ? 'over'
                : selectedNow === paper.choose ? 'exact' : 'under';

            paperEl.innerHTML = `
                <div class="planner-paper-header">
                    <div>
                        <span class="planner-paper-title">Paper ${paper.num}</span>
                        <span class="planner-paper-meta">${paper.total} questions, answer ${paper.choose}</span>
                    </div>
                    <div class="planner-count ${overClass}" id="planner-count-${paper.num}">
                        ${selectedNow} / ${paper.choose} selected
                    </div>
                </div>
                <div class="planner-courses" id="planner-courses-${paper.num}"></div>
            `;
            container.appendChild(paperEl);

            const coursesEl = paperEl.querySelector(`#planner-courses-${paper.num}`);
            paper.courses.forEach(course => {
                const conf = course.id ? (confMap[course.id] ?? null) : null;
                const confPct = conf !== null ? Math.round(conf * 100) : null;
                const confColor = conf === null ? '#888'
                    : conf >= 0.7 ? '#5a8a5a'
                    : conf >= 0.4 ? '#a07a30'
                    : '#8a4a4a';

                const sel = saved[`p${paper.num}`] || {};
                const pillsHtml = course.qs.map(qn => {
                    const on = !!sel[qn];
                    return `<button type="button" class="planner-q-pill${on ? ' selected' : ''}"
                        data-paper="${paper.num}" data-qn="${qn}">Q${qn}</button>`;
                }).join('');

                const row = document.createElement('div');
                row.className = 'planner-course-row';
                row.innerHTML = `
                    <span class="planner-course-name">${this.escapeHtml(course.name)}</span>
                    <span class="planner-q-pills">${pillsHtml}</span>
                    ${confPct !== null ? `
                        <span class="planner-conf-bar">
                            <span class="planner-conf-fill" style="width:${confPct}%;background:${confColor}"></span>
                        </span>
                        <span class="planner-conf-pct" style="color:${confColor}">${confPct}%</span>
                    ` : '<span class="planner-conf-na">no data</span>'}
                `;
                coursesEl.appendChild(row);

                row.querySelectorAll('.planner-q-pill').forEach(pill => {
                    pill.addEventListener('click', () => {
                        const paperNum = +pill.dataset.paper;
                        const qn = +pill.dataset.qn;
                        const all = JSON.parse(localStorage.getItem('plannerSelections') || '{}');
                        const pKey = `p${paperNum}`;
                        if (!all[pKey]) all[pKey] = {};
                        const next = !all[pKey][qn];
                        all[pKey][qn] = next;
                        localStorage.setItem('plannerSelections', JSON.stringify(all));
                        // mirror into in-memory `saved` so re-renders read fresh
                        saved[pKey] = all[pKey];
                        pill.classList.toggle('selected', next);
                        updateCountUI(PAPERS.find(p => p.num === paperNum));
                    });
                });
            });
        });
    },

    async showAnkiBank() {
        this.showView('anki');
        await this.loadAnkiBank();
    },

    async loadAnkiBank() {
        const container = document.getElementById('anki-bank-list');
        container.innerHTML = '<div class="loading"><span class="spinner"></span>Loading…</div>';
        try {
            const res = await fetch('/api/anki/bank');
            const data = await res.json();
            this.renderAnkiBank(data);
            // Update nav badge
            const navBtn = document.getElementById('nav-anki');
            if (navBtn) {
                navBtn.textContent = data.total > 0 ? `Anki Bank (${data.total})` : 'Anki Bank';
            }
            const exportAll = document.getElementById('anki-export-all-btn');
            if (exportAll) exportAll.disabled = data.total === 0;
        } catch (e) {
            container.innerHTML = '<div class="empty-state">Failed to load bank.</div>';
        }
    },

    renderAnkiBank(data) {
        const container = document.getElementById('anki-bank-list');
        if (data.total === 0) {
            container.innerHTML = '<div class="empty-state">No flashcards yet. Answer questions incorrectly and cards will appear here automatically.</div>';
            return;
        }
        container.innerHTML = data.topics.map(topic => `
            <div class="anki-topic-section">
                <div class="anki-topic-header">
                    <div>
                        <span class="anki-topic-name">${this.escapeHtml(topic.topic_name)}</span>
                        <span class="anki-topic-course">${this.escapeHtml(topic.course_name)}</span>
                    </div>
                    <div class="anki-topic-actions">
                        <span class="anki-card-count">${topic.cards.length} card${topic.cards.length !== 1 ? 's' : ''}</span>
                        <button class="btn btn-primary anki-export-btn" onclick="app.exportAnki('${this.escapeAttr(topic.topic_id)}')">Export topic</button>
                    </div>
                </div>
                <div class="anki-cards">
                    ${topic.cards.map(card => `
                        <div class="anki-card" id="anki-card-${this.escapeAttr(card.id)}">
                            <div class="anki-card-front">${this.escapeHtml(card.front)}</div>
                            <div class="anki-card-back">${this.escapeHtml(card.back)}</div>
                            <button class="anki-delete-btn" onclick="app.deleteAnkiCard('${this.escapeAttr(card.id)}')" title="Remove card">✕</button>
                        </div>
                    `).join('')}
                </div>
            </div>
        `).join('');
    },

    async exportAnki(topicId) {
        const btn = topicId
            ? document.querySelector(`button[onclick="app.exportAnki('${this.escapeAttr(topicId)}')"]`)
            : document.getElementById('anki-export-all-btn');
        if (btn) { btn.disabled = true; btn.textContent = 'Exporting…'; }

        try {
            const res = await fetch('/api/anki/export', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ topic_id: topicId || null }),
            });
            if (!res.ok) {
                const err = await res.json();
                alert(err.error || 'Export failed');
                if (btn) { btn.disabled = false; btn.textContent = topicId ? 'Export topic' : 'Export all as .apkg'; }
                return;
            }
            // Trigger download
            const blob = await res.blob();
            const url = URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = res.headers.get('Content-Disposition')?.match(/filename="?([^"]+)"?/)?.[1] || 'cards.apkg';
            a.click();
            URL.revokeObjectURL(url);
            // Reload bank (cards were cleared server-side)
            await this.loadAnkiBank();
        } catch (e) {
            alert('Export failed. Is the server running?');
            if (btn) { btn.disabled = false; btn.textContent = topicId ? 'Export topic' : 'Export all as .apkg'; }
        }
    },

    async deleteAnkiCard(cardId) {
        const el = document.getElementById(`anki-card-${cardId}`);
        if (el) el.style.opacity = '0.4';
        await fetch('/api/anki/delete', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ ids: [cardId] }),
        });
        await this.loadAnkiBank();
    },

    toggleDiagramEmbed(btn, pdfUrl) {
        const embedContainer = btn.nextElementSibling;
        if (!embedContainer) return;
        if (embedContainer.style.display === 'none') {
            embedContainer.style.display = 'block';
            if (!embedContainer.innerHTML) {
                embedContainer.innerHTML = `<iframe src="${pdfUrl}" class="diagram-embed-frame" title="Past paper question PDF"></iframe>`;
            }
            btn.textContent = 'Hide PDF';
        } else {
            embedContainer.style.display = 'none';
            btn.textContent = btn.closest('.diagram-notice')?.classList.contains('diagram-notice--subtle') ? 'View original PDF' : 'Show PDF';
        }
    },

    async togglePartDifficult(partIndex) {
        const q = this.currentQuestion;
        if (!q?.is_actual_past_paper || !q.source) return;
        const part = q.parts[partIndex];
        if (!part) return;

        const btn = document.getElementById(`part-flag-btn-${partIndex}`);
        const res = await fetch('/api/pp/progress', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ ref: q.source, toggle_difficult_part: part.label }),
        });
        const data = await res.json();
        const difficultParts = new Set(data.difficult_parts || []);
        const isFlagged = difficultParts.has(part.label);
        q._difficult_parts = data.difficult_parts || [];
        if (btn) {
            btn.classList.toggle('flagged', isFlagged);
            btn.textContent = isFlagged ? '⚑ Difficult' : '⚐';
            btn.title = isFlagged ? 'Remove difficult flag' : 'Flag this part as difficult';
        }
        // Invalidate pp cache so browser shows updated flags
        this._ppData = null;
    },

    escapeHtml(str) {
        if (!str) return '';
        const div = document.createElement('div');
        div.textContent = str;
        return div.innerHTML;
    },

    // Renders text with KaTeX math ($...$, $$...$$) and **bold** markdown
    renderContent(text) {
        if (!text) return '';
        const blocks = [];
        // Use unique token unlikely to appear in content
        const mkPH = i => `\x00\x00MATH${i}\x00\x00`;
        const rePH = /\x00\x00MATH(\d+)\x00\x00/g;
        let s = text
            // Display math: $$...$$ or \[...\]
            .replace(/\$\$([\s\S]+?)\$\$|\\\[([\s\S]+?)\\\]/g, (m, g1, g2) => {
                const expr = (g1 || g2).trim();
                const idx = blocks.length;
                if (typeof katex !== 'undefined') {
                    try { blocks.push(katex.renderToString(expr, { displayMode: true, throwOnError: false })); }
                    catch(e) { blocks.push(`<code>${this.escapeHtml(m)}</code>`); }
                } else { blocks.push(`<code>${this.escapeHtml(m)}</code>`); }
                return mkPH(idx);
            })
            // Inline math: $...$ (only if content has a math char — avoids matching $5 / prices)
            // or \(...\)
            .replace(/\$(?=[^$\n]*[\\^_{}\|])([^$\n]+?)\$|\\\((.+?)\\\)/g, (m, g1, g2) => {
                const expr = (g1 || g2).trim();
                const idx = blocks.length;
                if (typeof katex !== 'undefined') {
                    try { blocks.push(katex.renderToString(expr, { displayMode: false, throwOnError: false })); }
                    catch(e) { blocks.push(`<code>${this.escapeHtml(m)}</code>`); }
                } else { blocks.push(`<code>${this.escapeHtml(m)}</code>`); }
                return mkPH(idx);
            });
        // Escape HTML using regex (avoids DOM node that might strip null bytes)
        s = s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
        // Apply bold markdown, then restore math blocks
        s = s.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
             .replace(rePH, (_, i) => blocks[+i]);
        return s;
    },

    escapeAttr(str) {
        if (!str) return '';
        return str.replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/'/g, '&#39;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
    },

    // ---- Math symbol pad ----
    MATH_GROUPS: {
        greek: { label: 'Greek', symbols: [
            'α','β','γ','δ','ε','ζ','η','θ','ι','κ','λ','μ','ν','ξ','π','ρ','σ','τ','υ','φ','χ','ψ','ω',
            'Γ','Δ','Θ','Λ','Ξ','Π','Σ','Φ','Ψ','Ω'
        ]},
        logic: { label: 'Logic', symbols: [
            '∧','∨','¬','⇒','⇐','⇔','∀','∃','∄','⊢','⊨','⊤','⊥','≡','∴','∵'
        ]},
        sets: { label: 'Sets', symbols: [
            '∈','∉','⊆','⊂','⊇','⊃','∪','∩','∅','∖','∁','×','ℕ','ℤ','ℚ','ℝ','ℂ','℘','|'
        ]},
        rel: { label: 'Relations', symbols: [
            '≤','≥','≠','≈','≡','≅','≜','≺','≻','⪯','⪰','⊑','⊒','≪','≫','∝'
        ]},
        arrows: { label: 'Arrows', symbols: [
            '→','←','↔','⇒','⇐','⇔','↦','⟶','⟵','⟼','↑','↓','⤳','⇝','↪'
        ]},
        ops: { label: 'Operators', symbols: [
            '±','∓','×','÷','·','∘','⊕','⊗','⊙','⊞','⊠','√','∞','⌊','⌋','⌈','⌉',
            { btn: 'x²', ins: '$x^{}$', cursor: 4 },
            { btn: 'xₙ', ins: '$x_{}$', cursor: 4 },
            { btn: 'a⁄b', ins: '$\\frac{}{}$', cursor: 7 },
            { btn: '√x', ins: '$\\sqrt{}$', cursor: 7 }
        ]},
        calc: { label: 'Calculus', symbols: [
            '∑','∏','∫','∮','∂','∇','Δ','→',
            { btn: '∑_{i=0}^{n}', ins: '$\\sum_{i=0}^{n} $', cursor: 17 },
            { btn: '∏_{i=1}^{n}', ins: '$\\prod_{i=1}^{n} $', cursor: 18 },
            { btn: '∫_a^b', ins: '$\\int_{a}^{b} \\, dx$', cursor: 14 },
            { btn: 'lim', ins: '$\\lim_{x \\to \\infty} $', cursor: 22 },
            { btn: '∂/∂x', ins: '$\\frac{\\partial }{\\partial x}$', cursor: 16 }
        ]},
        sem: { label: 'Semantics', symbols: [
            '⇓','⇑','⟶','↦','⇒','⊢','≡','⊑','⟨','⟩','⟦','⟧','σ','ρ','Γ','∅',
            { btn: '⟨e,σ⟩', ins: '⟨, σ⟩', cursor: 1 },
            { btn: '⟦e⟧', ins: '⟦⟧', cursor: 1 },
            { btn: 'λx.', ins: 'λ.', cursor: 1 },
            { btn: 'e[v/x]', ins: '[v/x]', cursor: 0 },
            { btn: 'Γ ⊢ e:τ', ins: 'Γ ⊢ : τ', cursor: 4 }
        ]},
    },

    _attachMathPad(textarea) {
        if (textarea.dataset.mathPad) return;
        textarea.dataset.mathPad = '1';
        const q = this.currentQuestion || {};
        const courseStr = `${q.course_id || ''} ${q.course_name || ''}`.toLowerCase();
        const isSem = courseStr.includes('semantics');
        const questionText = [q.question, q.topic_name, ...((q.parts || []).map(p => p.text))].filter(Boolean).join(' ');
        const relevant = this._extractRelevantSymbols(questionText);

        const wrap = document.createElement('div');
        wrap.className = 'math-pad';
        const preview = document.createElement('div');
        preview.className = 'math-preview';
        preview.style.display = 'none';

        const groups = Object.entries(this.MATH_GROUPS).filter(([k]) => isSem || k !== 'sem');
        const tabsHtml = groups.map(([k, g], i) => `<button type="button" class="math-pad-tab${i === 0 ? ' active' : ''}" data-tab="${k}">${g.label}</button>`).join('');
        const relevantHtml = relevant.length
            ? `<div class="math-pad-relevant"><span class="math-pad-label">Relevant:</span>${relevant.map(s => this._symButtonHtml(s)).join('')}</div>`
            : '';
        const semToolsHtml = isSem
            ? `<div class="math-pad-semtools">
                <button type="button" class="btn btn-ghost math-pad-tool-btn" data-action="rule">▤ Insert rule</button>
                <button type="button" class="btn btn-ghost math-pad-tool-btn" data-action="induction">⊢ Induction scaffold</button>
              </div>`
            : '';

        wrap.innerHTML = `
            <button type="button" class="math-pad-toggle" aria-expanded="false">∑ Maths ▾</button>
            <div class="math-pad-body" style="display:none">
                ${relevantHtml}
                ${semToolsHtml}
                <div class="math-pad-tabs">${tabsHtml}</div>
                <div class="math-pad-grid"></div>
            </div>`;

        textarea.insertAdjacentElement('afterend', wrap);
        wrap.insertAdjacentElement('afterend', preview);

        const grid = wrap.querySelector('.math-pad-grid');
        const renderTab = (key) => {
            const g = this.MATH_GROUPS[key];
            if (!g) return;
            grid.innerHTML = g.symbols.map(s => this._symButtonHtml(s)).join('');
        };
        renderTab(groups[0][0]);

        // Prevent focus loss on any mouse interaction inside the pad
        wrap.addEventListener('mousedown', (e) => {
            if (e.target.closest('.math-sym, .math-pad-tool-btn, .math-pad-toggle, .math-pad-tab')) {
                e.preventDefault();
            }
        });

        wrap.addEventListener('click', (e) => {
            const toggle = e.target.closest('.math-pad-toggle');
            if (toggle) {
                const body = wrap.querySelector('.math-pad-body');
                const open = body.style.display === 'none';
                body.style.display = open ? '' : 'none';
                toggle.setAttribute('aria-expanded', open ? 'true' : 'false');
                toggle.textContent = open ? '∑ Maths ▴' : '∑ Maths ▾';
                return;
            }
            const tab = e.target.closest('.math-pad-tab');
            if (tab) {
                wrap.querySelectorAll('.math-pad-tab').forEach(t => t.classList.remove('active'));
                tab.classList.add('active');
                renderTab(tab.dataset.tab);
                return;
            }
            const sym = e.target.closest('.math-sym');
            if (sym) {
                const cursor = sym.dataset.cursor != null ? parseInt(sym.dataset.cursor, 10) : null;
                this._insertAtTextarea(textarea, sym.dataset.ins, cursor);
                return;
            }
            const tool = e.target.closest('.math-pad-tool-btn');
            if (tool) {
                if (tool.dataset.action === 'rule') this._openRuleBuilder(textarea);
                else if (tool.dataset.action === 'induction') this._openInductionScaffold(textarea);
            }
        });

        const updatePreview = () => {
            const v = textarea.value;
            const hasMath = /[\$\\]|[α-ωΑ-Ω∀∃∈∉∑∏∫∂∇∅⇒⇔⇓⟶↦⊢⊨≤≥≠≈≡⊑⟨⟩⟦⟧]/.test(v);
            if (hasMath && v.trim()) {
                preview.style.display = '';
                preview.innerHTML = `<div class="math-preview-label">Preview</div><div class="math-preview-body">${this.renderContent(v)}</div>`;
            } else {
                preview.style.display = 'none';
                preview.innerHTML = '';
            }
        };
        textarea.addEventListener('input', updatePreview);
        updatePreview();
    },

    _symButtonHtml(s) {
        if (typeof s === 'string') {
            const esc = this.escapeAttr(s);
            return `<button type="button" class="math-sym" data-ins="${esc}" title="${esc}">${this.escapeHtml(s)}</button>`;
        }
        const cursorAttr = s.cursor != null ? ` data-cursor="${s.cursor}"` : '';
        return `<button type="button" class="math-sym math-sym-wide" data-ins="${this.escapeAttr(s.ins)}"${cursorAttr} title="${this.escapeAttr(s.ins)}">${this.escapeHtml(s.btn)}</button>`;
    },

    _extractRelevantSymbols(text) {
        if (!text) return [];
        const uni = /[α-ωΑ-Ω∀∃∈∉∑∏∫∂∇∅⇒⇔⇓⇑⟶⟵↦⊢⊨≤≥≠≈≡⊑⊒⟨⟩⟦⟧∧∨¬→←↔·∘±×÷√∞ℕℤℚℝℂλμφψ]/g;
        const found = new Set();
        let m;
        while ((m = uni.exec(text)) !== null) found.add(m[0]);
        return Array.from(found).slice(0, 12);
    },

    _insertAtTextarea(ta, text, cursorOffset = null) {
        ta.focus();
        const start = ta.selectionStart;
        const end = ta.selectionEnd;
        const before = ta.value.substring(0, start);
        const after = ta.value.substring(end);
        ta.value = before + text + after;
        const pos = cursorOffset != null ? start + cursorOffset : start + text.length;
        ta.selectionStart = ta.selectionEnd = pos;
        ta.dispatchEvent(new Event('input', { bubbles: true }));
    },

    _openRuleBuilder(textarea) {
        const overlay = document.createElement('div');
        overlay.className = 'math-modal-overlay';
        overlay.innerHTML = `
            <div class="math-modal">
                <div class="math-modal-header">
                    <h3>Insert inference rule</h3>
                    <button type="button" class="math-modal-close">×</button>
                </div>
                <div class="math-modal-body">
                    <label>Rule name</label>
                    <input type="text" class="math-modal-input" id="rb-name" placeholder="e.g. Plus, If-True, App">
                    <label>Premises (one per line)</label>
                    <textarea class="math-modal-textarea" id="rb-prem" rows="4" placeholder="e₁ ⇓ v₁&#10;e₂ ⇓ v₂"></textarea>
                    <label>Conclusion</label>
                    <input type="text" class="math-modal-input" id="rb-conc" placeholder="e₁ + e₂ ⇓ v₁ + v₂">
                </div>
                <div class="math-modal-footer">
                    <button type="button" class="btn btn-ghost math-modal-cancel">Cancel</button>
                    <button type="button" class="btn btn-primary math-modal-ok">Insert</button>
                </div>
            </div>`;
        document.body.appendChild(overlay);
        const close = () => overlay.remove();
        overlay.querySelector('.math-modal-close').onclick = close;
        overlay.querySelector('.math-modal-cancel').onclick = close;
        overlay.addEventListener('click', (e) => { if (e.target === overlay) close(); });
        overlay.querySelector('#rb-name').focus();
        overlay.querySelector('.math-modal-ok').onclick = () => {
            const name = overlay.querySelector('#rb-name').value.trim();
            const prem = overlay.querySelector('#rb-prem').value.split('\n').map(s => s.trim()).filter(Boolean);
            const conc = overlay.querySelector('#rb-conc').value.trim();
            if (!conc) { overlay.querySelector('#rb-conc').focus(); return; }
            const premLatex = prem.length ? prem.join(' \\quad ') : '\\;';
            const rule = `$$\\dfrac{${premLatex}}{${conc}}${name ? ' \\quad \\text{(' + name + ')}' : ''}$$`;
            this._insertAtTextarea(textarea, '\n' + rule + '\n');
            close();
        };
    },

    _openInductionScaffold(textarea) {
        const overlay = document.createElement('div');
        overlay.className = 'math-modal-overlay';
        overlay.innerHTML = `
            <div class="math-modal">
                <div class="math-modal-header">
                    <h3>Rule induction scaffold</h3>
                    <button type="button" class="math-modal-close">×</button>
                </div>
                <div class="math-modal-body">
                    <label>Property to prove <span class="math-modal-hint">(e.g. ∀e,v. e ⇓ v ⇒ P(e,v))</span></label>
                    <input type="text" class="math-modal-input" id="is-prop" placeholder="P(e, v)">
                    <label>Judgement being inducted on</label>
                    <input type="text" class="math-modal-input" id="is-judge" placeholder="e ⇓ v">
                    <label>Rule names (one per line)</label>
                    <textarea class="math-modal-textarea" id="is-rules" rows="5" placeholder="Num&#10;Plus&#10;If-True&#10;If-False"></textarea>
                </div>
                <div class="math-modal-footer">
                    <button type="button" class="btn btn-ghost math-modal-cancel">Cancel</button>
                    <button type="button" class="btn btn-primary math-modal-ok">Insert scaffold</button>
                </div>
            </div>`;
        document.body.appendChild(overlay);
        const close = () => overlay.remove();
        overlay.querySelector('.math-modal-close').onclick = close;
        overlay.querySelector('.math-modal-cancel').onclick = close;
        overlay.addEventListener('click', (e) => { if (e.target === overlay) close(); });
        overlay.querySelector('#is-prop').focus();
        overlay.querySelector('.math-modal-ok').onclick = () => {
            const prop = overlay.querySelector('#is-prop').value.trim() || 'P(…)';
            const judge = overlay.querySelector('#is-judge').value.trim() || 'e ⇓ v';
            const rules = overlay.querySelector('#is-rules').value.split('\n').map(s => s.trim()).filter(Boolean);
            const cases = rules.length ? rules : ['Rule1', 'Rule2'];
            const scaffold = [
                `Claim: ${prop}.`,
                `Proof by rule induction on ${judge}.`,
                '',
                ...cases.map(r => `Case (${r}):\n  Assumptions: …\n  Inductive hypothesis: …\n  Goal: show ${prop}.\n  Argument: …\n`),
                '∎'
            ].join('\n');
            this._insertAtTextarea(textarea, '\n' + scaffold + '\n');
            close();
        };
    },

    // ---- Image paste in answer textareas ----
    _answerImages: {},
    MAX_IMAGES_PER_ANSWER: 4,
    MAX_IMAGE_DIMENSION: 1280,  // Resize larger pastes; tripos diagrams rarely need more.

    _imageStoreKey(textarea) {
        return textarea.id || textarea.dataset.label || 'default';
    },

    _attachImagePaste(textarea) {
        // Ensure a preview container right after the textarea (or after the part-btn-row).
        let preview = textarea.parentElement.querySelector(':scope > .answer-image-preview');
        if (!preview) {
            preview = document.createElement('div');
            preview.className = 'answer-image-preview';
            // Insert directly after the textarea so it sits visually below the text input.
            textarea.parentElement.insertBefore(preview, textarea.nextSibling);
            // Caption hint (paste instruction)
            const hint = document.createElement('div');
            hint.className = 'answer-image-hint';
            hint.textContent = '⌘/Ctrl + V to paste a diagram or screenshot';
            preview.appendChild(hint);
        }

        textarea.addEventListener('paste', (e) => {
            const items = (e.clipboardData || window.clipboardData)?.items;
            if (!items) return;
            const imageItems = Array.from(items).filter(it => it.kind === 'file' && it.type.startsWith('image/'));
            if (imageItems.length === 0) return;
            e.preventDefault();
            for (const it of imageItems) {
                const file = it.getAsFile();
                if (file) this._addAnswerImage(textarea, file);
            }
        });
    },

    async _addAnswerImage(textarea, file) {
        const key = this._imageStoreKey(textarea);
        if (!this._answerImages) this._answerImages = {};
        if (!this._answerImages[key]) this._answerImages[key] = [];
        if (this._answerImages[key].length >= this.MAX_IMAGES_PER_ANSWER) {
            this._showAnswerImageError(textarea, `Max ${this.MAX_IMAGES_PER_ANSWER} images per answer.`);
            return;
        }
        try {
            const { dataUrl, base64, mediaType } = await this._processPastedImage(file);
            const id = 'img_' + Date.now() + '_' + Math.random().toString(36).slice(2, 7);
            this._answerImages[key].push({ id, data: base64, media_type: mediaType, dataUrl });
            this._renderAnswerImageThumbs(textarea);
        } catch (err) {
            this._showAnswerImageError(textarea, 'Could not read pasted image.');
        }
    },

    _processPastedImage(file) {
        // Returns { dataUrl, base64, mediaType }. Resizes if larger than MAX_IMAGE_DIMENSION.
        return new Promise((resolve, reject) => {
            const reader = new FileReader();
            reader.onerror = () => reject(reader.error);
            reader.onload = () => {
                const dataUrl = reader.result;
                const img = new Image();
                img.onload = () => {
                    const max = this.MAX_IMAGE_DIMENSION;
                    let { width, height } = img;
                    if (width > max || height > max) {
                        const scale = Math.min(max / width, max / height);
                        width = Math.round(width * scale);
                        height = Math.round(height * scale);
                        const canvas = document.createElement('canvas');
                        canvas.width = width; canvas.height = height;
                        const ctx = canvas.getContext('2d');
                        ctx.drawImage(img, 0, 0, width, height);
                        const resized = canvas.toDataURL('image/png');
                        resolve(this._splitDataUrl(resized));
                    } else {
                        resolve(this._splitDataUrl(dataUrl));
                    }
                };
                img.onerror = () => reject(new Error('image decode failed'));
                img.src = dataUrl;
            };
            reader.readAsDataURL(file);
        });
    },

    _splitDataUrl(dataUrl) {
        const m = dataUrl.match(/^data:([^;]+);base64,(.+)$/);
        if (!m) throw new Error('bad data url');
        return { dataUrl, mediaType: m[1], base64: m[2] };
    },

    _renderAnswerImageThumbs(textarea) {
        const key = this._imageStoreKey(textarea);
        const imgs = this._answerImages?.[key] || [];
        const preview = textarea.parentElement.querySelector(':scope > .answer-image-preview');
        if (!preview) return;
        // Clear thumbs but keep the hint
        const hint = preview.querySelector('.answer-image-hint');
        preview.innerHTML = '';
        if (imgs.length === 0) {
            if (hint) preview.appendChild(hint);
            return;
        }
        const thumbsRow = document.createElement('div');
        thumbsRow.className = 'answer-image-thumbs';
        for (const im of imgs) {
            const t = document.createElement('div');
            t.className = 'answer-image-thumb';
            t.innerHTML = `
                <img src="${im.dataUrl}" alt="pasted image">
                <button type="button" class="answer-image-remove" data-img-id="${im.id}" aria-label="Remove image">×</button>
            `;
            t.querySelector('.answer-image-remove').addEventListener('click', () => {
                this._answerImages[key] = imgs.filter(x => x.id !== im.id);
                this._renderAnswerImageThumbs(textarea);
            });
            thumbsRow.appendChild(t);
        }
        preview.appendChild(thumbsRow);
        const hint2 = document.createElement('div');
        hint2.className = 'answer-image-hint';
        hint2.textContent = `${imgs.length} image${imgs.length > 1 ? 's' : ''} attached · paste more or click × to remove`;
        preview.appendChild(hint2);
    },

    _showAnswerImageError(textarea, message) {
        const preview = textarea.parentElement.querySelector(':scope > .answer-image-preview');
        if (!preview) return;
        const err = document.createElement('div');
        err.className = 'answer-image-error';
        err.textContent = message;
        preview.appendChild(err);
        setTimeout(() => err.remove(), 2500);
    },

    // ---- Chatbot ----
    _chatHistory: [],
    _chatBusy: false,

    toggleChat() {
        const panel = document.getElementById('chat-panel');
        const backdrop = document.getElementById('chat-backdrop');
        const fab = document.getElementById('chat-fab');
        if (!panel) return;
        const open = panel.classList.toggle('open');
        if (backdrop) backdrop.classList.toggle('show', open);
        panel.setAttribute('aria-hidden', open ? 'false' : 'true');
        if (fab) fab.classList.toggle('hidden', open);
        if (open) {
            // Auto-grow input + autofocus
            const ta = document.getElementById('chat-input');
            if (ta) {
                ta.focus();
                ta.oninput = () => {
                    ta.style.height = 'auto';
                    ta.style.height = Math.min(140, ta.scrollHeight) + 'px';
                };
                ta.onkeydown = (e) => {
                    if (e.key === 'Enter' && !e.shiftKey) {
                        e.preventDefault();
                        this.sendChat(e);
                    }
                };
            }
        }
    },

    onChatScopeChange() {
        // No-op for now; scope is read at send time. Could prepend a system note if desired.
    },

    clearChat() {
        this._chatHistory = [];
        const wrap = document.getElementById('chat-messages');
        if (wrap) {
            wrap.innerHTML = `
                <div class="chat-empty">
                    <p class="chat-empty-title">Conversation cleared</p>
                    <p class="chat-empty-desc">Ask anything about your courses below.</p>
                </div>`;
        }
    },

    askChatSuggestion(text) {
        const ta = document.getElementById('chat-input');
        if (ta) ta.value = text;
        this.sendChat();
    },

    async sendChat(event) {
        if (event && event.preventDefault) event.preventDefault();
        if (this._chatBusy) return;
        const ta = document.getElementById('chat-input');
        const message = (ta?.value || '').trim();
        if (!message) return;

        const courseSel = document.getElementById('chat-course-select');
        const courseId = courseSel?.value || '';

        this._appendChatMessage('user', message);
        this._chatHistory.push({ role: 'user', content: message });
        ta.value = '';
        ta.style.height = 'auto';

        const thinkingEl = this._appendChatMessage('assistant', '…', true);
        this._chatBusy = true;
        this._setChatSendDisabled(true);

        try {
            const res = await fetch('/api/chat', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    message,
                    course_id: courseId || null,
                    history: this._chatHistory.slice(0, -1),
                }),
            });
            const data = await res.json();
            if (!res.ok || !data.reply) {
                thinkingEl.querySelector('.chat-bubble').innerHTML =
                    `<em class="chat-error">Sorry — ${this.escapeHtml(data.error || 'something went wrong')}.</em>`;
            } else {
                thinkingEl.querySelector('.chat-bubble').innerHTML = this._renderChatMarkdown(data.reply);
                this._chatHistory.push({ role: 'assistant', content: data.reply });
            }
        } catch (err) {
            thinkingEl.querySelector('.chat-bubble').innerHTML =
                `<em class="chat-error">Network error. Try again.</em>`;
        } finally {
            this._chatBusy = false;
            this._setChatSendDisabled(false);
            const wrap = document.getElementById('chat-messages');
            if (wrap) wrap.scrollTop = wrap.scrollHeight;
            ta?.focus();
        }
    },

    _setChatSendDisabled(disabled) {
        const btn = document.getElementById('chat-send-btn');
        if (btn) btn.disabled = disabled;
    },

    _appendChatMessage(role, text, isPending) {
        const wrap = document.getElementById('chat-messages');
        if (!wrap) return null;
        // Remove empty-state on first message
        const empty = wrap.querySelector('.chat-empty');
        if (empty) empty.remove();

        const row = document.createElement('div');
        row.className = `chat-msg chat-msg--${role}`;
        const bubbleHtml = isPending
            ? '<span class="chat-thinking"><span></span><span></span><span></span></span>'
            : (role === 'user' ? this.escapeHtml(text) : this._renderChatMarkdown(text));
        row.innerHTML = `<div class="chat-bubble">${bubbleHtml}</div>`;
        wrap.appendChild(row);
        wrap.scrollTop = wrap.scrollHeight;
        return row;
    },

    // Lightweight markdown for chat: headings, paragraphs, bullet lists, tables,
    // **bold**, *italic*, `inline code`, and KaTeX math via renderContent.
    _renderChatMarkdown(text) {
        if (!text) return '';
        const lines = text.split(/\n/);
        const out = [];
        let listOpen = false;
        let para = [];
        let tableBuf = [];
        const flushPara = () => {
            if (para.length) {
                out.push('<p>' + para.join(' ') + '</p>');
                para = [];
            }
        };
        const closeList = () => {
            if (listOpen) { out.push('</ul>'); listOpen = false; }
        };
        const isTableRow = (l) => l.startsWith('|') && l.endsWith('|') && l.length > 2;
        const isTableSep = (l) => /^\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)+\|?$/.test(l);
        const splitRow = (l) => l.replace(/^\|/, '').replace(/\|$/, '').split('|').map(c => c.trim());
        const flushTable = () => {
            if (!tableBuf.length) return;
            // Table needs at least header + separator + 1 data row
            const sepIdx = tableBuf.findIndex(isTableSep);
            if (sepIdx < 1 || sepIdx >= tableBuf.length) {
                // Not a real table — fall back to paragraph
                tableBuf.forEach(l => para.push(l));
                tableBuf = [];
                return;
            }
            const headers = splitRow(tableBuf[0]);
            const bodyRows = tableBuf.slice(sepIdx + 1).map(splitRow);
            const th = '<tr>' + headers.map(h => `<th>${h}</th>`).join('') + '</tr>';
            const tb = bodyRows.map(r => '<tr>' + r.map(c => `<td>${c}</td>`).join('') + '</tr>').join('');
            out.push(`<table class="chat-table"><thead>${th}</thead><tbody>${tb}</tbody></table>`);
            tableBuf = [];
        };
        for (const raw of lines) {
            const line = raw.trim();
            // Tables: collect consecutive |...| rows
            if (isTableRow(line) || (tableBuf.length && isTableSep(line))) {
                flushPara(); closeList();
                tableBuf.push(line);
                continue;
            } else if (tableBuf.length) {
                flushTable();
            }
            if (!line) { flushPara(); closeList(); continue; }
            const heading = line.match(/^(#{1,4})\s+(.*)$/);
            if (heading) {
                flushPara(); closeList();
                const level = Math.min(6, heading[1].length + 2);  // ## → h4, ### → h5
                out.push(`<h${level}>${heading[2]}</h${level}>`);
                continue;
            }
            const bullet = line.match(/^[-•*]\s+(.*)$/);
            if (bullet) {
                flushPara();
                if (!listOpen) { out.push('<ul>'); listOpen = true; }
                out.push('<li>' + bullet[1] + '</li>');
            } else {
                closeList();
                para.push(line);
            }
        }
        flushTable(); flushPara(); closeList();
        let html = out.join('');
        // Run inline transforms (math + bold + italic + code) on text-bearing tags.
        html = html.replace(/<(p|li|h[1-6]|th|td)>([\s\S]*?)<\/\1>/g, (_, tag, inner) => {
            return `<${tag}>${this._renderChatInline(inner)}</${tag}>`;
        });
        return html;
    },

    _renderChatInline(text) {
        // Reuse renderContent for math+bold; then add italic + inline code on top.
        let html = this.renderContent(text);
        // Italic: *text* (not part of ** because renderContent already consumed those)
        html = html.replace(/(^|[^*])\*([^*\n]+)\*(?!\*)/g, '$1<em>$2</em>');
        // Inline code: `code`
        html = html.replace(/`([^`\n]+)`/g, '<code>$1</code>');
        return html;
    },
};

// Boot
document.addEventListener('DOMContentLoaded', () => app.init());
