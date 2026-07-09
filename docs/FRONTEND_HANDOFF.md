# casewright — Frontend & UX Build Handoff

> **You are building the frontend UI/UX for an existing product ("casewright", a
> placeholder name) and wiring it to a Python/FastAPI backend that the human owns.**
> Read Sections 0–3 before writing any code. The design is already decided; your job
> is to implement it faithfully, build the feature *shells* as well-defined targets,
> and **co-decide every data contract with the human** rather than inventing them.

---

## 0. Working agreement (read first — these are hard rules)

1. **Do NOT invent data models, API payloads, field names, endpoints, or schemas.**
   Every request/response shape that flows between the FastAPI backend and this
   frontend is decided **together with the human**, per feature. Where a view needs
   data, do this: (a) describe the *shape of the need* in a comment, (b) stub it with
   obviously-fake placeholder data marked `// TODO: contract TBD with owner`, and
   (c) **pause and ask the human to define the contract** before you finalize that view.
   The human will decide the data alongside you.

2. **Test execution / the sandbox is OUT OF SCOPE.** Do not build any test-running,
   sandbox, or execution logic. The **Run (▶) button must render but be inert**, with
   a clear, visible indication that running isn't available yet (e.g. disabled state +
   tooltip/label "Running isn't available yet"). Structure the UI so real execution
   and output can be wired in later without a rewrite. The human builds execution on
   the backend later.

3. **Preserve the design system exactly** (Section 4). No new colors, no shadows, one
   radius. When in doubt, match the mockups.

4. **When anything is ambiguous, ask the human. Do not guess** — especially on data,
   the git mechanism, and the frontend framework if not yet confirmed.

---

## 1. Product context

casewright turns **application/feature specifications into working test code** (unit,
integration, end-to-end). A spec goes in; the system decomposes it into comprehensive
test cases, retrieves the right context per case via **hybrid agentic RAG**, and
generates the tests. It is **early-stage** — the MVP generates tests from specs;
execution, integrations, and dashboards are being added. "casewright" is a placeholder
name; keep it swappable (single source of truth for the wordmark).

Safety posture (informs UX): generated code is treated as **untrusted** — every test is
for **human review** and is meant to run in a **sandbox**, never silently. Surface this
honestly in the UI (it's a differentiator, not fine print).

---

## 2. Tech constraints

- **Backend: Python + FastAPI**, owned by the human. You integrate with it; you do not
  redesign it. Endpoint shapes are co-decided (Rule 0.1).
- **Frontend: decided — HTMX + Jinja2 templates served by FastAPI + a touch of
  Alpine.js/vanilla** for the small client interactions (tab switching, textarea autosize,
  the center→dock transition, mobile sidebar). Rationale: one stack, no separate SPA build,
  and "submit prompt → backend returns a rendered panel" is a natural HTMX swap. **Do not**
  introduce a SPA framework (React/Next/Svelte/etc.) or a Node build pipeline unless the
  human explicitly changes this decision.
- Self-contained assets where reasonable; system fonts (no webfont CDN dependency).

---

## 3. What to build vs. not build (scope at a glance)

**In scope (go):** the three-page site; the product app UI (composer → workspace, code
panel, tabs, inert Run button); the test **library**; the **Export (copy + download)**
affordance; the **Runs dashboard** shell; the **spec-coverage** view; **failure-triage**
UI shell; placeholder/empty states everywhere real data isn't wired yet.

**Out of scope (do not build):** the sandbox / test execution; **git / PR integration**
(deferred — export is copy/download only for now); the actual generation / RAG (backend);
real API contracts (co-decide, then wire); auth (unless human asks); the final product name.

---

## 4. Design system — reproduce exactly

Single light theme. **Grayscale only — no accent color, no tints.** Depth is drawn with
hairlines, never shadows. One 2px corner radius everywhere. A neutral sans for reading,
a monospace for labels/data/code. Pass/fail are shown by **symbols (✓ / ✕), never color.**
Type weights limited to 400/500. Austere / editorial; generous spacing.

```css
:root{
  --ink-base:27,28,25;                 /* warm near-black, laddered by opacity */
  --paper:#f6f5f3; --surface:#ffffff; --sunken:#eeece7; --ink-solid:#1b1c19;
  --ink:rgba(var(--ink-base),.90); --ink-2:rgba(var(--ink-base),.60); --ink-3:rgba(var(--ink-base),.42);
  --line:rgba(var(--ink-base),.12); --line-2:rgba(var(--ink-base),.22);
  --font-sans:ui-sans-serif,-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
  --font-mono:ui-monospace,"SF Mono",SFMono-Regular,Menlo,"Cascadia Code",Consolas,monospace;
  --t-eyebrow:12px; --t-micro:13px; --t-body:15px; --t-lead:19px; --t-h3:21px; --t-h2:28px; /* display: clamp() */
  --radius:2px;                        /* the ONLY radius */
  /* spacing scale: 4 8 12 16 24 32 48 64 96 px */
}
```

Rules to hold: primary button = **solid ink** (`--ink-solid`) with paper-colored text
(the one strong element); secondary = hairline "ghost"; mono uppercase eyebrows with
letter-spacing; `font-variant-numeric: tabular-nums` on all aligned digits; visible ink
focus rings; honor `prefers-reduced-motion`; never let the page body scroll sideways
(inner containers get `overflow-x:auto`).

**Visual source of truth:** the HTML mockups in **`docs/mockups/`** (see that folder's
README). `three-page-site.html` is the canonical reference — Home, How-it-works, and the
Product app in one file (hash-routed: `#home`, `#how`, `#app`); `design-dna-board.html` is
the design-system reference. They are static, self-contained (system fonts, no build) —
open them in a browser and **port their markup/CSS into your Jinja templates rather than
re-deriving the look.**

---

## 5. Pages / views

The product is a **three-page site** plus the app. Marketing pages share a top nav
(Home · How it works · "Open app"); the app view uses its own chrome (left sidebar) with
a "← Back to site" link and the wordmark linking home.

**5.1 Home** — *what it is.* Hero ("Specifications in. Working tests out."), a static
**spec → test** demo panel (spec on the left, generated test on the right) with a small
results strip, a short "what it does" capability grid, and CTAs into the app. Content is
provided; refine copy with the human.

**5.2 How it works** — *the depth.* The **Decompose → Retrieve → Generate** pipeline as
three ordered steps; a "hybrid agentic RAG" explainer with a mono retrieval diagram; a
framework-targets row; and the safety/sandbox model. Content provided.

**5.3 Product app** — the working UI. Interaction model:
- **Empty state:** one composer input **centered horizontally and vertically**, wide with
  side margins, under "What do you want to test?" + a few example-prompt chips.
- **On submit:** the composer **docks to the bottom** (smaller); the main area becomes the
  **test-code workspace**. A brief "Generating…" beat, then the generated code appears.
  The prompt becomes the active item in the sidebar library.
- **Workspace panel:** top bar with **tabs (Code · Output)** and a **▶ Run button that is
  inert** (Rule 0.2). Code tab shows the generated test with a line-number gutter. The
  **Output tab is a placeholder** ("Test execution isn't available yet") — no live output.
- **Sidebar:** the test **library** (see 6.3), a "New test" button, workspace footer.
- Responsive: sidebar collapses to an off-canvas menu under ~820px.

---

## 6. Feature goals (the targets)

Each goal has a **Definition of Done (DoD)** you can check yourself. "**Data**" lines are
**co-decided with the human** — stub and confirm, never invent (Rule 0.1). Priorities:
**P0** = build now, **P1** = next, **P2** = after, **P3** = roadmap stub only.

**G1 — Three-page site + working navigation (P0)**
- DoD: Home, How-it-works, Product render in the design system; nav + CTAs move between
  them; direct links to each page work; responsive; reduced-motion respected.

**G2 — Product app core loop (P0)**
- DoD: centered composer → submit → docked composer + code workspace; Code/Output tabs
  switch; line-number gutter; "Generating…" transition; **Run button present but inert
  and clearly labeled unavailable**; Output tab shows the "not available yet" state.
- Data: the generated-code payload shape (what the backend returns for a prompt) — **TBD
  with human.** Stub with a canned test until defined.

**G3 — Test library (evolve the sidebar) (P0→P1)**
- DoD: recent tests list (grouped, e.g. Today/Earlier); selecting one loads it; "New test"
  resets to empty state; each item shows a title and a small status glyph slot. Design for
  future per-item **versions, the spec it came from, last-run badge, tags** (build the slots
  even if empty).
- Data: the test/library item model — **TBD with human.**

**G4 — Export the generated test (P1)**  *(see Section 7)*
- DoD: every generated test has an **Export** action with **Copy code** and **Download
  file** (e.g. `.test.ts` and/or a `.diff` patch). Both work **fully client-side, no
  backend**. Do **not** build any PR / commit / repo-push affordance yet — git integration
  is deferred (Section 7).
- Data: none required for copy/download (operates on the code already in the view).

**G5 — Runs dashboard (UI shell) (P1, depends on execution+data)**
- DoD: a dashboard view with panels for **run history, pass/fail/flake rate over time,
  top failures** — built as UI with clear **empty states**; renders from a runs endpoint
  once it exists.
- Data: runs/results model — **TBD with human.** (No live data until execution exists.)

**G6 — Spec-coverage matrix (P1→P2)**
- DoD: a view mapping **specs ↔ generated tests ↔ status** (a traceability matrix) so a
  user can see which specs are covered/proven. Build the grid + empty state.
- Data: spec/coverage model — **TBD with human.** This is a differentiator (casewright is
  spec-first); give it real care.

**G7 — Failure triage UI (P2, depends on execution)**
- DoD: when a run exists, the Output/results view can classify a failure as **"found a bug"
  vs. "fix the test"** (a spec-native distinction unique to a generation tool). Build the UI
  affordance + states; logic/data later.
- Data: failure/triage model — **TBD with human.**

**G8 — Flaky detection & quarantine (P2, depends on runs data)**
- DoD: surface flaky tests and allow **quarantine**; a flake indicator in the library and
  dashboard. UI + states now; data later.

**G9 — Roadmap stubs only (P3)** — **Jira "generate on ticket", CI integration, run
scheduling.** Do not build; leave clearly-labeled placeholders/links if they aid navigation.

---

## 7. Git integration (deferred)

For now, **export is copy/download only** (G4): Copy code + Download file/patch, entirely
client-side. **Do not build** any PR, commit, branch, or repo-push flow, and do not add a
provider (GitHub/GitLab/etc.) affordance to the UI.

Deeper git integration (e.g. opening a **pull request** so review = the safety model) is a
**future decision the human will make** — it depends on whether casewright ends up a hosted
product or a local dev tool, and on a backend design they own. When that time comes it will
be specced separately; leave no half-built git UI behind in the meantime.

---

## 8. Explicit non-goals / deferred

Sandbox & test execution; live run output; real API/data contracts (co-decide then wire);
the generation/RAG engine (backend); authentication (unless requested); final branding/name;
any provider-specific git logic hardcoded in the frontend.

---

## 9. Definition of done (overall)

A working, on-design **frontend paired with FastAPI**, where: the three pages + app loop
work; all data is **stubbed and clearly marked** with contracts left open for the human;
the **Run button is inert and labeled**; Export offers **Copy + Download** (working, client-
side; no git/PR); dashboard/coverage/triage exist as **UI shells with empty states**; everything
is responsive, accessible (focus states, reduced-motion), and matches the grayscale system.
Before finalizing any view that needs backend data, you have **confirmed the contract with
the human.**
