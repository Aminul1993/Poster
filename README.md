# Poster

Upload images, generate captions, hashtags, a category and an engagement score with an Ollama vision model, then schedule the posts through Buffer.

- `app.py`: Flask server. It holds the credentials and handles every call to Ollama, Buffer and the optional media host.
- `templates/index.html`: the dashboard (HTML, CSS and JavaScript). It only talks to `app.py`.

## Run

```bash
pip install -r requirements.txt
cp .env.example .env        # Windows: copy .env.example .env
# edit .env: OLLAMA_API_KEY, BUFFER_ACCESS_TOKEN, optionally MEDIA_UPLOAD_ENDPOINT
python app.py               # http://127.0.0.1:5000
```

Without credentials, Poster starts in **Demo mode**, where AI and scheduling are simulated.

## Notes

- Buffer attaches images only from public URLs. Configure a media host (for example a Cloudinary unsigned upload preset), or paste a public image URL per post in the editor.
- Server-side calls retry timeouts, network errors and HTTP 429/5xx three times (1 s, 2 s, 4 s). Buffer post creation is retried only on 429/503, so a post is never created twice.
- Keep `POSTER_HOST=127.0.0.1` unless you put authentication in front of the server: anyone who can reach it can use your Buffer and Ollama credentials.
