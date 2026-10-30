---
target: templates/index.html
total_score: 31
max_score: 40
na_heuristics: 
p0_count: 0
p1_count: 1
target_identity: "file:C:\\projects\\test\\Poster\\templates\\index.html"
target_fingerprint: "sha256:d253cab8cbe8545eeb2ea6dc2a9e1119bd9ba1fc037e846cbb0754a4b5861662"
target_path: "C:\\projects\\test\\Poster\\templates\\index.html"
timestamp: 2026-10-30T04-40-27Z
slug: templates-index-html
closed: true
---
Method: dual-agent (A: design-review sub-agent · B: detector-and-browser sub-agent). Demo mode only; Live-only paths judged from code.

## Design Health Score

| # | Heuristic | Score | Key Issue |
|---|-----------|-------|-----------|
| 1 | Visibility of System Status | 3 | Batch line, chips, progress panel excellent; refusal-reason toasts render under the modal Settings drawer |
| 2 | Match System / Real World | 3 | Plain sentence case; jargon leaks ("Unicode code points", "localStorage", "Parallel requests") |
| 3 | User Control and Freedom | 3 | Cancel, Restore, Esc; single-image Remove has no confirm or undo |
| 4 | Consistency and Standards | 3 | Two differently shaped editors for one Buffer post; 22:00 and 11 pm in one sentence |
| 5 | Error Prevention | 4 | Pre-flight "can't be scheduled yet", taken-minute skipping, night warning, Cancel-first dialogs |
| 6 | Recognition Rather Than Recall | 3 | Scheduled rows name their image; card move/remove icon-only (labelled) |
| 7 | Flexibility and Efficiency | 3 | Select all, planner, List view, Alt+arrows; no stage-action accelerators, no bulk hashtag/category edits |
| 8 | Aesthetic and Minimalist Design | 3 | Calm, one signal; always-on hints, empty AI panel shows four disabled "0" buttons |
| 9 | Error Recovery | 3 | Per-file upload reasons, inline errors; blocking reasons behind the drawer (server-info partly compensates) |
| 10 | Help and Documentation | 3 | Strong contextual hints; no shortcut sheet or help entry |
| **Total** | | **31/40** | **Good** |

## Design Specificity Verdict

LLM: behaviour and copy are specific to Poster (stage-driven One Signal, dispatch-slip batch line, prose planner preview, "Scheduled · Demo", "(10 channel posts)", estimate disclaimer, script-aware counters). The visual layer is a stock SaaS kit (Untitled UI greys, Tailwind indigo-600, white 14px cards, pill chips); nothing visual says "Dispatch Desk" beyond the batch line and send-arrow mark.

Deterministic: CLI exit 0, 1 advisory design-system-color on the skip link (index.html:77) — false positive; the CLI could not resolve the Jinja stylesheet href, so colour/custom-property rules ran without poster.css. Browser (5 states): flat-type-hierarchy (deliberate per DESIGN.md), nested-cards x7 (#dropzone, 3 thumb-cards, #op-status, #ed-result, #ed-tags, #ed-preview) — individually sanctioned or false positive; the cumulative signal (four bordered boxes stacked in the AI panel) agrees with the editor-valley finding. Contrast clean in light and dark. Overlay injected in headless Playwright; no persistent [Human] tab.

## Priority Issues

1. [P1] Refusal reason toast hidden behind the Settings drawer the same click opens — controller.js:468-469, :489-490; orchestrator.js:38-39, :103-104, :111-112, :117-118. Toast region renders under the showModal() top layer: blurred, unclickable, inert, timer expires. Fix: pass reason into settingsView.open(section, {reason}) and render in #settings-errors (index.html:548); never pair toast with modal; or make toast region popover="manual". Command: harden.
2. [P2] Editor is the valley: ungrouped 10-part form (index.html:396-475); Schedule post (:362) under the photo away from Publish at/Channels; Post preview last and below the sticky panel fold. Fix: Words / When & where (ending in Schedule post) / Model notes (folded); preview beside caption; consider List view as default review. Command: layout.
3. [P2] Locked fields look editable: disabled uses --surface-2 (#f9fafb, ~1.04:1) with full Field Edge border (poster.css:313, :884) vs DESIGN.md:340 Well Grey; disabled fieldset drops caption from Tab order. Fix: --surface-3, hairline border, lock glyph + "In Buffer"; readonly instead of disabled fieldset. Command: polish.
4. [P2] Two editors for one Buffer post (inline Edit in Buffer with pills + 2200 counter vs dialog with flattened hashtags, no limit); DESIGN.md:395 vs :408 contradict. Fix: row Edit opens the inline Edit-in-Buffer state; dialog only for per-channel delete; one rule in DESIGN.md. Command: distill.
5. [P2] Scheduled-post row Edit/Delete have identical accessible names on every row (posts.js:140-141). Fix: include "#n · file name". Command: clarify.

## Persona Red Flags

Alex: no accelerators for Generate all / Apply / All ready; hashtag pruning one ✕ at a time, read-only in List view, no bulk tag/category edit; Regenerate always confirms even on untouched posts; toasts over the gallery.
Sam: identical row button names; refusal toasts inert under modal; disabled fieldset removes caption from Tab order on scheduled posts. Good: labelled gallery photo buttons, move announcer, skip link.
Project persona (Bengali + English owner): no caption-language control, Demo always English; whole mixed caption gets one lang="bn"; "Unicode code points" hint is developer language. Good: Nirmala UI rendering, ZWJ kept, pills wrap.

## Minor Observations

- Mixed time formats in planner preview (locale "22:00" vs hard-coded "11 pm"); neither matches DESIGN.md's "Fri, 30 Oct, 4:00 pm".
- Demo says "not in Buffer" then "Shown as it is in Buffer" + Edit in Buffer; awkward delete title (posts.js:365).
- Done batch: Generate "All images 5" is the most prominent enabled control.
- Gallery-card trash removes in one click with no confirm/undo (gallery.js:360-370).
- Open card and focused card both get indigo borders.
- Grid/List toggle jumps position; "All" sorts newest first (reverse of gallery order).
- Lower row 1fr/1fr doesn't align with workspace 5fr/6fr.
- Phone: Scheduled posts is a 460px scroll box inside a 5,330px page.
- Mode switches immediately from badge but waits for Save in drawer.
- DESIGN.md drift: :395 vs :408; locked-field fill.

## Questions to Consider

- If the batch is the unit of work, why is the single-post editor the largest permanent surface and List view secondary?
- What would the dispatch slip look like as the page's centrepiece instead of a 13px muted line?
- Should caption language be as first-class as Tone?
- Is a green bar and one sentence the right ending, or a glanceable timeline of the batch as Buffer holds it?
