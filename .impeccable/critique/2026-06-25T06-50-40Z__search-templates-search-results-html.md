---
target: search/templates/search/results.html
total_score: 30
p0_count: 0
p1_count: 0
timestamp: 2026-06-25T06-50-40Z
slug: search-templates-search-results-html
---
# Critique: search/templates/search/results.html (re-run after fixes)

Reviewed with web/_panel.html, _pagination.html, footer.

## Design Health Score

| # | Heuristic | Score | Key Issue |
|---|-----------|-------|-----------|
| 1 | Visibility of System Status | 3 | Page indicator now readable + aria-current + rel=prev/next; still no result-count/engine summary (deliberately deferred) |
| 2 | Match System / Real World | 4 | Strong search conventions |
| 3 | User Control and Freedom | 3 | No one-click clear-filters |
| 4 | Consistency and Standards | 3 | Wordmark gradient(home)/solid(here); raw slate-* vs tokens |
| 5 | Error Prevention | 3 | Block site fires with no confirm |
| 6 | Recognition Rather Than Recall | 3 | Per-result actions now reachable on touch |
| 7 | Flexibility and Efficiency | 3 | Keyboard nav, bangs, autocomplete |
| 8 | Aesthetic and Minimalist Design | 3 | Provenance now legible instead of invisible noise |
| 9 | Error Recovery | 3 | Excellent specific empty states |
| 10 | Help and Documentation | 2 | Footer links only; no contextual help |
| Total | | 30/40 | Good |

Caveat: the real wins were contrast/a11y + a functional mobile bug. Nielsen heuristics have no contrast axis, so those improvements barely move this scale though they materially helped users. They show up in an audit (Accessibility dimension). Read 29->30 as "heuristic frame is coarse," not "fixes were marginal."

## Anti-Patterns Verdict

AI-generated? Clear no. Deterministic scan: 2 findings, both ai-color-palette flagging text-indigo-600/400 result titles at web/_panel.html:67 — committed Signal Indigo (DESIGN.md), not AI violet. Same false positive as baseline; disregarded. Prior flat-type-hierarchy finding cleared once wordmark stopped being inline-styled. Visual overlays: none (no browser automation/dev server); source-derived.

## What Changed Since Baseline (29/40)

- Engine provenance now legible (web/_panel.html:57-65): slate-300 -> slate-500/400, ~1.5:1 -> ~4.8:1. Kept minimal (contrast only). P1 resolved.
- Per-result actions reachable on touch (assets/app.css @media hover:none): Cached/Block-site no longer hover-trapped on mobile. Mobile P1 resolved.
- Pagination wayfinding (_pagination.html): readable indicator (~2.5:1 -> ~4.8:1), aria-current, rel=prev/next. P2 slice resolved.
- Description/age/placeholder lifted to comfortable AA floor. P2 resolved.
- Wordmark drift (results.html): inline styles -> utilities. P3 resolved.

## Remaining Issues

- [P2] No proof-of-work on a metasearch page. Still no visible "searched N engines / blended X results." Deliberately kept minimal — a chosen gap, not a defect, but the biggest unrealized opportunity.
- [P3] Two open brand decisions (untouched): gradient-vs-solid wordmark; green-700 result URL echoing Google SERP convention. Here by design until decided.
- [P2] Help/contextual guidance (heuristic #10, score 2): no inline help on filters or metasearch concept. Out of scope this pass.
- Systemic (not user-facing): templates still use raw slate-*/indigo-* rather than semantic tokens — Tokens-Only Rule drift. Audit/refactor concern.

## Persona Red Flags (re-walk)

Sam (a11y): the two WCAG-failing colors (provenance ~1.5:1, page indicator ~2.5:1) fixed. Remaining: filter selects navigate on change (surprises keyboard users); provenance readable but conveyed by small text alone (no icon for multi-engine agreement).
Casey (mobile): kebab actions now visible on touch — blocking issue gone.
Alex (power): unchanged, well served.

## Questions to Consider

- Engine-label contrast resolved but kept minimal — at what point does "two engines agreed" deserve to be stated rather than whispered? The one remaining lever on the differentiator.
- Worth a quick audit run to see the Accessibility dimension reflect these fixes.
