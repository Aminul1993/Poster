# Poster

Upload images (or generate them from a description, or have AI change an uploaded photo as you describe), generate captions, hashtags, a category and an engagement score with a Groq vision model, then schedule the posts to your Facebook Pages and Instagram accounts. Poster keeps the scheduled posts in its own queue and publishes them through Meta's Graph API when they fall due.

## Layout

- `app.py`: the entry point, for Vercel and for `python app.py`.
- `poster/`: the Flask server. It holds the credentials and handles every call to Groq, Meta, Pollinations and the optional media host.
  - `web.py`: routes, security headers, static assets. `auth.py`: the password.
  - `store.py`: the queue of scheduled posts and the activity log (Postgres, or a SQLite file for local runs). `activity.py`: the activity log.
  - `services/`: `groq.py`, `meta.py` (Graph API), `social.py` (scheduling and the publisher), `media.py` (Vercel Blob or an upload endpoint) and `imagegen.py` (Pollinations).
  - `content.py`: model reply to post content. `imaging.py`: thumbnails and AVIF copies.
  - `config.py`: settings, limits and API paths. `retry.py`, `http.py`, `validation.py`.
- `templates/index.html` and `static/`: the dashboard. Its JavaScript is plain ES modules (`static/js/main.js` and what it imports, with one view per card in `static/js/ui/`), with no build step and no npm. It only talks to the server.
- `tests/`: the pytest suite. `tests/browser/` drives the real dashboard with Playwright.
- `tools/check_js.py`: checks the JavaScript modules' imports, without Node.

## Run

```bash
pip install -r requirements.txt
cp .env.example .env        # Windows: copy .env.example .env
# edit .env: GROQ_API_KEY, META_ACCESS_TOKEN, optionally POLLINATIONS_API_KEY and BLOB_READ_WRITE_TOKEN
python app.py               # http://127.0.0.1:5000
```

Without credentials, Poster starts in **Demo mode**, where AI and scheduling are simulated.

Locally, scheduled posts are kept in `poster.db` (SQLite) next to `app.py`, unless `DATABASE_URL` points to Postgres, and `python app.py` publishes them every minute while it runs. Facebook and Instagram still fetch each post's image from a public URL, so local runs need a public media host (see Notes).

## Connect Facebook and Instagram

Poster posts with a token from your own Meta app. Since you are the only user, the app can stay in Development mode and needs no App Review.

1. Switch the Instagram account to a professional account (Business or Creator) and link it to the Facebook Page (in the Page's settings, under Linked accounts).
2. At [developers.facebook.com](https://developers.facebook.com/apps), create an app of type **Business** and connect it to your business portfolio.
3. In [Meta Business settings](https://business.facebook.com/settings), go to **Users → System users** and add a system user (Admin). Under **Assign assets**, give it full control of the Page and of the Instagram account.
4. Still on the system user, choose **Generate token**, pick the app, choose **Never** expires, and tick `pages_show_list`, `pages_read_engagement`, `pages_manage_posts`, `instagram_basic`, `instagram_content_publish` and `business_management`.
5. Put the token in `META_ACCESS_TOKEN`. Optionally put the app secret (App settings → Basic) in `META_APP_SECRET`, so every call carries an `appsecret_proof`.
6. In the dashboard, open **Settings → Facebook & Instagram** and choose **Connect**. Each Page appears with the Instagram account linked to it; tick the ones to post to.

## Deploy to Vercel

1. Add a Postgres database from the Vercel Marketplace (Supabase or Neon). The integration sets `POSTGRES_URL`, which Poster uses; `DATABASE_URL` takes precedence if you set both. Poster creates its tables (`poster_posts`, `poster_state`, `poster_activity`) on first use, with row level security turned on, so Supabase's public REST API cannot read them.
2. Set the variables from `.env.example` in the Vercel project, including `META_ACCESS_TOKEN` and `CRON_SECRET` (a long random value, for example from `python -c "import secrets; print(secrets.token_urlsafe(32))"`).
3. Set `POSTER_PASSWORD` to a long random value. Poster serves a public address only with a password: the browser asks for it once (any user name), and requests without it are refused.
4. Facebook and Instagram fetch post images from `/api/media/image/` without the password, so Deployment Protection must stay off for those URLs, and for `/api/publish/due`.
5. Set up the publisher (below).

On Vercel, each request stops retrying after `POSTER_REQUEST_BUDGET` seconds (280 by default), before the function's 300-second limit in `vercel.json`. A Vercel Firewall rate-limit rule on `/api/` is a good addition.

### The publisher

Instagram's API cannot schedule posts; it only publishes immediately. So Poster keeps the queue and publishes each post when it falls due, for both networks. On Vercel nothing runs between requests, so something has to call the publisher every minute:

```
GET https://<your-app>.vercel.app/api/publish/due
Authorization: Bearer <CRON_SECRET>
```

- **Vercel Hobby** runs cron jobs only once a day, so use an outside scheduler. At [cron-job.org](https://cron-job.org) (free), create a job for that URL that runs every minute, and under **Advanced → Headers** add `Authorization` with the value `Bearer <CRON_SECRET>`.
- **Vercel Pro**: add `"crons": [{"path": "/api/publish/due", "schedule": "* * * * *"}]` to `vercel.json`. Vercel sends the `CRON_SECRET` header itself.

Each run publishes up to 10 due posts. A post goes out within a minute or two of its time, as long as the scheduler keeps calling. The **Scheduled posts** card shows when the publisher last ran and warns when posts are waiting for it. Runs that overlap are safe: each post is claimed in the database before it is published, so it is never published twice.

## Develop

```bash
pip install -r requirements-dev.txt
python -m playwright install chromium     # once, for the browser tests
ruff check . && mypy && python tools/check_js.py
pytest                                     # server and browser tests
pytest --browser-channel msedge            # the browser tests on an installed Edge
```

Tests never reach the real services or read your `.env`: the upstream APIs are replaced by in-memory fakes (`tests/fakes.py`), and every app gets its own in-memory SQLite queue. To run the queue against Postgres too, point `POSTER_TEST_DATABASE_URL` at a throwaway database: its tables are dropped before every test.

`requirements.txt` is a lock file. To change a dependency, edit `requirements.in` and run `pip-compile requirements.in`.

## Notes

- The **Add images** card has three modes: **Upload** (drag and drop, browse or paste), **Generate with AI** (describe the image, pick a shape, and Pollinations creates it) and **Edit with AI** (add a photo, describe the change, and Pollinations makes an updated copy). Generated and edited images join the gallery exactly like uploads; the photo you edit is not changed or added, so you can try again or describe another change. Both need `POLLINATIONS_API_KEY` (free at enter.pollinations.ai); in Demo mode they draw a placeholder or tint the photo instead.
- **Edit with AI** sends a copy of the photo shrunk to at most 1536 px (JPEG) through `/api/image/edit` to Pollinations' `/v1/images/edits`, using the model in `POLLINATIONS_EDIT_MODEL` (`kontext`, FLUX.1 Kontext, by default; any model that lists `/v1/images/edits` in gen.pollinations.ai/image/models works). An edit is billed per call and not cached, so it is retried only when Pollinations refuses it (HTTP 429/503), never after a timeout or another server error.
- Facebook and Instagram fetch images only from public URLs, and they fetch them when the post is published, not when it is scheduled, so an image must stay reachable until then. Set `BLOB_READ_WRITE_TOKEN` to use a public Vercel Blob store, or configure another media host (for example a Cloudinary unsigned upload preset), or paste a public image URL per post in the editor.
- Instagram takes JPEG images with an aspect ratio between 4:5 and 1.91:1, captions up to 2,200 characters and at most 30 hashtags, and every post needs an image. Poster refuses an Instagram post that breaks the caption rules or has no image when you schedule it; an image Instagram cannot use shows up as a failed post. Facebook posts can be text only. Instagram publishes at most 100 posts per account in 24 hours.
- **Scheduled posts** lists the posts in Poster's queue (All, Upcoming or Published, 30 per page): scheduled, being published (`sending`), published (`sent`, with a link to the post) and failed (`error`, with Meta's reason). Posts made directly on Facebook or Instagram are not listed. The list reads the database alone, so it works before `META_ACCESS_TOKEN` is set. Demo mode keeps its simulated posts in memory for the tab. A post whose image is served by Poster (`<PUBLIC_BASE_URL>/api/media/image/<name>.jpg`) shows its stored Vercel Blob thumbnail instead of the full image.
- The **Activity log** is kept in the database (the newest 1,000 entries, 100 per page, filtered by the server). The dashboard saves its entries in batches, and they wait in the browser until the server has them; the publisher adds what it published, what it will try again and what it gave up on. Without a database, the log stays in the browser.
- Posts can be changed until they are published: the pencil edits a post's text and publish time, and the calendar-✕ deletes it from the queue. Posts that are published or being published are refused. Editing a failed post puts it back in the queue (at its new time, or at the next run if its time has passed). When all of an image's posts are deleted, the image can be scheduled again.
- The dashboard shows thumbnails, never the original file. Gallery cards always show the one made in the browser when an image is added; the editor preview switches to the stored one once the image is uploaded.
- With Vercel Blob, Poster never uploads the original file. It saves a 400 px AVIF thumbnail (`poster/<name>.thumb.avif`) and a compressed AVIF copy of the image (`poster/<name>.avif`: at most 1920 px, quality 48, turned upright, EXIF stripped), and schedules the post with `/api/media/image/<name>.jpg` (or `.png`/`.webp`, matching the uploaded file) on this server as the image URL; Instagram posts always get the `.jpg` URL. Facebook and Instagram do not take AVIF, so that route converts the AVIF copy back to the format its extension names; `<name>.avif` or the bare name returns the AVIF copy as stored. Meta must be able to reach it: on Vercel the URL uses the address you opened Poster at, and elsewhere you set `PUBLIC_BASE_URL`.
- On Vercel, request bodies are capped at 4.5 MB, so the browser re-encodes images over 4 MB as smaller JPEGs before uploading them. PNG and WEBP images over 40 megapixels are refused, because they are decoded at full size.
- Hashtags keep letters, digits, underscores and the combining marks of scripts such as Bengali, Hindi and Tamil (`#বাংলাদেশ`, `#हिन्दी`).
- Server-side calls retry timeouts, network errors and HTTP 429/5xx up to three times (1 s, 2 s, 4 s), within the request's time budget. Scheduling sends a key with each post, so a schedule request sent again (after a lost answer) returns the posts already queued instead of adding them twice. The publisher retries a publish immediately only on 429/503, and later (after 2, 5, 15 and 30 minutes, five tries in all) on rate limits and temporary errors. When a publish's answer is lost, the post stays `sending`, and about ten minutes later the publisher looks for it on the Page or Instagram account (and checks the Instagram upload's status) before it tries again, so a post is not published twice. An expired or revoked token stops the run and leaves the posts queued.
- Set `GROQ_MODELS` to limit which models the dashboard may use.
- Keep `POSTER_HOST=127.0.0.1` unless you set `POSTER_PASSWORD`: anyone who can reach the server could otherwise use your Meta and Groq credentials.
