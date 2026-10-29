---
target: templates/index.html
total_score: 28
max_score: 40
na_heuristics: 
p0_count: 0
p1_count: 2
target_identity: "file:C:\\projects\\test\\Poster\\templates\\index.html"
target_fingerprint: "sha256:f4263a9c574117479526e8f7d41d62aaf30b436185ede8f192de2d7adaf47814"
target_path: "C:\\projects\\test\\Poster\\templates\\index.html"
timestamp: 2026-10-29T09-48-04Z
slug: templates-index-html
---
Method: two separate reviewers working in parallel. A did a fresh design review by hand without seeing the earlier reports. B ran the automated design checker on the source and on seven page states. Both used Poster in Demo mode; neither saw the other's results.

## Design Health Score

| # | Heuristic | Score | Key Issue |
|---|-----------|-------|-----------|
| 1 | Visibility of System Status | 3 | Excellent batch line, chips and progress. But the closing line says "6 posts queued" while the batch line says "12 upcoming" (it counts channel copies), and editing a scheduled post changes nothing in Buffer, with no warning in Demo. |
| 2 | Match System / Real World | 3 | Plain, precise copy. Deleting one channel copy on purpose turns the card amber "Partly scheduled", so a deliberate choice reads as a problem. |
| 3 | User Control and Freedom | 3 | Cancel, restoring regenerated content, and safe dialog focus all work. Removing a post from both channels takes two deletes and two dialogs. |
| 4 | Consistency and Standards | 2 | The gallery shows "Scheduled · Demo" but Scheduled posts shows a green "Scheduled". The List view locks scheduled captions but the editor doesn't. Dates still appear in four formats (the log and the posts footer have their own). |
| 5 | Error Prevention | 3 | Good check before scheduling and a guarded switch to Live. But the editor lets you change posts that are already scheduled, and the planner spreads 25 hourly posts through the night without comment. |
| 6 | Recognition Rather Than Recall | 3 | Chips, counts and "Next to fix" help. Grid cards show about three Bengali words of caption, and the List view hides hashtags. |
| 7 | Flexibility and Efficiency | 3 | Batch actions, the planner, shortcuts, skip links and the List view. There's no shortcut for the stage's main action, and the List view is limited to the narrow column. |
| 8 | Aesthetic and Minimalist Design | 3 | Calm. But "Demo" appears six or more times at once, the editor shows its full form before any caption exists, the planner stays open after use, and up to four toasts stack up. |
| 9 | Error Recovery | 3 | Good messages and per-post failure lists. The "can't be scheduled yet" dialog doesn't name the posts. |
| 10 | Help and Documentation | 2 | Good inline hints. Shortcuts are only discoverable through tooltips. |
| **Total** | | **28/40** | **Good** |

## Design Specificity Verdict

**Design review:** the behaviour is now unmistakably this product. The batch line reads like a dispatch slip, and the single indigo button moves with the stage (All images → Apply to all → All ready → nothing). "In Demo mode (not in Buffer)" appears wherever results are reported. The check before scheduling, and the closing line with the first and last publish times, could not be swapped into another product. The visual surface is still a standard kit, which DESIGN.md accepts, so the identity rests on copy and state. It never drifts into the looks you ruled out.

**Automated check:**
- **Rendered page:**

  | State | Warnings | Advisory | Last round |
  |---|---|---|---|
  | Populated, light | 4 | 1 | 18 |
  | Populated, phone | 10 | 1 | 24 |
  | Empty page | 1 | 1 | — |

  The List view, the Settings drawer and high-contrast mode all add nothing new. There are no contrast, tiny-text, colour or gradient findings.
- **What remains:** only "box inside a card" warnings (the progress panel, the hashtag field, the post preview, the inline success alert, and the gallery cards at phone width), all sanctioned by DESIGN.md. The "flat type hierarchy" advisory is also there, as intended.
- **Source scans:** one advisory each, both false positives. The checker can't follow the Jinja stylesheet link, and `#000` is only an input to the avatar colour mix.
- **Where the reviews agree:** high contrast and toasts covering content.
- **What only the checker found:** dark-theme field borders on Tray Grey (the planner inputs and the read-only List fields) measure 2.99:1, just under 3:1.
- **What only the design review found:** the log announcements, the editor not being locked, and how Scheduled posts reads.
- **Ignores:** the two ignores apply to source scans only. Rendered findings are filed under the page URL.

**Visual overlays:** none. The browser runs were headless, so no page with highlighted issues was shown to you.

## Overall Impression

This is a real step up, to Good. The page now always points at the next action, Demo is honest everywhere, Bengali is handled well, and the checker is down to sanctioned patterns. What's left is about the two moments that matter most. During review, the editor still sits fourth in a crowded panel. At the end, Scheduled posts lists channel copies newest first, and a post that's already in Buffer can still be edited in the editor without warning. The desk never quite "clears".

## What's Working

- **The indigo button follows the stage.** The reviewer checked it at every stage: All images, then Apply to all (with the planner opening by itself), then All ready, then nothing once the batch is done.
- **The true state, told plainly.** "Not in Buffer" appears everywhere results are reported, scheduled posts are skipped in bulk runs, the estimate reads as a guess, and the closing line names the first and last times.
- **Care for South Asian scripts and the keyboard.** A 566-character mixed Bengali/English caption with 15 Bengali tags rendered cleanly in light, dark and at 390px. Enter on a card moves focus to the caption with one announcement, and "Skip to scheduling" jumps past 75 tab stops.

## Priority Issues

**[P1] Review is still buried, and neither view shows a whole post**
- **What:**
  - After generating, the panel stacks the Generate row, the open planner (about 210px), the Schedule row and the finished run (about 130px) before the editor.
  - The planner never closes, even after scheduling.
  - Grid captions are one line.
  - The List view stays in the left column and hides hashtags and the alternative caption.
  - Before captions exist, the editor shows its full form.
- **Why it matters:** review is where the owner spends the most time, and where "AI drafts, the owner decides" happens.
- **Fix:**
  - Close the planner once **Apply** succeeds, or whenever the stage moves past "plan".
  - Shrink the run panel when a post is opened.
  - Let the List view use the full workspace width, with read-only hashtag pills on each row.
  - Clamp grid captions to two lines.
  - Before generation, show only the image and **Generate content** in the editor.
- **Suggested command:** `/impeccable layout`, then `/impeccable distill`

**[P1] The editor changes posts that are already in Buffer, silently**
- **What:**
  - After scheduling, typing in the caption changed the gallery card and the saved draft, but not Buffer, and the chip still said "Scheduled".
  - The note "Changes made here do not update Buffer" appears only in Live mode (`static/js/ui/editor.js:99`).
  - The List view locks the same post; the editor doesn't.
- **Why it matters:** this breaks "show the true state". The owner comes back later, reads the edited caption, and believes that's what will publish.
- **Fix:** make the editor read-only for scheduled posts, as the List view already is. Show the note in both modes, with a button that opens that post's edit dialog.
- **Suggested command:** `/impeccable harden`

**[P2] Scheduled posts doesn't read as "the batch I just sent"**
- **What:**
  - There's one row per channel copy: 12 rows for 6 posts, 50 for 25, in a 460px scroll box.
  - **View in Scheduled posts** lands on All, newest first.
  - Demo rows show a green "Scheduled" plus a separate "Demo" chip.
  - The batch line counts channel copies.
  - The edit and delete dialogs say "from Buffer" in Demo.
- **Why it matters:** this is the closing moment, the one people remember.
- **Fix:**
  - Group rows by post, with a channel dot for each copy and its actions inside.
  - Make **View** open Upcoming, soonest first, and highlight the posts just created.
  - Use the "Scheduled · Demo" chip in Demo.
  - Have the batch line count posts: "6 upcoming (12 channel posts)".
  - Say "Demo mode, not in Buffer" in the dialogs.
- **Suggested command:** `/impeccable clarify`

**[P2] Screen-reader and high-contrast gaps during runs**
- **What:**
  - The activity log is a live region rebuilt in full on every entry (`static/js/ui/log.js:45`), so a 25-image run floods the screen reader.
  - In the List view, Alt+↓ changes the editor's post but leaves the caret in the old row.
  - Reordering isn't announced.
  - High-contrast mode has no style for the stage's indigo button, so the next action can't be seen.
  - Dark field borders on Tray Grey measure 2.99:1.
- **Why it matters:** PRODUCT.md commits to keyboard and screen-reader support.
- **Fix:**
  - Add log entries one at a time, or drop `role="log"` and announce only run summaries.
  - In the List view, Alt+↑/↓ moves the caret to the next row's caption.
  - Announce reorders.
  - In high-contrast mode, give the primary button a Highlight fill or border.
  - Raise the dark field edge slightly (about `#606a7c`).
- **Suggested command:** `/impeccable harden`

**[P3] Toasts cover content and repeat the closing line**
- **What:** up to four toasts stack up. At 1181px and wider they sit over the gallery rows being reviewed and over Scheduled posts after **View**. On phones they cover the closing line while saying less than it does.
- **Fix:** skip the success toast when the progress panel already says it, show at most two toasts, and don't repeat on phones what the panel says.
- **Suggested command:** `/impeccable quieter`

## Persona Red Flags

**Alex (impatient power user):**
- One-line grid captions and no hashtags in the List view mean 25 posts can't be skimmed.
- No shortcut for the stage's main action.
- The planner stays open after use.
- Deleting from both channels takes two dialogs.
- **View** shows the last post first.
- Stacked toasts sit over the cards being reviewed.

**Sam (keyboard only, screen reader, low vision):**
- The log floods the screen reader during runs.
- In the List view, Alt+↓ moves the editor away from the caret.
- Reorders are silent.
- The stage's main button is invisible in high-contrast mode.
- At 200% zoom (about 720px wide), the editor drops below the whole gallery.
- "Skip to scheduling" appears only on keyboard focus at desktop widths.

**The owner writing Bengali mixed with English:**
- Grid cards show about three words.
- The List caption cuts its last visible line in half at the 8-line cap.
- `lang` applies to a whole field, so the English inside a Bengali caption gets the Bengali voice.
- On phones the hashtag placeholder is clipped ("…press Ente").
- Demo only writes English captions, so a mixed-script draft can't be rehearsed.

## Minor Observations

- **Dates:** the log ("03:33:33 pm") and the posts footer ("3:33 pm") still use their own formats.
- **Live switch dialog:** it shows the alert triangle and focuses **Switch to Live**.
- **Same colour for two states:** in Demo, "Ready" and "Scheduled · Demo" share the same indigo wash, so a card doesn't visibly change when it's scheduled.
- **Phone:** about 700px of file details and buttons sit above the caption.
- **Activity log:** 25 green "AI" tags for one run.
- **Scheduled post text:** clamped to two lines, which hides most hashtags.

## Questions to Consider

- If review is the job, should the List view become the main surface after generation, with the editor as a drawer?
- Should a post that's already in Buffer be editable anywhere except Buffer's edit dialog, or should Poster's editor update Buffer?
- Is a channel copy a "post"? The owner thinks in images, Buffer in channel posts. Which unit should the batch line, Scheduled posts and Delete use?
- Should the planner know the owner's day, so 25 hourly posts don't run through the night?
