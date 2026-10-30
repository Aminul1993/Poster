---
target: templates/index.html
total_score: 28
max_score: 40
na_heuristics: 
p0_count: 0
p1_count: 2
target_identity: "file:C:\\projects\\test\\Poster\\templates\\index.html"
target_fingerprint: "sha256:3c76e61530366a341677406bcfbee9ba50a19585c111f8fa2ebc850331b4f7d5"
target_path: "C:\\projects\\test\\Poster\\templates\\index.html"
timestamp: 2026-10-29T12-49-41Z
slug: templates-index-html
---
Method: two separate reviewers working in parallel. A did a fresh design review by hand without seeing the earlier reports. B ran the automated design checker on the source and on nine page states. Both used Poster in Demo mode; neither saw the other's results.

## Design Health Score

| # | Heuristic | Score | Key Issue |
|---|-----------|-------|-----------|
| 1 | Visibility of System Status | 3 | A post removed from Buffer drops out of every count in the batch line and out of "Next to fix". |
| 2 | Match System / Real World | 3 | Date fields show "30-10-2026 09:00" next to hints reading "Fri, 30 Oct, 9:00 am". |
| 3 | User Control and Freedom | 3 | An edit made in Scheduled posts is lost if that post is later deleted: the card goes back to the old draft. A rejected hashtag is wiped from the input. |
| 4 | Consistency and Standards | 2 | Hashtag pills in the editor versus one raw textarea in the Scheduled-post dialog. The "Removed" copy says to give the post a new time, but **Schedule post** reschedules it at the old time. In Scheduled-post rows, the Tab order differs from what's on screen. |
| 5 | Error Prevention | 3 | The planner ignores times other posts already have: two posts at 10:00 am, and post 1 publishing after post 2. |
| 6 | Recognition Rather Than Recall | 3 | Scheduled-post rows show no file name or gallery number, so you match thumbnails by eye. |
| 7 | Flexibility and Efficiency | 3 | Good shortcuts and the List view. 39 Tab presses to reach **All images** with 10 images. No batch edit of tags or category. |
| 8 | Aesthetic and Minimalist Design | 3 | Three scroll areas inside each other: the page, the pinned panel, and the preview box. |
| 9 | Error Recovery | 3 | Dialogs name the posts behind each reason. "The maximum is 15 hashtags" stays red on a valid 15/15. |
| 10 | Help and Documentation | 2 | Inline hints only; no reference for the shortcuts. |
| **Total** | | **28/40** | **Good** |

## Design Specificity Verdict

**Design review:** the behaviour and the words belong to this product: the batch line counts posts per image, Demo is honest, the closing line names the first and last times, the planner warns about overnight posts, the locked editor mirrors Buffer, and Bengali gets `lang` on each run of words. The look is still the category's standard kit. "The Dispatch Desk" names a metaphor that nothing visual draws on, but that's acceptable for a calm tool.

**Automated check:**
- **Rendered page:** the same two rules in all nine states.

  | State | "Box inside a card" warnings | Advisory |
  |---|---|---|
  | Empty page (light or dark) | 1 | 1 |
  | 6 images, not generated | 7 | 1 |
  | Populated, with the locked editor open | 9–10 | 1 |
  | List view | 4 | 1 |
  | Phone | 10 | 1 |
  | High-contrast mode | 9 | 1 |

  Every flagged element is one DESIGN.md documents: the dropzone, gallery cards, progress panel, result note, the hashtag field (an input, so a false positive) and the post preview. The advisory is the intended "flat type hierarchy".
- **Contrast:** all 38 colour pairs pass. The closest are the field borders on Well Grey at 3.07:1 (light) and 3.06:1 (dark).
- **Source scans:** one advisory each, both false positives: the checker can't follow the Jinja stylesheet link, and the `#000` is only an input to the avatar colour mix.
- **What only the design review found:** in the locked editor, the 60% fading makes hashtags 3.21:1 at 13px, below the 4.5:1 that text needs.

**Visual overlays:** none. The browser runs were headless, so no page with highlighted issues was shown to you.

## Overall Impression

The score is back to Good at 28/40. The editor regression and the mirror are fixed, and the checker finds only patterns DESIGN.md allows. What's left is in two areas. One is keyboard focus: after every stage action and every delete, focus falls back to the top of the page. The other is edge-case truth after scheduling: removed posts drop out of the counts, an edit is lost on delete, the planner causes clashes, and the delete toast is misleading. The happy path is solid; the edges are now where trust is earned or lost.

## What's Working

- **True-state copy.** Posts are counted per image with channel copies in brackets, "(not in Buffer)" appears everywhere in Demo, and the locked editor reflects a Buffer edit.
- **One indigo button per stage**, verified at each step. The planner opens by itself, and the closing line includes the overnight count.
- **South Asian scripts and the keyboard model.** `lang` is set on each run of words. Hashtags with underscores, joiners and Bengali digits are accepted (`#র‍্যাব`, `#কফি২০২৬`). Alt+↑/↓ carries the cursor between List rows, and the hashtags are a single tab stop.

## Priority Issues

**[P1] Keyboard focus is lost at every stage change**
- **What:**
  - Pressing Enter on **All images**, **Apply to all** or **All ready** leaves focus on the page itself, because the button is disabled or the planner folds away.
  - Deleting a scheduled post does the same, because its row disappears.
  - Enter on a card with no caption leaves focus on the card, since the caption field is hidden in that state.
- **Why it matters:** a keyboard or screen-reader user loses their place three times per batch, and again after every delete.
- **Fix:**
  - When a run starts, focus the progress panel's title. When it finishes, focus the next stage's button.
  - After **Apply**, focus **All ready**.
  - After a delete, focus the next row, or the list heading if none are left.
  - On a card with no caption, focus **Generate content**.
- **Suggested command:** `/impeccable harden`

**[P1] Removed and edited posts don't show the true state**
- **What:**
  - (a) Removed posts are missing from the batch line and from **Next to fix**.
  - (b) The copy says to give a removed post a new time, but **Schedule post** reschedules it at the old time, while **All ready** skips it.
  - (c) If you edit a post in Scheduled posts and then delete it, the card and editor go back to the pre-edit draft, so scheduling it again would publish the old text.
  - (d) Deleting one channel copy toasts "The post was deleted".
- **Why it matters:** this breaks "show the true state" exactly where a post's content and existence are at stake.
- **Fix:**
  - Write Buffer edits into the draft itself.
  - Add "· 1 removed from Buffer" to the batch line and count it in **Next to fix**.
  - Make the copy say what **Schedule post** will do ("puts it back at Fri, 9:00 am, or pick a new time").
  - Toast "Instagram copy deleted; LinkedIn copy still scheduled".
- **Suggested command:** `/impeccable clarify`

**[P2] The planner loses scope and creates clashes**
- **What:**
  - Choosing "Set times in the planner" from a Selected run opens a planner that previews and highlights "Apply to all: 10 posts".
  - The planner ignores times other posts already have, so two posts land at 10:00 am and the order breaks.
- **Fix:**
  - When opened from a Selected run, preview and highlight "Apply to selected (3)".
  - Detect clashes in the preview ("2 posts would share 10:00 am") and offer to fit around existing times.
- **Suggested command:** `/impeccable harden`

**[P2] Scheduled posts can't be scanned as a batch**
- **What:**
  - Rows are 160–182px tall inside a 460px scroll box.
  - Rows have no file name or gallery number.
  - In each row, Tab reaches the ✕ that deletes one channel's copy before **Edit**.
- **Fix:**
  - Put the channels on one line ("Instagram · LinkedIn"), with chips only when they differ.
  - Move deleting one channel's copy into the edit dialog.
  - Show "#2 · coffee_latte.jpg" on each row.
  - Let the list grow after **View**.
  - Put **Edit** and **Delete** first in the Tab order.
- **Suggested command:** `/impeccable layout`

**[P3] The review panel is overloaded**
- **What:**
  - Three scroll areas sit inside each other.
  - File / Size / Dimensions take up the action column.
  - **Generate content** and **Write it myself** are split across the two columns.
  - In the List view at 1440px, the panel sits just above the width where the editor stacks, so the fields squeeze.
- **Fix:**
  - Put **Generate content** inside the "No caption yet" note.
  - Fold the file details away.
  - Let the preview grow.
- **Suggested command:** `/impeccable distill`

## Persona Red Flags

**Alex (impatient power user):**
- No shortcut for Generate all or Schedule all.
- No batch edit of tags or category.
- A typed tag rejected at the 15-tag cap is wiped from the input.
- Editing a scheduled post takes three steps.
- **Schedule post** is enabled for a post with no caption.
- The planner clashes.

**Sam (keyboard only, screen reader, low vision):**
- Focus is lost at every stage change and after every delete.
- The destructive ✕ comes first in each scheduled row's Tab order.
- In the locked editor, faded hashtags are 3.21:1.
- Focus on a hashtag circles only the ✕, not the pill.
- **Switch to Live** opens with focus on the button that commits.
- Native date fields don't match the hints.

**The owner writing Bengali mixed with English:**
- Hashtags in the List view are 12.5px, below DESIGN.md's 13px rule.
- The Scheduled-post dialog is one raw textarea that opens scrolled to the end, so the start of a long Bengali caption is hidden.
- At the 15-tag cap, extra Bengali tags vanish.
- Counting code points is never explained.
- There's no language control for generation.

## Minor Observations

- The Scheduled-posts empty state says "No posts in Buffer" in Demo.
- The default timezone shows the old alias "Asia/Calcutta" until the field is changed.
- The "maximum 15" error border stays until the next edit.
- The mode badge stays 28px tall on touch screens.
- The indigo icons on the Settings section headings are in tension with the One Signal Rule.
- The edit dialog lacks the readable date ("Fri, 30 Oct") and the "in 16 hours" that the editor shows.
- At 1440px, toasts can cover Scheduled posts while you're working there.

## Questions to Consider

- Should the List view be the default way to review, with the editor as a drawer?
- Does "a post in Buffer is edited only in its own dialog" cost more than it protects? An editor you can unlock, which saves straight to Buffer, would leave one editing model instead of two.
- After scheduling, should the gallery cards carry Buffer's state and actions, leaving Scheduled posts as a checking view?
- Should the batch become a visible, time-ordered list, the "Dispatch Desk" made visible?
