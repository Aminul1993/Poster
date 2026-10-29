---
target: templates/index.html
total_score: 26
max_score: 40
na_heuristics: 
p0_count: 0
p1_count: 3
target_identity: "file:C:\\projects\\test\\Poster\\templates\\index.html"
target_fingerprint: "sha256:3ab72fb10aa06b8062cd7d22613f65ebc8efffe2b75aa8330b34d828463f749c"
target_path: "C:\\projects\\test\\Poster\\templates\\index.html"
timestamp: 2026-10-29T07-11-10Z
slug: templates-index-html
---
Method: two separate reviewers working in parallel. A did a design review by hand. B ran the automated design checker on the source and on the rendered page. Both used Poster in Demo mode; neither saw the other's results.

## Design Health Score

| # | Heuristic | Score | Key Issue |
|---|-----------|-------|-----------|
| 1 | Visibility of System Status | 3 | Batch progress, counts and the "Read from Buffer" timestamp are excellent. But "Ready" only means "has a caption", and a "Failed" chip stays after the problem is fixed. |
| 2 | Match System / Real World | 3 | Plain, precise copy. But the planner only takes minutes (daily means typing 1440), and "Engagement score (1–100)" never says it's an estimate. |
| 3 | User Control and Freedom | 3 | Cancelling a batch, the unsaved-settings guard, and editing or deleting in Buffer are all good. But Regenerate can't be undone, and switching from Demo to Live takes one click. |
| 4 | Consistency and Standards | 2 | Mode changes instantly from the header but needs Save in Settings. Single-post Generate/Schedule appears in two places. "Ready" is amber in the KPI tile and indigo on the chip. |
| 5 | Error Prevention | 2 | The highlighted "All ready" button fails every post when no times are set. Dialogs put focus on the overwriting or destructive button. |
| 6 | Recognition Rather Than Recall | 2 | Gallery cards show file size and pixel dimensions, not the caption, time or channels, so all 25 posts have to be opened one by one. |
| 7 | Flexibility and Efficiency | 2 | The bulk actions are strong. But there's no next/previous-post or "next one needing attention" shortcut, and it takes 136 Tab presses to reach the caption with 25 images. |
| 8 | Aesthetic and Minimalist Design | 3 | Calm and consistent. But the KPI row repeats the button counts, and the preview repeats the caption. |
| 9 | Error Recovery | 3 | Errors are specific and banners offer actions. But the "no publish time" failure doesn't point to the planner. |
| 10 | Help and Documentation | 3 | The inline hints are unusually good. Nothing states the order of the work: generate → review → plan times → schedule. |
| **Total** | | **26/40** | **Acceptable** |

## Design Specificity Verdict

**Design review:** the way Poster works belongs to this product. The Generate/Schedule × This/Selected/All buttons with live counts, the batch progress panel, the caption/alternative caption swap, the hashtag editor that handles Indic scripts, and the honest Demo labelling all come from a real batch publisher. The look is borrowed: the Untitled UI grey and status colours plus Tailwind indigo, four tinted-icon KPI tiles, and a violet gradient brand mark with a glow. For a working tool that's acceptable. The real gap is structural. The "Dispatch Desk" idea never shows up in the layout: the batch is never presented as a batch (a list of posts with times, order and channels at a glance), only as a gallery of files plus a form for one post.

**Automated check:**
- **Source scan of `index.html`:** 3 findings, all false positives. The checker couldn't follow the Jinja stylesheet link, so it scanned with no CSS, and it flagged `#ed-image` for having no `src` even though that is set at runtime.
- **`static/js/ui`:** 1 real finding. The channel colour dot in `static/js/ui/dom.js:39` has a 2px radius, but DESIGN.md says dots are circles.
- **Rendered page:**
  - 6 findings in light theme, 9 in dark, 6 at phone width.
  - 52 when populated, mostly the same two findings repeated once per log entry.
  - The repeated two: timestamps at 2.6:1 contrast (×17) and 10px category tags (×17).
  - Also: the purple palette, the brand-mark glow on dark, two Settings descriptions at about 99 characters per line, 11.5px log detail text, and some single-word last lines.
- **Where the two reviews agree:**
  - The faint grey text fails contrast.
  - The violet gradients and glow read as the growth-hacker look you ruled out. The checker only stopped flagging the palette once both gradients were removed.
- **What only the checker found:**
  - The red "Failed" chip measures 4.44:1.
  - Muted text on the selected mode option measures 4.45:1.
  - The capacity bar ("0 of 25 slots used") also uses the gradient. DESIGN.md wrongly says only the running batch bar does.
- **False positives:**
  - The two source-scan findings caused by the missing stylesheet.
  - The `#ed-image` broken-image flag.
  - "Card inside card" on the hashtag editor (it's a text field).

**Visual overlays:** none. The browser runs were headless, so no page with highlighted issues was shown to you.

## Overall Impression

The engineering underneath is careful: it guards against duplicate posts, rereads posts from Buffer, can cancel a batch partway, and is honest about Demo mode. The main path, though, breaks at the most important moment. After **Generate all**, the highlighted **All ready** button schedules nothing because no post has a time yet, and every card turns red. The single biggest opportunity is to make the batch itself the thing you review and schedule, so a post without a time can't exist at the moment of scheduling.

## What's Working

- **The bulk action buttons.** GENERATE and SCHEDULE × This / Selected / All, each with a live count and disabled when it doesn't apply. "The batch is the unit of work" is built into the controls, and you can see what each click will touch before you click.
- **Honest feedback during work.** The progress panel shows succeeded, failed, skipped and retried counts. Cancelling says it will "let the post already sent to Buffer finish". Demo images are stamped "not AI-generated". The scheduled list shows when it was read from Buffer. This earns the trust PRODUCT.md asks for.
- **South Asian scripts mostly work.** Hashtags normalise with NFC and keep letters, combining marks and digits from any script. Counters count characters, not bytes. Bengali, Hindi and Tamil tags rendered cleanly in pills, preview and the edit dialog, using the Nirmala UI fallback font.

## Priority Issues

**[P1] "Ready" doesn't mean ready to schedule, so the main path fails completely**
- **What:**
  - "Ready" is set as soon as a caption exists (`static/js/store.js:28`).
  - After Generate all, **All ready N** is the highlighted button and the KPI says "N Ready to schedule". With no times set, every post fails with "Choose a date and time to publish".
  - The planner is collapsed and sits below the Schedule row (`templates/index.html:271-316`).
  - Applying the planner afterwards doesn't clear the error (`static/js/scheduler.js:319-332`), so the cards stay red "Failed" even though they're now fine.
- **Why it matters:** this is the core flow at its highest-stakes step. Status chips that keep saying "Failed" after a fix teach the owner not to trust them. In Live, the confirmation dialog even promises posts will go out "at its publish time".
- **Fix:**
  - Make "Ready" require a caption and a valid time, and show caption-only posts as "Needs time".
  - Count only schedulable posts in **All ready**, and add "6 need a publish time · Plan times" next to it.
  - Order the panel Generate → Planner → Schedule, and open the planner automatically when any post has no time.
  - Check for missing times before a run starts.
  - Make `planTimes` clear errors left from a failed scheduling attempt.
  - Show the first and last publish time in the Live confirmation.
- **Suggested command:** `/impeccable harden`

**[P1] Reviewing posts breaks down at batch size**
- **What:**
  - Gallery cards show the file name, size in KB and pixel dimensions, not the caption, time or channels.
  - The editor doesn't stay on screen and isn't scrolled into view when you pick a card (`static/js/ui/gallery.js:121-123`). With 25 images, clicking card 22 left the editor 1,597px off-screen.
  - Reaching the Caption field by keyboard takes 136 Tab presses.
- **Why it matters:** PRODUCT.md defines success as a 25-image batch done in one sitting, and reviewing posts is most of that sitting.
- **Fix:**
  - Make the AI content panel stay on screen while the page scrolls (`position: sticky` with its own scroll) at 1180px and wider, and scroll it into view below that width.
  - Replace size and dimensions on cards with the caption's first line, publish time and hashtag count.
  - Add next/previous-post and "next one needing attention" shortcuts.
- **Suggested command:** `/impeccable layout`

**[P1] Switching from Demo to Live misreports state and has no guard**
- **What:**
  - One click on the header badge switches to Live (`static/js/ui/controller.js:215`).
  - The banner's filled indigo **Switch to Live mode** is the loudest control on an empty page.
  - Posts scheduled in Demo keep a green "Scheduled" chip. They're left out of Live's **All ready**, and the confirmation calls them "already scheduled".
- **Why it matters:** this breaks "show the true state" at exactly the moment posts start going to real accounts.
- **Fix:**
  - Ask for confirmation before switching to Live, and say what changes: real Ollama calls, posting to the named channels.
  - Offer "Clear demo results" in that dialog.
  - Show Demo results with a neutral "Demo only" chip, and ignore them when Live decides what to skip.
  - Make the banner button a normal button.
- **Suggested command:** `/impeccable harden`

**[P2] Dialogs default to the destructive choice**
- **What:**
  - Every dialog focuses its primary button (`static/js/ui/dialogs.js:31`).
  - In the regenerate dialog, **Regenerate all N**, which overwrites your edits, is the indigo primary, so it has focus.
  - Delete-from-Buffer, Remove all, Reset and Clear log put focus on the destructive button.
  - There's no undo.
- **Why it matters:** one press of Enter destroys a hand-written Bengali caption or deletes a post in Buffer.
- **Fix:**
  - Focus Cancel in danger dialogs.
  - Make "Only new" the primary whenever it applies, and style "Regenerate all" as danger when posts have edits.
  - Keep the previous content so a regeneration can be undone once.
- **Suggested command:** `/impeccable harden`

**[P2] Text the owner must read, or wrote, gets degraded**
- **Contrast** (B measured it, A saw it):
  - Faint Slate `#98a2b3` measures 2.58:1 on white and 3.58:1 in dark theme. It's used for the "optional" marker, placeholders, the empty preview, post IDs and log timestamps.
  - Log category tags are 10px.
  - The red "Failed" chip measures 4.44:1.
  - Grip icons, empty-state icons and the service dots are below 3:1 in light theme.
- **South Asian text:**
  - Hashtags silently lose zero-width joiners, so `#র‍্যালি` becomes `#র্যালি`, a different rendered word (`static/js/validation.js:111`).
  - `truncate()` counts UTF-16 code units, so it can cut a conjunct in half in the delete dialog's quote (`static/js/util.js:128-131`).
  - Caption fields have no `lang`, so screen readers read Bengali with an English voice.
  - "Over X's 280 limit" shows even when no X channel is selected (`static/js/ui/editor.js:155`). Long Bengali captions make this false alarm permanent.
- **Why it matters:** PRODUCT.md calls South Asian scripts normal content. Changing what the owner wrote, without saying so, is the worst kind of failure for a tool that posts under their name.
- **Fix:**
  - Darken `--faint` to about `#6b7385` (4.5:1 on white) or move readable uses to Muted Slate. Raise log tags to 11px.
  - Keep U+200C and U+200D in hashtags.
  - Cut text at grapheme boundaries with `Intl.Segmenter`.
  - Allow `lang` on caption fields.
  - Show the 280-character warning only when an X channel is selected.
- **Suggested commands:** `/impeccable harden` (script handling), `/impeccable typeset` (contrast and size)

## Persona Red Flags

**Alex (impatient power user):**
- The only shortcuts are Ctrl+Enter in the image prompts and Alt+←/→ to reorder. There's nothing for next/previous post, Generate all, Schedule all, or "jump to failed".
- The planner takes minutes only and doesn't show when the last post will go out.
- Every added image raises its own toast, and the activity log reaches 128 entries for 25 images.
- Tone of voice can only be set globally, through a trip to Settings.
- There's no shift-click to select a range.
- The regenerate dialog interrupts every rerun.

**Sam (keyboard only, screen reader, low vision):**
- Each gallery card has 5 tab stops (125 for a full gallery) before the AI panel.
- Picking a card doesn't move focus to the editor; the only announcement is "22 of 25".
- The focus outline on photos is an inset 2px indigo ring, which measured about 1.1:1 against the purple demo images.
- Faint text measures 2.58:1, and log tags are 10px.
- Destructive dialogs focus the destructive button.
- Bengali captions are read with an English voice.

**The owner writing Bengali mixed with English:**
- Zero-width joiner forms disappear from hashtags with no notice.
- The false "over X's 280" warning shows on posts that never go to X.
- Hashtag pills cut long compound tags with an ellipsis.
- The 4-row caption box scrolls internally on a 400-character caption, and then the preview repeats it below.
- **Edit scheduled post** merges caption and hashtags into one textarea, which loses the 15-tag limit and the duplicate check.
- Text that holds post content is 12.5–13px. Bengali in Nirmala UI at that size is noticeably denser than Latin text.

## Minor Observations

**Mistakes in DESIGN.md, the file I wrote in the previous step:**
- **Engagement score:** the slider, the green/amber/red colouring and the log line "engagement 79/100" in success green present an estimate as a metric. The score isn't used in the post at all. DESIGN.md's rule giving estimates high/mid/low colours contradicts PRODUCT.md, so the rule needs to go.
- **KPI row:** it's the analytics-dashboard look you ruled out, in miniature. It's the largest text on the page, repeating counts the buttons already show.
- **Capacity bar:** it uses the violet gradient too, which DESIGN.md doesn't mention.

**In the dashboard:**
- Editor fields stay editable after a post is scheduled, but those edits never reach Buffer.
- "Buffer channels · Change" in the editor opens the global Settings, not anything specific to that post.
- "Upcoming in Buffer 12" counts one post per channel, so it shows 12 after 6 posts are scheduled.
- The Scheduled posts list sorts newest first under All but oldest first under Upcoming.
- In Demo, the "Ollama · Demo" and "Buffer · Demo" pills look like the clickable mode badge but aren't buttons.
- "Reset All Settings" is in title case; everything else is sentence case.
- Two Settings descriptions run about 99 characters per line.
- The timezone select has about 400 entries and can't be searched.
- The channel colour dot has a 2px radius instead of being a circle.

## Questions to Consider

- If the batch is the unit of work, why is the review surface a form for one post? What would a 25-row editable list (thumbnail · caption · time · channels · status) do to session time?
- Should "Schedule" and "Plan times" be one action, such as "Schedule 25 posts, every 60 min from 13:30", so a post without a time can't exist at scheduling?
- What does the KPI row tell you that the button counts don't? What would break if it were gone?
- Should Live look different enough (header, accent) that you can't mistake it for Demo from across the room?
