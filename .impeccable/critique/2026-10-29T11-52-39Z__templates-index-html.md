---
target: templates/index.html
total_score: 26
max_score: 40
na_heuristics: 
p0_count: 0
p1_count: 3
target_identity: "file:C:\\projects\\test\\Poster\\templates\\index.html"
target_fingerprint: "sha256:a4a53c25bf426f13928af191d690ed15f87533522acdd00a59aa35b9602b2a43"
target_path: "C:\\projects\\test\\Poster\\templates\\index.html"
timestamp: 2026-10-29T11-52-39Z
slug: templates-index-html
---
Method: two separate reviewers working in parallel. A did a fresh design review by hand without seeing the earlier reports. B ran the automated design checker on the source and on nine page states. Both used Poster in Demo mode; neither saw the other's results.

## Design Health Score

| # | Heuristic | Score | Key Issue |
|---|-----------|-------|-----------|
| 1 | Visibility of System Status | 3 | Strong batch line, chips and progress. But after an edit in Scheduled posts, the card, List row and editor keep the old text and time. |
| 2 | Match System / Real World | 3 | Counting one post per image is right. But deleting one channel copy is labelled "Delete post", and "Selected 1 of 3 ready" is jargon. |
| 3 | User Control and Freedom | 2 | The locked editor's only way out, **Edit in Scheduled posts**, is disabled. Deleting a post quietly makes it "Ready" again. |
| 4 | Consistency and Standards | 2 | Hashtag pills in the editor, but raw text in the Buffer edit dialog. A Demo log line says "deleted from Buffer". DESIGN.md contradicts itself on **Schedule post**. |
| 5 | Error Prevention | 2 | **Schedule again** confirms with focus on the button that creates a duplicate. The default plan puts posts overnight. |
| 6 | Recognition Rather Than Recall | 3 | Counts on every button. Shortcuts appear only in tooltips, and the reorder hint is hidden in the List view. |
| 7 | Flexibility and Efficiency | 3 | Planner, List view, Next to fix and shortcuts are good. There's no batch edit of tags, category, tone or channels; tone and channels live in Settings. |
| 8 | Aesthetic and Minimalist Design | 3 | Calm, but five regions stack above the editor, and "Demo" is said four or five times. |
| 9 | Error Recovery | 3 | The "can't be scheduled yet" dialog names each post and its reason. Live failures weren't exercised. |
| 10 | Help and Documentation | 2 | Good inline hints, but no shortcut reference, and "Partly scheduled" and "channel posts" are never explained. |
| **Total** | | **26/40** | **Acceptable** |

## Design Specificity Verdict

**Design review:** the behaviour belongs only to Poster: the batch line read like a dispatch slip, one indigo button per stage, one post per image, `lang` set from each script, the overnight warning, and the estimate disclaimer. The visual skin is still generic, which DESIGN.md chose on purpose. Nothing visual says "dispatch desk", but nothing drifts into the looks you ruled out either.

**Automated check:**
- **Rendered page:**

  | State | Warnings | Advisory |
  |---|---|---|
  | Populated | 10 | 1 |
  | List view | 4 | 1 |
  | Empty page | 1 | 1 |

  The warnings are all "box inside a card": the six gallery cards, the progress panel, the hashtag field, the post preview and the result note. All of them are sanctioned by DESIGN.md, except the hashtag field, which is a false positive. The advisory is the intended "flat type hierarchy".
- **Nothing new in the other states:** dark, phone, high contrast, Settings and the post-View highlight.
- **Contrast:** every text pair passes. The one near miss is the light field border on Well Grey at 2.99:1. Fields have a white background, so the border still reads 3.29:1 against the field itself.
- **Source scans:** one advisory each, both false positives (the unread Jinja stylesheet, and the `#000` in the avatar colour mix).
- **Transient finding:** a "text hidden behind other text" warning on a toast appeared once and didn't reproduce.
- **Where the reviews agree:** no contrast or colour problems remain. What's left is behaviour, which only the design review can see.

**Visual overlays:** none. The browser runs were headless, so no page with highlighted issues was shown to you.

## Overall Impression

The score dropped from 28 to 26. Part of that is a regression I introduced: the editor lock disables its own way out. The rest is that this reviewer pushed into what happens after scheduling: editing in Buffer, deleting, and scheduling again. Those flows are where the truth drifts. Edits don't come back to the batch, deleted posts re-arm as "Ready", and **Schedule again** defaults to creating a duplicate. Before scheduling, Poster is now strong. After scheduling, it still treats the batch as a draft rather than as a record of what Buffer holds.

## What's Working

- **The One Signal Rule is implemented faithfully.** Exactly one indigo button at each stage: **All images**, then **Apply to all**, then **All ready**, then none. In high-contrast mode it fills with Highlight.
- **South Asian scripts are first-class.** `lang="bn"` is set on the caption, the List view, tag pills and scheduled rows. র‍্যালি keeps its ZWJ, a 41-character Bengali tag wraps cleanly, and the List textarea shows the whole caption.
- **Honest and safe defaults, plus a keyboard model that works.** Destructive dialogs open on Cancel, and the pre-flight dialog names each post. Enter on a card focuses its caption. Alt+↑/↓ moves between posts in the editor and between rows in the List view, keeping the caret on the same field. Opening a post announces "dog-park.jpg, Needs time, post 4 of 10".

## Priority Issues

**[P1] The locked editor blocks its own way out and invites duplicates**
- **What:**
  - **Edit in Scheduled posts** sits inside the disabled fieldset, so it's always disabled.
  - **Regenerate content** and **Schedule again** stay enabled.
  - "Schedule this post again?" focuses **Schedule again**, so Enter creates a duplicate post in Buffer.
  - The lock note sits below the fold.
  - For a keyboard user, Enter on a scheduled card tries to focus a disabled textarea, so focus stays on the card.
- **Why it matters:** this breaks PRODUCT.md's "no post … duplicated" and DESIGN.md's own spec for the editor.
- **Fix:**
  - Move the result note out of the fieldset to the top of the editor ("In Buffer · Sun 1 Nov 4:30 pm · Edit in Scheduled posts").
  - Hide **Schedule again** for posts already in Buffer, or make its confirm the danger variant with Cancel focused.
  - Lock **Regenerate** too.
  - Move keyboard focus to the result's button when the post is locked.
- **Suggested command:** `/impeccable harden`

**[P1] Edits made in Scheduled posts never come back to the batch**
- **What:** only `result.dueAt` is updated (`static/js/feed.js:111`). After editing a post's text and time there:
  - the gallery card kept the old caption and time;
  - the editor's **Publish at** said "in 16 hours" while its own result line said "Sun 1 Nov";
  - the post preview showed the old text.
- **Why it matters:** this breaks "show the true state" on the main overview.
- **Fix:** after a successful edit, write the time back to `scheduleLocal`, store Buffer's text on the result, and show that on cards, List rows and the preview with an "Edited in Buffer" note.
- **Suggested command:** `/impeccable harden`

**[P1] Review is still buried, and the default plan warns about itself**
- **What:**
  - After generating, the editor starts around 808px down a 1000px window: the planner (about 230px), the Schedule row and the full "Content generation complete" panel sit above it, with "Finished in 2s" and "Elapsed 2s" both shown.
  - The planner defaults to the next hour, every hour, so 10 posts give "6 of them land between 11 pm and 6 am", while **Apply to all** is the indigo button.
- **Fix:**
  - Shrink the generation panel when the planner opens on its own.
  - Hide the amber "Plan times" link while the planner is open.
  - Default the first post to tomorrow morning, or to the time of day used last.
  - Repeat any overnight count in the Live confirmation and the closing line.
- **Suggested command:** `/impeccable layout`

**[P2] The delete flows mislabel their scope and re-arm deleted posts**
- **What:**
  - Deleting one channel copy says "Delete post", while deleting the whole post says "Delete from 2 channels".
  - After deleting a whole post, its image goes back to "Ready", the batch line counts it, and **All ready** lights up indigo, pointing at re-sending what you just deleted.
- **Fix:**
  - One copy: "Delete the Instagram copy?" / **Delete Instagram copy**. Whole post: **Delete post (2 channels)**.
  - Mark the image "Removed from Buffer" (neutral), and leave it out of **All ready** until you plan it again.
- **Suggested command:** `/impeccable clarify`

**[P2] The keyboard and screen-reader cost of the editor**
- **What:**
  - Every hashtag's ✕ is a tab stop: about 22 Tab presses from the caption to **Publish at**.
  - Each List row has 6 tab stops.
  - The locked state is shown only as 60% opacity.
  - `lang` applies to a whole element at a 50% threshold, so a Bengali post's mostly-English tag line was marked `bn`.
- **Fix:**
  - Make the hashtag list a single tab stop, with the arrow keys to move and Delete to remove.
  - Add a visible "Locked: in Buffer" line at the top of the form.
  - Mark runs of each script with their own `lang` in the preview and in scheduled rows.
- **Suggested command:** `/impeccable harden`

## Persona Red Flags

**Alex (impatient power user):**
- He has to scroll the panel to reach the editor on every post.
- No batch edit of tags, category, tone or channels.
- With 10 posts, Scheduled posts is 1,796px inside a 460px scrolling box.
- No "start next batch".

**Sam (keyboard only, screen reader, low vision):**
- The hashtag ✕ buttons are tab stops.
- Enter on a scheduled card leaves focus on the card, and the lock explanation and its disabled button come last.
- Disabled "Selected 0" and "All ready 0" measure 2.6:1 but carry state.
- The reorder hint is hidden in the List view.
- Mixed-script `lang`.

**The owner writing Bengali mixed with English:**
- The hashtag error says "use letters, numbers or _", which reads as Latin only.
- The character counter counts code points, so conjuncts inflate it, despite the docstring promising "user-perceived characters".
- Captions that are mostly English get no `bn`.
- The edit dialog treats hashtags as raw text.
- On phones in the List view, the checkbox and the order number cover most of the 72px photo, leaving a 72×28px strip to tap.

## Minor Observations

- "Loading posts from Buffer…" appears in Demo.
- After **View**, every row is highlighted when every row is new, which carries no information.
- The "Drag cards to reorder" hint stays after the batch is done.
- Settings' "Default planner interval (minutes)" duplicates the planner's remembered interval.
- Date fields show 24-hour "19:00" while previews say "7:00 pm".
- Log lines carry Post IDs and media URLs.
- In high-contrast mode, a finished progress bar stays green instead of Highlight, because a more specific rule wins.
- On a phone the page is 5,350px tall for 6 images.

## Questions to Consider

- If the batch is the unit of work, why are tone and channels browser-wide settings in a drawer, rather than choices for each batch next to **All images**?
- Once a post is in Buffer, should its card become the Buffer post (live text and time, edited in place) instead of a locked draft that drifts?
- Should the planner offer presets ("Tomorrow, 9 am–9 pm, evenly spaced") instead of a raw start and interval?
- What does "done" look like? Should a finished batch fold into a receipt with **Start next batch**?
