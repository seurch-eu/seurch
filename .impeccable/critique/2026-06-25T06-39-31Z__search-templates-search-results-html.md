---
target: search/templates/search/results.html
total_score: 29
p0_count: 0
p1_count: 2
timestamp: 2026-06-25T06-39-31Z
slug: search-templates-search-results-html
---
# Critique: search/templates/search/results.html

Reviewed together with its default content web/_panel.html, pagination, and footer (that's what renders).

## Design Health Score

| # | Heuristic | Score | Key Issue |
|---|-----------|-------|-----------|
| 1 | Visibility of System Status | 2 | No result count, no "which engines searched" summary, tiny low-contrast page indicator |
| 2 | Match System / Real World | 4 | Strong search conventions: favicon, green URL, "Did you mean", sitelinks, plain language |
| 3 | User Control and Freedom | 3 | Query persists, filters editable, back/tabs work; no one-click "clear filters" |
| 4 | Consistency and Standards | 3 | Wordmark gradient (home) vs solid indigo (here); raw slate-* instead of tokens |
| 5 | Error Prevention | 3 | "Block site" fires with no confirm; otherwise constrained selects, safe-search default |
| 6 | Recognition Rather Than Recall | 3 | Filters visible, tabs labeled; per-result actions hidden until hover |
| 7 | Flexibility and Efficiency | 3 | Keyboard result nav, bangs, autocomplete, on-change filter nav |
| 8 | Aesthetic and Minimalist Design | 3 | Clean restrained 700px column; source labels noise-or-invisible |
| 9 | Error Recovery | 3 | Excellent, specific empty states (no engines / no keys / no results) |
| 10 | Help and Documentation | 2 | Footer links only; no contextual help on filters or metasearch concept |
| **Total** | | **29/40** | **Good** |

## Anti-Patterns Verdict

Does this look AI-generated? No. Hand-built production search UI: four distinct actionable empty states, deep no-JS degradation, i18n, keyboard nav. No gradient-text abuse, no eyebrows, no identical card grids.

Deterministic scan (3 findings — both rules read as FALSE POSITIVES in context):
- ai-color-palette x2 at web/_panel.html:67 — flags text-indigo-600/400 result titles as "AI purple." This is Signal Indigo, the committed brand color in DESIGN.md, not AI violet gradient. Fair nudge that indigo carries a lot here.
- flat-type-hierarchy at results.html:24 — reports 12/14/19.2px at 1.6:1, which is healthy, not flat (<1.25). Self-contradicting; disregard.

Visual overlays: none — browser automation not enabled this session, no dev server. Findings source-derived.

## Overall Impression

A calm, legible, genuinely resilient search page that lives up to "The Quiet Instrument." Biggest opportunity: the one thing the page exists to prove (multi-engine blending) is hidden. Engine-provenance labels render at text-slate-300 (~1.5:1), effectively invisible. The differentiator is buried.

## What's Working

1. Empty/error states are excellent — four separate specific actionable messages (no query, no engines, no keys with exact env vars, no results).
2. No-JS resilience is thorough and honest (real-link tabs, details kebab, submit fallback, eager-render link).
3. Restrained result composition — 700px column, indigo only on titles/links/actions, line-clamped descriptions.

## Priority Issues

- [P1] Engine provenance is invisible. web/_panel.html:57-65 renders contributing engine + multi-engine source_label at text-slate-300 dark:text-slate-600 (~1.5:1). Fails WCAG AA AND throws away the core value prop. DESIGN.md names "found in multiple engines" as a Signal Indigo moment. Fix: brand-soft chip + text-brand when >=2 engines agree, ink-muted for single source. Command: /impeccable colorize

- [P1] Per-result actions vanish on touch. Kebab (web/_panel.html:36) is opacity-0 group-hover:opacity-100. Coarse-pointer devices have no hover, so Cached/Block-site is permanently invisible on mobile. Fix: reveal on @media (hover: none). Command: /impeccable adapt

- [P2] No system-status / proof-of-work. No result count, no "Searched Brave/Mojeek/Marginalia" line. Pagination Prev/Next only with text-slate-400 (~2.5:1) page indicator. Fix: slim results-meta line + bump page indicator to ink-muted. Command: /impeccable harden

- [P2] Muted text rides the contrast floor. Descriptions text-slate-500 (~4.76:1) just scrape AA; page indicator and some meta below it. Fix: ink-muted floor for body-weight secondary text; reserve ink-soft for decoration. Command: /impeccable audit

- [P3] Brand inconsistencies. (a) Wordmark gradient on home vs text-indigo-600 here (results.html:24). (b) Result URL text-green-700 echoes Google-SERP convention, brushing the "surveillance big-search" anti-reference. Command: /impeccable polish

## Persona Red Flags

Sam (Accessibility): engine labels ~1.5:1 and "Page N" ~2.5:1 unreadable; provenance conveyed by color/tiny-grey alone, no non-color cue. Filter selects navigate on change, surprising keyboard users. Titles are real focusable anchors — good.

Casey (Mobile): kebab invisible (hover-gated) on touch — Block-site unreachable one-handed. Tabs scroll horizontally (fine). State in URL, interruption-safe. Search top-of-screen acceptable.

Alex (Power): well served — j/k nav, bangs, autocomplete, on-change filters, no modals. Could want visible result count + keyboard hints.

"The Operator" (project persona): wants confidence the blend works; on results that signal is only the near-invisible per-result labels; engine-status is a footer link away.

## Minor Observations

- md:pl-[160px] magic numbers approximate search-box alignment rather than a shared layout token.
- Templates use raw slate-*/indigo-* utilities not semantic tokens (DESIGN.md Tokens-Only Rule drifting) — audit/refactor concern, not UX.
- backdrop-blur on sticky header is purposeful (legible scroll), not decorative glass — fine.

## Questions to Consider

- What if "blended from 3 engines" were a confident visible statement at the top, instead of a 1.5:1 whisper per row?
- Does the green URL serve Seurch, or is it muscle-memory from the engine you define yourself against?
- On a page about agreement between engines, what's the most honest way to show two engines agreed — and should that be where indigo earns its keep?
