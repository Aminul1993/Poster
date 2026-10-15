"""Poster: Flask backend for AI social media publishing.

The package does four jobs:

* serves the dashboard (``templates/index.html`` and ``static/``),
* talks to Ollama: one image in; a caption, an alternative caption, hashtags,
  a category and an engagement score out (``services/ollama.py``),
* optionally talks to Pollinations: a written description in, an image out,
  as an alternative to uploading one (``services/imagegen.py``),
* talks to Buffer's GraphQL API (token check, channels, scheduled posts) and,
  optionally, to a media host that turns uploads into public image URLs:
  Vercel Blob, which stores a thumbnail and a compressed AVIF copy of each
  image that this server serves back to Buffer, or an upload endpoint such as a
  Cloudinary unsigned preset (``services/buffer.py``, ``services/media.py``).

Credentials stay on the server, in environment variables or a ``.env`` file
next to app.py, and the browser never sees them. The browser only calls this
server (same origin), so Ollama's and Buffer's CORS rules do not apply.
"""

from poster.web import create_app

__all__ = ["create_app"]
