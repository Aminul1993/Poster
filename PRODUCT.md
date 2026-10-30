# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

One owner who posts to their own social accounts for their own brand or business. They sit down with a batch of photos, or an idea for images they don't have yet, and want to leave with a run of captioned, hashtagged posts scheduled in Buffer. They run Poster themselves, either locally or on their own Vercel project behind a password, so the person using the dashboard is also the person who configured the server.

The code is MIT-licensed on GitHub, but other self-hosters are not a confirmed audience.

## Product Purpose

Poster turns a set of images into scheduled social posts. It does this in one pass:

1. Add up to 10 images. Each can be uploaded, generated from a description, or made by AI-editing a photo.
2. An Ollama vision model writes each image's post: a caption, an alternative caption, hashtags, a category and an estimated engagement score.
3. The owner reviews and edits the posts, then sets publish times one at a time or with the planner (a first post time, then a fixed interval).
4. Poster schedules the posts through Buffer. They can then be watched, edited and deleted until they are published.

Success means a whole batch goes from images to correctly scheduled Buffer posts in one sitting. No post is lost or duplicated, and the owner doesn't have to rewrite the AI's work post by post.

## Positioning

Poster is a self-hosted batch publisher. A set of photos, or images generated on demand, goes in. Captioned, hashtagged, scheduled posts come out. The model is one the owner chooses, every credential stays on the owner's server, and the only subscription is the owner's existing Buffer plan. Buffer's own composer works one post at a time, with Buffer's AI and Buffer's storage.

## Operating Context

- **Where it runs:** locally with `python app.py` at `127.0.0.1:5000`, or on the owner's Vercel project. On Vercel, `POSTER_PASSWORD` is required (HTTP Basic auth, any user name).
- **Services:** Ollama (captions, local or Ollama Cloud), Buffer (scheduling, one account, several channels), Pollinations (image generation and editing, optional), and Vercel Blob or another media host (public image URLs for Buffer, optional).
- **Demo mode:** without credentials, Poster starts in Demo mode. AI, image generation and scheduling are simulated. The header always shows whether Demo or Live is active.
- **Workflow and UI names:**
  - **Add images**: Upload, Generate with AI or Edit with AI.
  - **Image gallery**: select, reorder, remove (with Undo), and Edit selected (add or remove hashtags and set the category on every selected post).
  - **AI content & scheduling**: generate or schedule this image, the selected images, or all of them; the Schedule planner; the post editor.
  - **Scheduled posts**: read from Buffer, filtered by All, Upcoming or Published.
  - **Activity log**.
  - **Settings**: a side drawer with Mode, Ollama, Buffer, Media hosting, Scheduling, Content rules, and Import/export/reset.
- **Storage:** preferences, theme, drafts and the activity log live in the browser's localStorage. API keys and endpoints never leave the server and are never exported.

## Capabilities and Constraints

**Limits**
- At most 10 images per batch. Upload accepts JPG, JPEG, PNG or WEBP up to 20 MB each.
- At most 15 unique hashtags per post. Caption minimum and maximum lengths are settings. Reference limits are Instagram 2,200 characters and X 280.

**Post content**
- Each post has a caption and an alternative caption. The owner picks which one to publish.
- Posts also carry a category (free text with suggestions), a tone of voice (Friendly, Professional, Playful, Inspirational, Luxury or Bold) and optional extra instructions to the model.
- The caption language is a batch setting (English, Bengali, Bengali + English, Hindi, Hindi + English, Tamil or Tamil + English) that a post can override in the editor. It asks the model to write in that language; Poster does not check what language comes back.
- The engagement score (1–100) is the model's estimate. It is not measured performance and must never be shown as analytics.

**Buffer and hosting limits**
- Buffer attaches images only from public URLs: from Vercel Blob, a media host, or a URL pasted for each post.
- Buffer allows about 100 requests per 15 minutes on the free plan, so the Scheduled posts list never refreshes on its own.
- Scheduled posts can be edited (text and publish time) or deleted until Buffer marks them sending or sent.
- On Vercel, request bodies are capped at 4.5 MB and functions at 300 s. Images over 4 MB are re-encoded in the browser before upload.

**Reliability**
- Server calls retry within a time budget.
- Buffer post creation is never retried in a way that could duplicate a post. A lost reply is checked against Buffer before a failure is reported.

**Front-end stack**
- There is no build step and no npm. The dashboard is a single Flask/Jinja page (`templates/index.html`) with plain ES modules (`static/js/`) and one stylesheet (`static/css/poster.css`).
- The Content Security Policy allows only same-origin scripts (with a nonce) and same-origin or inline styles, and connects only to its own server. Fonts, scripts and stylesheets must ship with the app; nothing loads from third-party origins.
- Light and dark themes exist. The theme follows the system until the owner picks one.
- The browser talks only to the Poster server, never directly to Ollama, Buffer or Pollinations.

## Brand Commitments

- **Name:** Poster. Current tagline: "AI captions · Buffer scheduling".
- **Assets:** none beyond the send-arrow mark used for the favicon and header. There is no logo file or brand guide.
- **Voice (observed in current copy, not separately confirmed):** plain and precise sentence case that says what will happen and why, for example "Buffer has no upload endpoint: images are attached from public URLs."

## Evidence on Hand

- Demo mode (`static/js/demo.js`) produces simulated captions and posts. It is suitable for screenshots and design reviews without real credentials.
- `tests/browser/` drives the real dashboard with Playwright in Demo and Live modes against in-memory fakes.
- There are no usage figures, testimonials, customer names or performance results. Don't invent any. In particular, never present engagement scores as real outcomes.

## Product Principles

1. **The batch is the unit of work.** Actions on all or selected images are the main path. Single-post editing is for review and correction, not the default way to work.
2. **AI drafts, the owner decides.** Nothing reaches Buffer without an explicit schedule action, and every generated field stays editable until the post is published.
3. **Your model, your keys, your server.** Credentials stay on the server and the browser talks only to Poster. New features must not add third-party calls from the browser.
4. **Show the true state.** Always make clear whether Demo or Live is active, what Buffer actually holds, and what failed and why. Never imply a post was scheduled, or a score was measured, when it wasn't.
5. **South Asian scripts are normal content.** Captions and hashtags in Bengali, Hindi, Tamil and other South Asian languages are everyday input, not edge cases.

## Accessibility & Inclusion

- Real posts are written in several South Asian languages (confirmed), including Bengali, Hindi and Tamil, often mixed with English. Captions, hashtags, previews and counters must display, edit and wrap these scripts correctly.
- No formal accessibility standard has been set. The current dashboard already supports keyboard and screen-reader use (observed in code): a skip link, labelled controls, live status regions, keyboard-operable dropzones, and arrow-button reordering as well as drag. Future work keeps that level.
