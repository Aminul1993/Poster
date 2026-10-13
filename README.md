# Poster

Upload images (or generate them from a description), generate captions, hashtags, a category and an engagement score with an Ollama vision model, then schedule the posts through Buffer.

- `app.py`: Flask server. It holds the credentials and handles every call to Ollama, Buffer, Pollinations and the optional media host.
- `templates/index.html`: the dashboard (HTML, CSS and JavaScript). It only talks to `app.py`.

## Run

```bash
pip install -r requirements.txt
cp .env.example .env        # Windows: copy .env.example .env
# edit .env: OLLAMA_API_KEY, BUFFER_ACCESS_TOKEN, optionally POLLINATIONS_API_KEY and MEDIA_UPLOAD_ENDPOINT
python app.py               # http://127.0.0.1:5000
```

Without credentials, Poster starts in **Demo mode**, where AI and scheduling are simulated.

## Notes

- The **Add images** card has two modes: **Upload** (drag and drop, browse or paste) and **Generate with AI** (describe the image, pick a shape, and Pollinations creates it). Generated images join the gallery exactly like uploads. Generation needs `POLLINATIONS_API_KEY` (free at enter.pollinations.ai); in Demo mode it draws a placeholder instead.
- Buffer attaches images only from public URLs. Set `BLOB_READ_WRITE_TOKEN` to use a public Vercel Blob store, or configure another media host (for example a Cloudinary unsigned upload preset), or paste a public image URL per post in the editor.
- **Scheduled posts** lists the account's posts read directly from Buffer (All, Upcoming or Published, 30 per page), including posts created in Buffer itself. Nothing is saved in the browser: the list loads when the page opens, on Refresh, when the filter or mode changes, and once after each scheduling run. Buffer's API allows few requests (100 per 15 minutes on the free plan), so it never reloads on its own. Demo mode keeps its simulated posts in memory for the tab. A post whose image is served by Poster (`<PUBLIC_BASE_URL>/api/media/image/<name>.jpg`) shows its stored Vercel Blob thumbnail instead of the full image.
- Posts can be changed until they are published: the pencil edits a post's text and publish time, and the calendar-✕ deletes it from Buffer. Before each change the server reads the post's status from Buffer and refuses posts that are published (`sent`) or being published (`sending`). When all of an image's posts are deleted, the image can be scheduled again.
- The dashboard shows thumbnails, never the original file. Gallery cards always show the one made in the browser when an image is added; the editor preview switches to the stored one once the image is uploaded.
- With Vercel Blob, Poster never uploads the original file. It saves a 400 px AVIF thumbnail (`poster/<name>.thumb.avif`) and a compressed AVIF copy of the image (`poster/<name>.avif`: at most 1920 px, quality 48, turned upright, EXIF stripped), and schedules the post with `/api/media/image/<name>.jpg` (or `.png`/`.webp`, matching the uploaded file) on this server as the image URL. Buffer and the networks it posts to do not take AVIF, so that route converts the AVIF copy back to the format its extension names; `<name>.avif` or the bare name returns the AVIF copy as stored. Buffer must be able to reach it: on Vercel the URL uses the address you opened Poster at (keep Deployment Protection off for it), and elsewhere you set `PUBLIC_BASE_URL`.
- On Vercel, request bodies are capped at 4.5 MB, so the browser re-encodes images over 4 MB as smaller JPEGs before uploading them.
- Server-side calls retry timeouts, network errors and HTTP 429/5xx three times (1 s, 2 s, 4 s). Buffer post creation is retried only on 429/503, so a post is never created twice.
- Keep `POSTER_HOST=127.0.0.1` unless you put authentication in front of the server: anyone who can reach it can use your Buffer and Ollama credentials.
