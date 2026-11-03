"""Poster: the Vercel entry point and local server. The app itself lives in the ``poster`` package.

Run::

    pip install -r requirements.txt
    copy .env.example .env      (then fill in your keys)
    python app.py               -> http://127.0.0.1:5000

While it runs, it also publishes scheduled posts as they fall due (on Vercel, a scheduler calls
/api/publish/due every minute instead).
"""

from __future__ import annotations

import logging
import os

from poster import create_app
from poster.web import log_startup, start_local_publisher

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

app = create_app()

if __name__ == "__main__":
    config = app.extensions["poster"].config
    log_startup(app)
    # With the debugger's reloader, only the process that serves requests publishes.
    if not config.debug or os.environ.get("WERKZEUG_RUN_MAIN") == "true":
        start_local_publisher(app)
    app.run(host=config.host, port=config.port, debug=config.debug, threaded=True)
