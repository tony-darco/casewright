# Mockups — visual source of truth

These are the approved, static HTML mockups for casewright's frontend. They are the
**visual source of truth** referenced by `../FRONTEND_HANDOFF.md`. Port their markup and
CSS into the real Jinja templates — don't re-derive the look.

They are fully self-contained: system fonts, all CSS/JS inline, no build step, no network.
**Just open each file in a browser.**

## Files

- **`three-page-site.html`** — the canonical reference. All three views in one file,
  switched by URL hash:
  - `#home` — Home (what the product is)
  - `#how` — How it works (the decompose → retrieve → generate pipeline)
  - `#app` — the Product app (centered composer → docked composer + code workspace,
    Code/Output tabs, sidebar test library). Try it: type a prompt or click an example,
    then note the **Run button** and the **Output tab**.
- **`design-dna-board.html`** — the design system itself: the grayscale palette, type
  scale, the single 2px radius, spacing, and primitives (buttons, state chips, etc.).
  Use this to get tokens exactly right.

## Important notes for the build

- The mockups' JS **fakes** generation and (in earlier drafts) running, purely to
  demonstrate the UX. In the real build, generation comes from the FastAPI backend, and
  **test execution is out of scope** — the Run button must be present but inert and clearly
  labeled unavailable (see handoff §0.2). Do **not** copy the mockup's fake run/output logic
  as if it were real.
- "casewright" is a **placeholder name** — keep the wordmark a single, swappable source.
- Grayscale only, no color, symbols (✓/✕) for pass/fail — see the handoff §4 and the board.
