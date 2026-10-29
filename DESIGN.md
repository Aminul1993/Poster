---
name: Poster
description: A calm dispatch desk for turning a batch of photos into scheduled social posts.
colors:
  dispatch-indigo: "#4f46e5"
  dispatch-indigo-deep: "#4338ca"
  indigo-wash: "#eef2ff"
  indigo-ink: "#3730a3"
  desk-grey: "#f5f6f8"
  sheet-white: "#ffffff"
  tray-grey: "#f9fafb"
  well-grey: "#f2f4f7"
  hairline: "#e4e7ec"
  hairline-strong: "#d0d5dd"
  field-edge: "#848c9c"
  ink: "#101828"
  ink-soft: "#344054"
  muted-slate: "#636d82"
  faint-slate: "#737b8c"
  success-green: "#067647"
  success-wash: "#ecfdf3"
  caution-amber: "#b54708"
  caution-wash: "#fffaeb"
  alert-red: "#c4261b"
  alert-wash: "#fef3f2"
  notice-blue: "#175cd3"
  notice-wash: "#eff8ff"
  night-desk: "#0b0d12"
  night-sheet: "#13161d"
  night-indigo: "#818cf8"
typography:
  title:
    fontFamily: "-apple-system, BlinkMacSystemFont, 'Segoe UI', Inter, Roboto, 'Helvetica Neue', Arial, sans-serif"
    fontSize: "15px"
    fontWeight: 650
    lineHeight: 1.5
    letterSpacing: "-0.005em"
  body:
    fontFamily: "-apple-system, BlinkMacSystemFont, 'Segoe UI', Inter, Roboto, 'Helvetica Neue', Arial, sans-serif"
    fontSize: "14px"
    fontWeight: 400
    lineHeight: 1.5
  post:
    fontFamily: "-apple-system, BlinkMacSystemFont, 'Segoe UI', Inter, Roboto, 'Helvetica Neue', Arial, sans-serif"
    fontSize: "14px"
    fontWeight: 400
    lineHeight: 1.6
  body-sm:
    fontFamily: "-apple-system, BlinkMacSystemFont, 'Segoe UI', Inter, Roboto, 'Helvetica Neue', Arial, sans-serif"
    fontSize: "13px"
    fontWeight: 400
    lineHeight: 1.55
  label:
    fontFamily: "-apple-system, BlinkMacSystemFont, 'Segoe UI', Inter, Roboto, 'Helvetica Neue', Arial, sans-serif"
    fontSize: "12.5px"
    fontWeight: 650
    lineHeight: 1.5
  overline:
    fontFamily: "-apple-system, BlinkMacSystemFont, 'Segoe UI', Inter, Roboto, 'Helvetica Neue', Arial, sans-serif"
    fontSize: "11.5px"
    fontWeight: 700
    lineHeight: 1.5
    letterSpacing: "0.05em"
  mono:
    fontFamily: "ui-monospace, SFMono-Regular, 'Cascadia Code', Menlo, Consolas, monospace"
    fontSize: "11.5px"
    fontWeight: 400
    lineHeight: 1.5
rounded:
  sm: "6px"
  control: "8px"
  base: "10px"
  panel: "12px"
  lg: "14px"
  dialog: "16px"
  pill: "999px"
spacing:
  field: "6px"
  control-gap: "8px"
  row: "14px"
  card-x: "20px"
  section: "20px"
  page-x: "24px"
components:
  button:
    backgroundColor: "{colors.sheet-white}"
    textColor: "{colors.ink-soft}"
    typography: "{typography.label}"
    rounded: "{rounded.control}"
    padding: "0 14px"
    height: "36px"
  button-hover:
    backgroundColor: "{colors.well-grey}"
    textColor: "{colors.ink}"
  button-primary:
    backgroundColor: "{colors.dispatch-indigo}"
    textColor: "{colors.sheet-white}"
    rounded: "{rounded.control}"
    padding: "0 14px"
    height: "36px"
  button-primary-hover:
    backgroundColor: "{colors.dispatch-indigo-deep}"
    textColor: "{colors.sheet-white}"
  button-ghost:
    backgroundColor: "transparent"
    textColor: "{colors.muted-slate}"
    rounded: "{rounded.control}"
  button-danger:
    backgroundColor: "{colors.sheet-white}"
    textColor: "{colors.alert-red}"
    rounded: "{rounded.control}"
  button-sm:
    rounded: "7px"
    padding: "0 10px"
    height: "30px"
  button-xs:
    rounded: "{rounded.sm}"
    padding: "0 8px"
    height: "26px"
  input:
    backgroundColor: "{colors.sheet-white}"
    textColor: "{colors.ink}"
    typography: "{typography.body}"
    rounded: "{rounded.control}"
    padding: "8px 12px"
    height: "38px"
  card:
    backgroundColor: "{colors.sheet-white}"
    rounded: "{rounded.lg}"
    padding: "20px"
  batch-stat:
    textColor: "{colors.muted-slate}"
    typography: "{typography.body-sm}"
  mode-badge-demo:
    backgroundColor: "{colors.indigo-wash}"
    textColor: "{colors.indigo-ink}"
    rounded: "{rounded.pill}"
    height: "28px"
  mode-badge-live:
    backgroundColor: "{colors.success-wash}"
    textColor: "{colors.success-green}"
    rounded: "{rounded.pill}"
    height: "28px"
  service-pill:
    backgroundColor: "transparent"
    textColor: "{colors.ink-soft}"
    rounded: "{rounded.pill}"
    padding: "0 6px"
    height: "28px"
  chip-success:
    backgroundColor: "{colors.success-wash}"
    textColor: "{colors.success-green}"
    rounded: "{rounded.pill}"
    padding: "0 8px"
    height: "22px"
  chip-warning:
    backgroundColor: "{colors.caution-wash}"
    textColor: "{colors.caution-amber}"
    rounded: "{rounded.pill}"
    padding: "0 8px"
    height: "22px"
  chip-danger:
    backgroundColor: "{colors.alert-wash}"
    textColor: "{colors.alert-red}"
    rounded: "{rounded.pill}"
    padding: "0 8px"
    height: "22px"
  hashtag:
    backgroundColor: "{colors.indigo-wash}"
    textColor: "{colors.indigo-ink}"
    typography: "{typography.body-sm}"
    rounded: "{rounded.pill}"
    padding: "0 3px 0 10px"
    height: "26px"
  segmented-selected:
    backgroundColor: "{colors.sheet-white}"
    textColor: "{colors.ink}"
    rounded: "7px"
    padding: "5px 12px"
---

# Design System: Poster

## Overview

**Creative North Star: "The Dispatch Desk"**

Poster is a desk where one person prepares a batch of posts, checks every one, and sends the set off. The design is calm, precise and trustworthy: these posts go out under the owner's name, so nothing on screen may surprise them or overstate itself. Sheet-white cards sit on a pale Desk Grey background, one card per station on the desk: adding images, the gallery, AI content and scheduling, scheduled posts, and the activity log. A single line above the cards reads like a dispatch slip: how many images, how many have captions, how many still need a publish time, how many are ready, and what Buffer holds. Hairlines separate everything, and colour is held back for two jobs. Dispatch Indigo marks the next action, the current selection and focus. Four status colours report what has happened.

The density is that of a working tool, not a landing page. Text is 12–15px, controls are 26–38px tall, and status is visible at all times: the mode badge and service labels in the header, the batch line, counts on every action button, progress on every run. The work runs top to bottom in the order it happens: generate captions, plan the times, schedule. Numbers are exact and use tabular figures. Light and dark themes are both first-class. Every colour is a CSS custom property, redefined for dark under `[data-theme="dark"]` in `static/css/poster.css`.

Poster rejects three looks: growth-hacker SaaS (gradients, glows, purple-to-pink washes, sparkle decoration), copies of the social networks it posts to (Instagram, X, Buffer's own UI), and analytics dashboards (charts, trend arrows, hero metrics that suggest measured performance).

**Key Characteristics:**
- One flat accent, Dispatch Indigo, used only for the next action, selection and focus.
- Four status colours (green, amber, red, blue), each paired with a label or icon.
- Flat cards with 1px hairlines. Real shadows appear only on layers that float.
- The operating system's UI font, small and dense, with tabular numbers everywhere.
- Rounded rectangles that grow with size (6 → 16px), and pills for status.
- No gradients and no coloured glow anywhere.
- Light and dark themes built from the same tokens.

## Colors

The palette is a cool neutral grey family with one indigo accent and four status colours. The greys come from the same scale (ink `#101828` through tray `#f9fafb`). Status colours always come as a trio: a strong colour for text and icons, a pale wash for backgrounds, and a light border.

### Primary
- **Dispatch Indigo**: the action colour. It fills the one main button in each group and the brand mark, draws the focus ring (2px outline, or a 3px ring at 35% alpha on fields), marks the active gallery card and checked options, fills progress bars while work runs, and colours links and checkboxes. In dark theme it becomes **Night Indigo**, with near-black text on top (`--on-accent: #0b0d12`).
- **Dispatch Indigo Deep**: the hover state of primary buttons. Never used at rest.
- **Indigo Wash** / **Indigo Ink**: the soft pairing for things that belong to Poster's own state rather than a status: the Demo mode badge, hashtag pills, the "Ready" chip, selected mode and channel options, dialog icon tiles. (Dark theme: indigo at 14% alpha, with `#c7d2fe` text.)

### Neutral
- **Desk Grey**: the page background behind every card. **Night Desk** in dark theme.
- **Sheet White**: cards, inputs, default buttons, toasts and dialogs. **Night Sheet** in dark theme.
- **Tray Grey**: recessed strips inside a card (the ops bars, the gallery toolbar, the drawer footer, the post preview and server-info boxes).
- **Well Grey**: the deepest neutral fill: segmented-control tracks, count badges, the hover on default and ghost buttons, image placeholders.
- **Hairline** / **Hairline Strong**: 1px card borders and dividers, and the stronger edge on buttons and the dashed dropzone.
- **Field Edge**: the border of every input, select, textarea and the hashtag field. It reads at 3:1 against the surface (WCAG 1.4.11), so an empty field is still visible on every surface, Well Grey included; dark theme uses `#646e80`.
- **Ink**: headings, primary text and the numbers in the batch line. **Ink Soft**: field labels, default button text and secondary copy. **Muted Slate**: hints, sublines, placeholders, timestamps, post IDs and ghost button text; it passes 4.5:1 on every surface, including Indigo Wash.
- **Faint Slate**: icons, grips and status dots only (3:1 or better). It never carries text.

### Status
- **Success Green**: Live mode, a finished run, scheduled posts, a passed connection test.
- **Caution Amber**: posts that need a publish time or whose time has passed, retries, cancelled runs, missing optional server settings.
- **Alert Red**: failures, invalid fields, destructive buttons (outlined, never filled).
- **Notice Blue**: information banners and work in progress (Generating…, Scheduling…).

### Named Rules
**The One Signal Rule.** Dispatch Indigo means "act here" or "this is selected". There is one next action per stage, and only it is filled indigo. In the AI panel that is **All images** while posts lack captions, **Apply to all** while posts need a time, and **All ready** while posts are ready; when the batch is done, nothing in the panel is indigo. The editor's **Schedule post** is never indigo. Dialogs and the Settings drawer each have their own single primary (**Save settings**). Never use indigo as decoration or as a background for a whole region.

**The Status Speaks Twice Rule.** A status colour never stands alone. Chips carry a dot and text, service labels carry a dot and a name, log lines carry a category tag tinted by level (errors also use red text), banners carry an icon and a title, and the batch line's "need a publish time" is words as well as amber. List items never get a coloured side stripe.

**The Flat Colour Rule.** Every fill is one flat colour. No gradients, including on the brand mark and progress bars, and no coloured glow.

**The Estimate Is Not a Metric Rule.** The engagement score is the model's guess. It is shown as plain "89 / 100" text with the words "estimate" and "not a measurement", never with a slider, a colour scale or success-green.

## Typography

**Body Font:** the operating system's UI face: Segoe UI (Variable on Windows 11), San Francisco on Apple, Roboto on Android (`var(--font)`).
**Mono Font:** the system monospace: Cascadia Code, SF Mono, Menlo or Consolas (`var(--mono)`).

**Character:** One family, no display face. It reads like a native desktop tool, so the content (the owner's photos and captions) carries the personality. Hierarchy comes from weight and small steps in size, not from contrast between typefaces.

### Hierarchy
- **Title** (650, 15px, −0.005em): card titles, always paired with an 18px Muted Slate icon. Dialog and drawer headings step up to 16–17px at 700. The brand name is 17px at 700.
- **Body** (400, 14px, 1.5): form values and running text. Most interface text is Label or Body Small, so the page's dominant text measures 12.5–13px; that is deliberate for a dense tool.
- **Post** (400, 14px, 1.6): anything that shows a post's own words: the post preview and scheduled-post text. Caption fields use Body.
- **Body Small** (400, 13–13.5px, 1.5–1.55): the batch line, toasts, banners, empty-state copy (max 360px wide), the planner preview, hashtag pills.
- **Label** (650, 12.5px): field labels, segmented options, small buttons and gallery-card names. Gallery-card captions are 13px, because they are a post's own words. Default buttons use 13.5px at 600.
- **Hint** (400, 12px, Muted Slate): help text under fields, counters, sublines and the gallery card's time-and-tags line.
- **Overline** (700, 11.5px, 0.05em, uppercase): only the GENERATE and SCHEDULE row labels. Log category tags use the same treatment at 11px and 0.04em.
- **Mono** (11.5px, Muted Slate): log timestamps and inline `code`. Buffer post IDs are not shown in rows; they sit in the status chip's tooltip and in the log.

**Dates** have one format everywhere: "Fri, 30 Oct, 4:00 pm", with the year only when it isn't this year. Times of day on their own read "4:00 pm" (the log adds seconds). The timezone is named once beside the fields and in confirmations, not in every date.

### Named Rules
**The System Voice Rule.** Poster ships no web fonts. Its Content Security Policy only allows files from its own server, so a new face would have to be self-hosted and justified. Use `var(--font)` and `var(--mono)`.

**The Tabular Numbers Rule.** Every count, counter, score, position, time and duration uses `font-variant-numeric: tabular-nums`. This covers the batch line, count badges, button counts, character counters, the estimate, "3 of 12", the gallery card's time line and op stats.

**The In-Between Weights Note.** Weights 550, 650 and 750 render exactly only with variable system fonts (Segoe UI Variable, SF Pro). Elsewhere they round to the nearest available weight. That's acceptable, but never rely on 650 versus 700 to carry meaning.

**The South Asian Script Rule.** Captions and hashtags in Bengali, Hindi, Tamil and other South Asian scripts are normal content. Text that shows a post's words is 13px or larger with a line-height of at least 1.5, and never clips vertically. Poster sets `lang` from the script of the text (bn, hi, ta and others), so screen readers use the right voice and the browser picks the right font. The system stack falls back to Nirmala UI on Windows and the Sangam or Kohinoor faces on Apple. Don't set a font stack that blocks those fallbacks. Text is shortened only between whole graphemes, and hashtags keep the zero-width joiner and non-joiner.

## Layout

There's a single page with a sticky header. Content is centred at up to 1480px wide, with 24px side padding (16px on phones) and 20px gaps between all major blocks (16px on phones).

- **Header** (64px; 58px at ≤720px): the brand at the left, then the mode badge and the service labels, then the theme toggle and Settings at the right. It is Sheet White at 86% opacity with a 12px backdrop blur.
- **Banners** stack under the header, then the **batch line**: one row of Body Small stats separated by middots (a two-column grid on phones).
- **Workspace**: two columns at 5fr / 6fr. The left column stacks Add images and the Image gallery; the right holds AI content & scheduling. From 1181px the right panel stays on screen while the page scrolls (sticky 16px below the header, scrolling inside itself), so the editor is always beside the gallery. Below 1180px it becomes one column, and opening a card scrolls the editor into view.
- **AI panel order**: the Generate row, then the Schedule planner (it opens on its own when generation leaves posts without a time), then the Schedule row with the progress panel, then the editor. The order is the order of the work, and each part has a heading (Generate, Schedule planner, Schedule, Post editor).
- **Folding as the batch moves on**: once posts have captions, Add images folds to its header line ("6 of 10 images in this batch" · Add more). The planner folds after Apply and after a scheduling run. A finished run's panel shrinks to its title and closing line once the next step starts or a post is opened. At 1181px and wider, toasts appear bottom left over the gallery, never over the editor.
- **The List view widens the workspace** to 6fr / 5fr. The AI panel is a container: below about 620px wide, the editor stacks its preview above the fields.
- **Lower row**: Scheduled posts and the Activity log side by side (1fr / 1fr), one column below 1180px. Long lists scroll inside their card (max 460px).
- **Cards** have a 16×20px header row separated by a hairline, then a 20px body (16px side padding on phones). Recessed Tray Grey strips (ops, toolbars) run the full width of the card.
- **Gallery**: an auto-fill grid of square thumbnails at least 150px wide (130px on phones), with 14px gaps, or the List view (one row per post). A "Skip to scheduling" link jumps past the cards; it is visible below 1180px and appears on keyboard focus above it.
- **Editor**: a 200px preview column next to the fields (22px gap). At ≤720px the preview sits above the fields.
- **Forms**: fields stack with a 6px gap from label to control. Paired fields sit in a two-column row with 14px gaps, one column at ≤560px.
- **Breakpoints**: 1181px (sticky panel), 1180px (columns collapse), 900px (header tagline and pill details hide), 720px (phone spacing, stacked editor, planner summary hint hides), 560px (single-column forms; service labels hide unless they show an error or warning).

## Elevation & Depth

Depth is flat, with lift reserved for floating layers. Structure comes from 1px hairlines and tonal steps (Desk Grey → Sheet White → Tray Grey → Well Grey). Resting surfaces carry only a whisper of shadow. Shadows grow only on hover, for selection, and for layers that float above the page. In dark theme the same roles use heavier black shadows.

### Shadow Vocabulary
- **Whisper** (`--shadow-sm: 0 1px 2px rgba(16,24,40,.05)`): resting cards, default buttons, the dropzone icon tile.
- **Lift** (`--shadow: 0 1px 3px rgba(16,24,40,.08), 0 1px 2px rgba(16,24,40,.04)`): hovered gallery cards, the selected segmented option, and controls floating over a photo (checkbox, status chip).
- **Float** (`--shadow-lg: 0 20px 40px -12px rgba(16,24,40,.22), 0 4px 10px -4px rgba(16,24,40,.08)`): toasts, the Settings drawer, dialogs.
- **Scrim**: the dialog backdrop is `rgba(10,13,20,.55)` with a 2px blur. Busy overlays on the editor use Sheet White at 78% with a 2px blur.

### Named Rules
**The Hairline First Rule.** Separate things with a 1px Hairline or a tonal step before reaching for a shadow. A shadow at rest never exceeds Whisper, and no shadow is ever coloured. Fields are the exception to the hairline: their edge is Field Edge, because it has to be seen.

## Shapes

Everything is a rounded rectangle, and the radius grows with the size of the object. Small controls and code use 6px. Buttons and inputs use 8px (7px for small buttons and segmented options). Notices, list items and option rows use 10px. Panels inside a card (gallery cards, dropzones, the op-status box, banners, toasts) use 12px. Cards use 14px, and dialogs 16px. Anything that reports a state or a count is a full pill: mode badge, chips, count badges, hashtags. Dots, including channel colour dots, are circles.

- Only `--radius-sm` (6px), `--radius` (10px) and `--radius-lg` (14px) exist as CSS variables. The 8px, 12px and 16px steps are written as literal values.
- A dashed 1.5px Hairline Strong border means "drop files here" and nothing else.
- Icons are stroke icons on a 24px grid, with a 2px stroke and round caps and joins. They come from the inline SVG sprite in `templates/index.html` and are sized 18px by default, 16px in buttons, 15px in small buttons and 14px in extra-small ones.
- The editor preview sits on a 16px checkerboard, so transparent images read as transparent.

## Components

Every component is **quiet until it matters**. Controls are outlined and neutral by default. Indigo fill appears only for the one main action in a group, and red only for something destructive.

### Buttons
- **Shape:** gently rounded (8px), 36px tall with 14px side padding. Small is 30px tall (7px radius), extra-small 26px (6px radius), and icon-only buttons are square.
- **Default:** Sheet White with a Hairline Strong border, Ink Soft text at 13.5px and 600, and the Whisper shadow. Hover moves to Well Grey and Ink. Pressing nudges the button down 1px.
- **Primary:** Dispatch Indigo fill and border with white text. Hover moves to Dispatch Indigo Deep. One per action group (see The One Signal Rule). The editor's **Schedule post** is never primary (see The One Signal Rule).
- **Ghost:** no fill, border or shadow, Muted Slate text, hovering to Well Grey. Used for Dismiss, Remove all, Refresh, Clear, Change, Next to fix, Restore previous content and the theme toggle.
- **Danger:** Sheet White with Alert Red text and a light red border, and an Alert Wash hover. Never filled red.
- **Count:** a small tabular pill inside the label ("Selected 3"), white at 22% on primary buttons.
- **Loading:** the icon is replaced by a 14px spinner in the button's text colour, and clicks are blocked.
- **Disabled:** 50% opacity, no shadow, a `not-allowed` cursor.

### Header status
- **Mode badge:** a 28px pill with an icon. Demo uses Indigo Wash with Indigo Ink; Live uses Success Wash with Success Green and a green border. It's a button: switching to Live asks first, naming the model and the Buffer channels.
- **Service labels** (Ollama, Buffer, Offline): plain text with no fill or border, so they don't read as buttons. A 7px dot with a 3px halo in the state colour (green ok, indigo demo, amber warn, red error), a name and a Muted detail ("Demo"). The error state alone gets an Alert Wash pill with a red border.

### Batch line
One row above the cards: "6 / 10 images · 6 with captions · 2 need a publish time · 4 ready to schedule · 12 upcoming in Buffer (all channels)". Numbers in Ink at 650 and tabular; words in Muted Slate; middot separators in Faint Slate. "Need a publish time" turns Caution Amber, words and number, while it's above zero. It counts the work in the batch; it is not a performance metric.

### Chips
- **Style:** 22px pills with a 6px dot in the text colour, 11.5px at 650. The neutral, accent, info, success, warning and danger variants each use their wash, strong colour and border.
- **Item states:** No content (neutral), Needs time and Time passed (warning), Ready (accent), Generating… and Scheduling… (info), Queued (neutral), Scheduled (success), Scheduled · Demo (success, told apart by its words), Removed from Buffer (neutral: deleted on purpose, out of "All ready" until it gets a new time), Partly scheduled (warning: some channels failed; deleting one channel's copy on purpose is not a failure), Failed (danger). "Ready" always means a caption and a publish time ahead. A post scheduled in Demo mode doesn't count as scheduled once Poster is live.

### Cards / Containers
- **Corner style:** 14px.
- **Background:** Sheet White on Desk Grey, with a 1px Hairline border and the Whisper shadow.
- **Header:** the title with a Muted icon and an optional count badge on the left; actions (ghost buttons, segmented filters) on the right. It wraps on narrow widths.
- **Empty states:** a centred 50px Well Grey icon tile, an Ink title at 650, and up to two lines of Muted copy that say what the card will hold.

### Inputs / Fields
- **Style:** Sheet White, a 1px Hairline Strong border, 8px radius, 38px minimum height, 8×12px padding. Textareas start at 84px and resize vertically. The caret is Dispatch Indigo; placeholders are Muted Slate.
- **Label row:** a 12.5px/650 label on the left with a counter or small ghost action on the right. An "optional" marker sits in Muted Slate at 11.5px.
- **Focus:** the border turns Dispatch Indigo, plus a 3px ring at 35% alpha (`--focus-ring`). Other focusable elements get a 2px indigo outline offset by 2px.
- **Error:** an Alert Red border, a red-wash focus ring, and a 12px red message under the field. Counters turn red when over the limit.
- **Disabled:** 60% opacity.
- **Timezone:** a text field with suggestions (type "Dhaka" to find Asia/Dhaka) rather than a 400-item list.

### Segmented control
A Well Grey track (3px padding, 9px radius) holding options at 12.5px/600 in Muted Slate. The selected option becomes a Sheet White pill with Ink text and the Lift shadow. It's used for image source, caption choice and the scheduled-posts filter, and is built on real radio inputs.

### Hashtag editor
The field holds removable pills (Indigo Wash with Indigo Ink, at least 26px tall, 13px, a 20px round ✕) followed by a borderless text input. It is one tab stop: ← from the empty input reaches the pills, the arrows move between them, Delete removes one, and → or Esc returns to the input. The whole box takes the field focus ring when anything inside it has focus.

### Dropzone
Tray Grey with a dashed 1.5px Hairline Strong border, 12px radius, and a centred white icon tile with Dispatch Indigo. Hovering turns the dashes indigo. Dragging over fills it with Indigo Wash at 1.005 scale. A compact horizontal version is used for the single photo in Edit with AI.

### Gallery card
A square photo (`object-fit: cover`) with a white checkbox tile at top left, a dark translucent order number at top right, and a status chip at bottom left. Below sit the file name, the caption's first line (italic Muted "No caption yet" when empty) and a Muted line with the publish time and hashtag count, then a row of reorder controls with a grip. The active card gets a Dispatch Indigo border plus a 1px indigo ring. Keyboard focus on the photo is a 3px indigo outline with a white inner ring, so it shows on any image. Opening a card from the keyboard moves focus to the caption.

### Post editor
Before a post has a caption, the editor shows the image, its navigation and **Generate content**, and in place of the form a Tray Grey note: "No caption yet" with **Write it myself**. The full form opens once there is a caption. Once a post is scheduled, the editor mirrors Buffer: the form shows the post as Buffer has it now (including an edit made in Scheduled posts) and is locked, and **Regenerate** is disabled. The result note sits at the top of the editor, outside the locked form: "In Buffer · Fri, 30 Oct, 9:00 am", the channels, and **Edit in Scheduled posts**, which opens that post's own dialog. Enter on a scheduled card moves focus to that button. **Schedule again** asks first, in the danger variant with Cancel focused, because it makes a second copy.

### List view
The gallery's second view, for reviewing a batch in one scroll. Each row: a 112px photo (72px on phones) with its checkbox and order number, then the file name, the caption in an editable textarea that shows the whole caption (Post type, `lang` from its script), the hashtags as read-only pills, and a line with the publish-time field, the status chip and a note when the alternative caption is published. Edits apply exactly as in the editor. Posts already scheduled are read-only here. Rows reorder with Alt+←/→ or the arrow buttons, not by dragging, so selecting caption text never moves a row.

### Scheduled posts
One row per post, the way the owner thinks of it (one image), not per Buffer channel copy. Copies that share their text and publish time are one post. The row shows the thumbnail, the caption (two lines, `lang` from its script), its hashtags on one muted line, the time, one status chip when every copy agrees, and a line per channel (colour dot, name, and its own chip when they differ). Edit and Delete act on every copy ("Delete post (2 channels)"); a small ✕ deletes one channel's copy ("Delete the Instagram copy?"). An edit flows back to the image's card, List row and editor. Post IDs live in the channel name's tooltip. After **View in Scheduled posts**, the list shows Upcoming, soonest first, with the posts just created on Indigo Wash, unless every row is new, when nothing is highlighted. The batch line counts posts and gives Buffer's count in brackets: "6 upcoming in Buffer (12 channel posts)".

### Schedule planner
A disclosure between the Generate and Schedule rows: first post time, then "every [n] [minutes | hours | days]", a live preview line ("Apply to all: 6 posts, from Thu, 29 Oct, 3:00 pm to 8:00 pm"), the replace-times checkbox, and Apply to selected / Apply to all. The first post defaults to the time of day used last (9:00 am at first), today if still ahead, otherwise tomorrow. When the plan puts posts between 11 pm and 6 am, the preview turns Caution Amber and says how many, and the Live confirmation and the closing line repeat the count. The amber "Plan times" link is hidden while the planner is open. When posts lack a time, the Schedule row shows an amber ghost link: "3 posts need a publish time · Plan times".

### Batch progress (op status)
A 12px-radius panel with a title, spinner and Cancel button (a default button: cancelling is never destructive), then an 8px progress track (flat Dispatch Indigo while running), the current item on one line, and tabular stats. The border and bar take the outcome's status colour when the run finishes. A finished scheduling run ends on a closing line, "6 posts queued in Buffer · first Fri, 30 Oct, 4:00 pm, last Fri, 30 Oct, 9:00 pm.", with a **View in Scheduled posts** button. In Demo mode the line says "in Demo mode (not in Buffer)", and so do the toasts.

### Feedback: inline alerts, banners, toasts
All three use the status trio: a wash background, a light border and an icon in the strong colour, with a 650 title and Muted detail text. Banners sit across the top of the content; their actions are default buttons, never primary. Toasts (390px wide, Float shadow, a 3px timer bar that pauses on hover or focus) show at most two at a time, and a run's success toast is skipped when its progress panel is on screen, since it would only repeat it.

### Settings drawer and dialogs
The drawer slides in from the right (up to 620px wide): a sticky header, a scrolling body of hairline-separated sections (14px/700 headings with an indigo icon, descriptions capped at 72 characters a line), and a Tray Grey footer with Cancel and Save settings. Dialogs are 460px (580px wide variant) with a 16px radius, a 40px tinted icon tile, and right-aligned actions. The danger variant tints the tile red. The tile's icon fits the question: the alert triangle for warnings and deletes, a neutral icon for choices such as switching to Live. Focus opens on the safe choice: the primary when it is safe, otherwise Cancel, so Enter never deletes or overwrites. The "can't be scheduled yet" dialog names the posts behind each reason.

### Accessibility details
In Windows high-contrast mode, the stage's primary button fills with Highlight, the checked segmented option, the checked mode and channel options and the open post carry a Highlight outline, and progress bars fill with Highlight. On touch screens every control is at least 44px. Moving between posts announces the file, its status and its position once; moving a card announces its new position. Screen readers hear a run's start and its outcome, not each item: the activity log is a plain list, not a live region. In the List view, Alt+↑/↓ moves the caret to the same field in the next row.

### Motion
State changes take 0.15s. Entrances take 0.2–0.25s on `cubic-bezier(.2,.8,.2,1)`: fades, a 12px rise for toasts, a 32px slide for the drawer, an 8px rise with 0.98 scale for dialogs. With `prefers-reduced-motion`, everything snaps to its end state and scrolling jumps instead of gliding, but spinners keep turning slowly (1.6s) so work still reads as in progress.

## Do's and Don'ts

### Do:
- **Do** take every colour from the CSS custom properties, and define any new colour in both the light (`:root, [data-theme="light"]`) and dark (`[data-theme="dark"]`) blocks.
- **Do** give each action group exactly one primary (Dispatch Indigo) button. Everything else is default, ghost or danger.
- **Do** pair every status colour with a label, dot or icon (The Status Speaks Twice Rule).
- **Do** use tabular numbers for every count, counter, score and time.
- **Do** build new panels as a card with a header row, a hairline and a 20px body, on 14px corners with the Whisper shadow.
- **Do** show a post's own words at 13px or larger with a line-height of at least 1.5, and set `lang` from its script.
- **Do** keep the 2px focus outline (or the 3px field ring) on everything interactive, and keep reduced-motion support.
- **Do** keep Muted Slate for any secondary text that has to be read; it passes 4.5:1 everywhere.
- **Do** say "Demo mode (not in Buffer)" wherever Demo results are reported, and nowhere else more than once: in Demo the service labels are hidden and the AI panel says "(simulated)".
- **Do** count posts the way the owner does, one per image, and give Buffer's per-channel count in brackets.

### Don't:
- **Don't** drift into growth-hacker SaaS: no gradients, glows, purple-to-pink washes or decorative sparkles. The sparkles icon labels AI generation actions and is never used as ornament.
- **Don't** imitate Instagram, X or Buffer's interface. The post preview shows the text plainly, not as a fake network card.
- **Don't** turn Poster into an analytics dashboard: no charts, trend arrows, hero numbers or KPI tiles. The batch line counts the work, and the engagement score is always presented as the model's estimate.
- **Don't** fill a button red. Destructive actions are outlined red, confirmed in a dialog, and the dialog opens with Cancel focused.
- **Don't** use Faint Slate for text. It's for icons, grips and dots.
- **Don't** call a post "Ready" unless it has a caption and a publish time ahead.
- **Don't** load fonts, icons or styles from another origin. The Content Security Policy forbids it, and the icon sprite lives in `templates/index.html`.
- **Don't** raise resting shadows above Whisper, or colour a shadow.
- **Don't** give list items or alerts a coloured side stripe.
- **Don't** let a post that is already in Buffer be edited anywhere but its own Scheduled-posts dialog.
