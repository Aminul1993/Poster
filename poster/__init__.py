"""Poster: Flask backend for AI social media publishing.

The package does four jobs:

* serves the dashboard (``templates/index.html`` and ``static/``),
* talks to Groq: one image in; a caption, an alternative caption, hashtags,
  a category and an engagement score out (``services/groq.py``),
* optionally talks to Pollinations: a written description in, an image out,
  as an alternative to uploading one (``services/imagegen.py``),
* keeps scheduled posts in its own queue (Postgres, or SQLite for local runs:
  ``store.py``) and publishes them to Facebook Pages and Instagram through
  Meta's Graph API when they fall due (``services/social.py``,
  ``services/meta.py``). Optionally, a media host turns uploads into the public
  image URLs Meta fetches: Vercel Blob, which stores a thumbnail and a
  compressed AVIF copy of each image that this server serves back as JPEG, or
  an upload endpoint such as a Cloudinary unsigned preset (``services/media.py``).

Credentials stay on the server, in environment variables or a ``.env`` file
next to app.py, and the browser never sees them. The browser only calls this
server (same origin), so Groq's and Meta's CORS rules do not apply.
"""

from poster.web import create_app

__all__ = ["create_app"]
