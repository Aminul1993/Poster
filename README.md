# Poster

Upload images (or generate them from a description), generate captions, hashtags, a category and an engagement score with an Ollama vision model, then schedule the posts through Buffer.

## Layout

- `app.py`: the entry point, for Vercel and for `python app.py`.
- `poster/`: the Flask server. It holds the credentials and handles every call to Ollama, Buffer, Pollinations and the optional media host.
  - `web.py`: routes, security headers, static assets. `auth.py`: the password.
  - `services/`: `ollama.py`, `buffer.py`, `media.py` (Vercel Blob or an upload endpoint) and `imagegen.py` (Pollinations).
  - `content.py`: model reply to post content. `imaging.py`: thumbnails and AVIF copies.
  - `config.py`: settings, limits and API paths. `retry.py`, `http.py`, `validation.py`.
- `templates/index.html` and `static/`: the dashboard. Its JavaScript is plain ES modules (`static/js/main.js` and what it imports, with one view per card in `static/js/ui/`), with no build step and no npm. It only talks to the server.
- `tests/`: the pytest suite. `tests/browser/` drives the real dashboard with Playwright.
- `tools/check_js.py`: checks the JavaScript modules' imports, without Node.

## Run

```bash
pip install -r requirements.txt
cp .env.example .env        # Windows: copy .env.example .env
# edit .env: OLLAMA_API_KEY, BUFFER_ACCESS_TOKEN, optionally POLLINATIONS_API_KEY and MEDIA_UPLOAD_ENDPOINT
python app.py               # http://127.0.0.1:5000
```

Without credentials, Poster starts in **Demo mode**, where AI and scheduling are simulated.

## Deploy to Vercel

1. Set the variables from `.env.example` in the Vercel project.
2. Set `POSTER_PASSWORD` to a long random value. Poster serves a public address only with a password: the browser asks for it once (any user name), and requests without it are refused.
3. Buffer fetches post images from `/api/media/image/` without the password, so Deployment Protection must stay off for those URLs.

On Vercel, each request stops retrying after `POSTER_REQUEST_BUDGET` seconds (280 by default), before the function's 300-second limit in `vercel.json`. A Vercel Firewall rate-limit rule on `/api/` is a good addition.

## Develop

```bash
pip install -r requirements-dev.txt
python -m playwright install chromium     # once, for the browser tests
ruff check . && mypy && python tools/check_js.py
pytest                                     # server and browser tests
pytest --browser-channel msedge            # the browser tests on an installed Edge
```

Tests never reach the real services or read your `.env`: the upstream APIs are replaced by in-memory fakes (`tests/fakes.py`).

`requirements.txt` is a lock file. To change a dependency, edit `requirements.in` and run `pip-compile requirements.in`.

## Notes

- The **Add images** card has two modes: **Upload** (drag and drop, browse or paste) and **Generate with AI** (describe the image, pick a shape, and Pollinations creates it). Generated images join the gallery exactly like uploads. Generation needs `POLLINATIONS_API_KEY` (free at enter.pollinations.ai); in Demo mode it draws a placeholder instead.
- Buffer attaches images only from public URLs. Set `BLOB_READ_WRITE_TOKEN` to use a public Vercel Blob store, or configure another media host (for example a Cloudinary unsigned upload preset), or paste a public image URL per post in the editor.
- **Scheduled posts** lists the account's posts read directly from Buffer (All, Upcoming or Published, 30 per page), including posts created in Buffer itself. Nothing is saved in the browser: the list loads when the page opens, on Refresh, when the filter or mode changes, and once after each scheduling run. Buffer's API allows few requests (100 per 15 minutes on the free plan), so it never reloads on its own, and a filter change waits for the load in flight instead of sending a request next to it. Demo mode keeps its simulated posts in memory for the tab. A post whose image is served by Poster (`<PUBLIC_BASE_URL>/api/media/image/<name>.jpg`) shows its stored Vercel Blob thumbnail instead of the full image.
- Posts can be changed until they are published: the pencil edits a post's text and publish time, and the calendar-✕ deletes it from Buffer. Before each change the server reads the post's status from Buffer and refuses posts that are published (`sent`) or being published (`sending`). When all of an image's posts are deleted, the image can be scheduled again.
- The dashboard shows thumbnails, never the original file. Gallery cards always show the one made in the browser when an image is added; the editor preview switches to the stored one once the image is uploaded.
- With Vercel Blob, Poster never uploads the original file. It saves a 400 px AVIF thumbnail (`poster/<name>.thumb.avif`) and a compressed AVIF copy of the image (`poster/<name>.avif`: at most 1920 px, quality 48, turned upright, EXIF stripped), and schedules the post with `/api/media/image/<name>.jpg` (or `.png`/`.webp`, matching the uploaded file) on this server as the image URL. Buffer and the networks it posts to do not take AVIF, so that route converts the AVIF copy back to the format its extension names; `<name>.avif` or the bare name returns the AVIF copy as stored. Buffer must be able to reach it: on Vercel the URL uses the address you opened Poster at, and elsewhere you set `PUBLIC_BASE_URL`.
- On Vercel, request bodies are capped at 4.5 MB, so the browser re-encodes images over 4 MB as smaller JPEGs before uploading them. PNG and WEBP images over 40 megapixels are refused, because they are decoded at full size.
- Hashtags keep letters, digits, underscores and the combining marks of scripts such as Bengali, Hindi and Tamil (`#বাংলাদেশ`, `#हिन्दी`).
- Server-side calls retry timeouts, network errors and HTTP 429/5xx up to three times (1 s, 2 s, 4 s), within the request's time budget. Buffer post creation is retried only on 429/503, so a post is never created twice. When the answer to a post creation is lost (a timeout or a dropped connection), the dashboard looks for the post in Buffer before reporting a failure, so scheduling again does not duplicate it.
- Set `OLLAMA_MODELS` to limit which models the dashboard may use.
- Keep `POSTER_HOST=127.0.0.1` unless you set `POSTER_PASSWORD`: anyone who can reach the server could otherwise use your Buffer and Ollama credentials.
