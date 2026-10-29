---
target: templates/index.html
total_score: 27
max_score: 40
na_heuristics: 
p0_count: 0
p1_count: 3
target_identity: "file:C:\\projects\\test\\Poster\\templates\\index.html"
target_fingerprint: "sha256:41dca7891c1298b46bf900bf5488666098095b870d54645728f675111ae22780"
target_path: "C:\\projects\\test\\Poster\\templates\\index.html"
timestamp: 2026-10-29T08-49-57Z
slug: templates-index-html
---
Method: two separate reviewers working in parallel. A did a fresh design review by hand without seeing the earlier report. B ran the automated design checker on the source and on six page states. Both used Poster in Demo mode; neither saw the other's results.

## Design Health Score

| # | Heuristic | Score | Key Issue |
|---|-----------|-------|-----------|
| 1 | Visibility of System Status | 3 | The batch line, chips and progress are excellent. In Demo, though, it still says "12 upcoming in Buffer" and "updated in Buffer". Retry waits show only in the log. |
| 2 | Match System / Real World | 3 | Plain sentence-case copy. Settings uses technical terms ("Base64", "/api/chat", "GraphQL"), and every scheduled row shows a Post ID. |
| 3 | User Control and Freedom | 3 | Cancel, restoring regenerated content, and focus on Cancel in risky dialogs all work. Restoring works once and only until the tab closes, and removing an image can't be undone. |
| 4 | Consistency and Standards | 2 | "Selected N" counts selected images under Generate but ready posts under Schedule. Which buttons are indigo is fixed in the action rows but changes with state in the editor. Dates appear in three formats. |
| 5 | Error Prevention | 3 | The check before scheduling and the safe dialog focus work well. But the still-indigo "All images" button invites a regeneration after the work is done. |
| 6 | Recognition Rather Than Recall | 3 | Each post's state is shown on its card. Keyboard shortcuts appear only in tooltips, and "Next to fix 5" doesn't say what needs fixing. |
| 7 | Flexibility and Efficiency | 2 | Bulk generate, plan and schedule are strong. But review is one post at a time, there's no bulk edit, and there are no shortcuts for the bulk actions. |
| 8 | Aesthetic and Minimalist Design | 3 | Calm and consistent. But the Add images card stays full size, the finished progress panel lingers, every row shows a Post ID, and log lines are fully green. |
| 9 | Error Recovery | 3 | Errors are specific and listed by post. Nothing says that running All ready again skips posts already scheduled. |
| 10 | Help and Documentation | 2 | Good inline hints. There's no list of keyboard shortcuts and no guide for a first batch. |
| **Total** | | **27/40** | **Acceptable** (one point short of Good) |

## Design Specificity Verdict

**Design review:** the interaction and the copy now clearly belong to this product. The batch line reads like a dispatch slip. The AI panel runs Generate → Planner → Schedule in the order of the work. The planner preview says exactly what will happen ("25 posts, from Thu 4:00 pm to Fri 4:00 pm"). The check before scheduling and the Live confirmation are real product logic. The visual skin is still a standard kit (Untitled UI greys plus Tailwind indigo), which DESIGN.md accepts for a native-feeling tool. What's still missing is the one thing the owner actually produces: a run of publish times. Today it appears only as a 12px line under each thumbnail.

**Automated check:**
- **Source scans:** one advisory each, and both are false positives.
  - The skip link's colour was measured against browser defaults, because the checker can't follow the Jinja stylesheet link.
  - The `#000` in the avatar colour mix is deliberate: it darkens the brand colour so the white initial passes 4.5:1.
- **Rendered page:**

  | State | Findings | Before the fixes |
  |---|---|---|
  | Empty page | 2 | 6 |
  | Populated page | 18 | 52 |
  | Phone width | 24 | 6 (empty page) |
  | Settings drawer and confirm dialog | nothing new inside either | — |

- **What the populated findings are:** almost all are 12 "tiny text" hits on the Post ID lines (11.5px monospace) plus the "box inside a card" warning.
- **Contrast:** every contrast warning from the last run is gone, and every text colour pair passes 4.5:1 in both themes.
- **Where the two reviews agree:** the Post ID line on every scheduled row is noise, and important text is small.
- **What only the design review found:**
  - Input borders measure 1.47:1, below the 3:1 that control edges need.
  - High-contrast mode loses which segmented option is selected, as well as the progress bars.
- **False positives:**
  - "Box inside a card" on the hashtag field and the inline success alert.
  - The source-scan findings caused by the missing stylesheet.
- **Ignores:** the two ignores scoped to `templates/index.html` apply only to source scans. Rendered scans file their findings under the page URL, so "flat type hierarchy" still appears there as an advisory.
- **Doc mismatch:** the checker measured the main body text at 12.5–13px, while DESIGN.md says Body is 14px.

**Visual overlays:** none. The browser runs were headless, so no page with highlighted issues was shown to you.

## Overall Impression

The earlier P1 problems are gone. "Ready" means ready, the check before scheduling turns a would-be mass failure into a decision, the switch to Live is guarded, and Bengali is handled as normal content. The score moved only one point, because this review went deeper: high-contrast mode, field borders, and how review feels at 25 posts. The biggest opportunity now is to have the page always point at the next step, and to make reviewing 25 captions a single read instead of 25 round trips.

## What's Working

- **A check before every bulk send.** "2 posts can't be scheduled yet" with "Set times in the planner" as the safe default, and destructive dialogs open on Cancel. "AI drafts, the owner decides" is now part of the interaction itself.
- **The batch state is readable at any moment.** The batch line counts work, never performance, and its amber "need a publish time" uses both colour and words. The chip vocabulary is consistent, and the planner preview states the result before you apply it.
- **South Asian scripts are normal content.** `lang="bn"` is set on the caption, the preview, the gallery caption and each hashtag. Conjunct hashtags (`#কক্সবাজার`) are accepted, duplicates are detected, and truncation never splits a character.

## Priority Issues

**[P1] The indigo "act here" button doesn't move with the work**
- **What:**
  - **All images** and **All ready** are always the indigo buttons (`templates/index.html:258`, `templates/index.html:297`), while the planner's **Apply to all** is a default button.
  - After generating, the only indigo button points at the step just finished.
  - After planning there are two indigo buttons, plus the editor's **Schedule post**.
  - After scheduling, the only indigo button left is "All images", which means regenerate.
  - The run ends on a toast and a stale green panel.
- **Why it matters:** in a working tool, the main button answers "what now?". Here it gives the wrong answer at three of the four stages, and the ending, the part people remember, is weak. DESIGN.md's One Signal Rule lists these buttons by name, so the rule itself is part of the problem.
- **Fix:**
  - Have exactly one indigo button in the AI panel, chosen by stage: **All images** while captions are missing, **Apply to all** while posts need a time, **All ready** while posts are ready.
  - When the panel's button is indigo, the editor's **Schedule post** stays a default button.
  - When a run finishes, replace the panel with "6 posts queued · first Thu 4:00 pm, last 9:00 pm · View in Scheduled posts".
  - Change the rule in DESIGN.md to "one next action per stage".
- **Suggested command:** `/impeccable layout`

**[P1] Review is buried, covered and one post at a time**
- **What:**
  - At 1440×1000 the caption field starts around 817px down the page.
  - Above it, Add images stays full size after use, and the finished progress panel stays in the AI panel.
  - Toasts (bottom right, 390px wide, 4.5–9 seconds) sit on top of the editor's fields in nearly every screenshot.
  - The caption box is 4 rows, so a long Bengali caption scrolls inside a scrolling panel.
  - Reviewing 25 posts means opening 25 cards.
- **Why it matters:** PRODUCT.md's success test is that the owner doesn't rewrite the AI's work post by post. Reading that work is the core task, and right now it's the hardest part to reach.
- **Fix:**
  - Collapse Add images to a single line ("6 of 25 · Add more") once the gallery has images.
  - Collapse the finished progress panel to one line once the next step starts.
  - Move toasts to the bottom left while the AI panel is sticky.
  - Let the caption box grow to about 10 lines.
  - Consider a "List" view of the gallery: one row per post with the full caption, tag count, time and status.
- **Suggested command:** `/impeccable layout`

**[P1] Gaps for keyboard and low-vision users that hide the key choice**
- **What:**
  - In high-contrast (forced colours) mode, segmented controls lose their selected state, because selection is shown only by background and shadow (`static/css/poster.css:542`). That includes "Publish with: Caption | Alternative caption", which decides what gets posted.
  - Progress bars and the active gallery card also disappear in that mode.
  - Input borders measure 1.47:1, below the 3:1 that control edges need.
  - A full gallery has about 125 tab stops before the AI panel, and the panel has no sub-headings to jump between.
- **Why it matters:** PRODUCT.md commits to keeping keyboard and screen-reader support. In high contrast, the user can't see which caption will be posted.
- **Fix:**
  - Add a forced-colours block: an outline in the system highlight colour on the checked segment and the active card, and highlight-coloured progress bars.
  - Give field borders a token of at least 3:1 (about `#868e9e` light, `#5d6678` dark), keeping the hairline for cards.
  - Make the gallery a single tab stop, with arrow keys to move between cards.
  - Add headings in the AI panel: Generate, Schedule planner, Schedule, Post editor.
- **Suggested command:** `/impeccable harden`

**[P2] Demo mode still overstates what happened**
- **What:**
  - In Demo, the batch line says "upcoming in Buffer".
  - Toasts say "Posts scheduled", "The post was updated in Buffer." and "…deleted from Buffer." (`static/js/ui/posts.js:221`, `static/js/ui/posts.js:246`).
  - Chips show the same green "Scheduled" as in Live.
  - Successful log lines are coloured success-green, including "estimated engagement 79/100" (`static/css/poster.css:610`). That breaks DESIGN.md's own estimate rule.
- **Why it matters:** this conflicts with PRODUCT.md's "never imply a post was scheduled when it wasn't".
- **Fix:**
  - Batch line: "simulated (Demo)".
  - Toasts: "Scheduled 6 posts in Demo mode (not in Buffer)".
  - Chip: "Scheduled · Demo".
  - Log lines: colour only the left edge and the tag, not the message text.
- **Suggested command:** `/impeccable clarify`

**[P2] Counts and labels that mean two things**
- **What:**
  - With 2 images selected and none ready, Schedule shows an enabled "Selected 0" that acts on 2 (`static/js/ui/operations.js:28`).
  - "Next to fix 5" says nothing about what's wrong.
  - A screen reader hears two "Selected N" buttons with no verb.
- **Fix:**
  - Show "Selected · 0 of 2 ready".
  - Write "Next to fix: 3 need a time, 2 no caption".
  - Give the buttons spoken names that start with the action, such as "Schedule selected (0 ready)".
- **Suggested command:** `/impeccable clarify`

## Persona Red Flags

**Alex (impatient power user):**
- No bulk edit for selected posts (category, append a hashtag, publish with the alternative caption).
- Review is one card at a time.
- No shortcut for Generate all, Apply to all or Schedule all, and Alt+↑/↓ is documented only in tooltips.
- Toasts cover the fields, so Alex has to wait them out or close them.
- 25 images on 2 channels make 50 Scheduled-posts rows, each with a Post ID line.

**Sam (keyboard only, screen reader, low vision):**
- About 125 tab stops across the gallery.
- In high-contrast mode the "Publish with" choice is invisible.
- Input borders measure 1.47:1, and much important text is 11.5–12.5px.
- Alt+↓ announces only "3 of 6", not the file or its status.
- The only description of the image is "Preview of IMG_2041.jpg", so Sam can't check the caption against the photo.
- The progress line is rewritten every second inside a live region (not confirmed with a real screen reader).

**The owner writing Bengali mixed with English:**
- The gallery caption is 12.5px, below DESIGN.md's own 13px rule.
- The 4-row caption box is cramped.
- `lang` is set only when one script makes up more than half the letters, so captions that are mostly English get no `bn`.
- There's no language setting for generation, only free-text extra instructions.
- **Edit scheduled post** treats hashtags as plain text.

## Minor Observations

- On the empty page, the disabled indigo buttons look lavender and "almost ready".
- Cancel on the progress panel is outlined red, but cancelling generation isn't destructive.
- Dates appear in three formats.
- After deleting one of a post's two channel copies, its chip still says "Scheduled" rather than "Partly scheduled".
- Deleting a scheduled post uses a calendar-✕ icon, while removing an image uses a trash can.
- The middot separators in the batch line are read aloud as text.
- The Live confirmation focuses **Switch to Live**. Nothing posts without a second confirmation, but Enter twice switches mode.
- On phones, Generate and Schedule sit below the whole gallery (13 rows for 25 images), and 107 tap targets are smaller than 44px. There's no sideways scrolling at 390px or 320px.
- DESIGN.md says body text is 14px, but most body text on the page is 12.5–13px. Update one or the other.

## Questions to Consider

- If the batch is the unit of work, why is review the only step done one post at a time?
- Is the One Signal Rule "one indigo button per group", or "one next action on the screen"? Only the second tells the owner where they are.
- Should the dashboard reshape itself by stage (collect → review → dispatch) instead of always showing all five cards at full size?
- Should post language and image alt text be outputs of generation, next to tone?
