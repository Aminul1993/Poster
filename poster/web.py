"""The Flask app: dashboard page, JSON API, static assets, security headers and error handling."""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import mimetypes
import re
import secrets
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast
from urllib.parse import urlparse

from flask import Blueprint, Flask, current_app, g, jsonify, redirect, render_template, request, send_from_directory, url_for
from werkzeug.exceptions import HTTPException, NotFound

from poster import validation
from poster.activity import ActivityLog
from poster.auth import require_login
from poster.config import API, APP_VERSION, BASE_DIR, LANGUAGES, LIMITS, TONES, Config, check_url, is_local_host
from poster.content import FALLBACK_TAGS, STOP_WORDS
from poster.errors import ServiceError
from poster.http import HttpClient
from poster.imaging import IMAGE_EXTENSIONS, sniff_image
from poster.retry import RetryLog, time_budget
from poster.services.groq import GroqService
from poster.services.imagegen import ImageGenService
from poster.services.media import MediaService
from poster.services.meta import MetaService
from poster.services.social import SocialService, UrlMapper
from poster.store import PostStore

log = logging.getLogger("poster")

# Some Windows registries map .js to text/plain, which browsers refuse for module scripts.
mimetypes.add_type("text/javascript", ".js")
mimetypes.add_type("text/css", ".css")

STATIC_DIR = BASE_DIR / "static"
MEDIA_IMAGE_PATH = "/api/media/image/"
_IMAGE_FILE_RE = re.compile(r"([a-z0-9-]{1,80})(?:\.(avif|jpe?g|png|webp))?")
# What media_image serves for each extension; a bare name gets the AVIF copy as stored.
_IMAGE_FILE_TYPES = {None: "image/avif", "avif": "image/avif", "jpg": "image/jpeg", "jpeg": "image/jpeg",
                     "png": "image/png", "webp": "image/webp"}
_REQUEST_ID_RE = re.compile(r"[\w.:-]{1,64}")
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]+")
IMMUTABLE = "public, max-age=31536000, s-maxage=31536000, immutable"


@dataclass(frozen=True)
class Services:
    """The app's configuration and service clients, kept in app.extensions["poster"]."""

    config: Config
    groq: GroqService
    social: SocialService
    activity: ActivityLog
    media: MediaService
    image_gen: ImageGenService
    # Content hash of static/, so asset URLs change whenever a file does and can be cached forever.
    asset_version: str


def services() -> Services:
    return cast(Services, current_app.extensions["poster"])


def asset_version(directory: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(p for p in directory.rglob("*") if p.is_file()):
        digest.update(path.relative_to(directory).as_posix().encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()[:12] if directory.is_dir() else APP_VERSION


bp = Blueprint("poster", __name__)


def create_app(config: Config | None = None, *, http: HttpClient | None = None) -> Flask:
    """The Poster app. Without ``config``, settings come from the environment and the .env file."""
    config = config or Config.from_env()
    http = http or HttpClient()
    app = Flask("poster", root_path=str(BASE_DIR), static_folder=None)
    app.config["MAX_CONTENT_LENGTH"] = LIMITS["max_image_bytes"] + 1024 * 1024
    app.json.sort_keys = False  # type: ignore[attr-defined]
    store = PostStore(config)
    activity = ActivityLog(store)
    app.extensions["poster"] = Services(
        config=config, groq=GroqService(http, config),
        social=SocialService(MetaService(http, config), store, activity, cron_secret=config.cron_secret), activity=activity,
        media=MediaService(http, config), image_gen=ImageGenService(http, config), asset_version=asset_version(STATIC_DIR),
    )
    app.register_blueprint(bp)
    return app


def default_mode() -> str:
    mode = services().config.mode
    if mode in ("demo", "live"):
        return mode
    return "live" if services().groq.configured else "demo"


def server_config() -> dict[str, Any]:
    """Non-secret configuration and the shared content rules, for the browser."""
    s = services()
    retry = API["retry"]
    return {
        "version": APP_VERSION,
        "mode": default_mode(),
        "groq": s.groq.public_info(),
        "social": s.social.public_info(),
        "media": s.media.public_info(),
        "imageGen": s.image_gen.public_info(),
        "limits": {"maxImageBytes": LIMITS["max_image_bytes"], "maxUploadBytes": s.config.max_upload_bytes,
                   "maxChannels": LIMITS["max_channels"]},
        # One source of truth for rules the browser applies too (validation, Demo mode, the posts list).
        "rules": {
            "tones": TONES,
            "languages": {key: value["label"] for key, value in LANGUAGES.items()},
            "hashtags": {"min": LIMITS["min_hashtags"], "max": LIMITS["max_hashtags"], "fallback": list(FALLBACK_TAGS),
                         "stopWords": sorted(STOP_WORDS)},
            "maxTextChars": LIMITS["max_text_chars"],
            "maxExtraChars": LIMITS["max_extra_chars"],
            "maxCategoryChars": LIMITS["max_category_chars"],
            "caption": {"minLow": LIMITS["min_caption_chars"], "minHigh": LIMITS["max_caption_min"],
                        "maxLow": LIMITS["min_caption_max"], "maxHigh": LIMITS["max_caption_chars"],
                        "defaultMin": LIMITS["default_caption_min"], "defaultMax": LIMITS["default_caption_max"]},
            "prompt": {"min": LIMITS["min_prompt_chars"], "max": LIMITS["max_prompt_chars"]},
            "postFilters": API["social"]["post_filters"],
            "publishedStatuses": sorted(API["social"]["published_statuses"]),
            "activity": {"filters": API["activity"]["filters"], "maxBatch": API["activity"]["max_batch"]},
            "instagram": {"maxCaptionChars": API["social"]["instagram_caption_chars"],
                          "maxHashtags": API["social"]["instagram_hashtags"]},
            "retry": {"maxRetries": retry["max_retries"], "delaysMs": [d * 1000 for d in retry["delays"]],
                      "retryableStatus": sorted(retry["retryable_status"]), "retryableKinds": sorted(retry["retryable_kinds"])},
        },
    }


# =============================================================================
# Request hooks and error handlers
# =============================================================================

@bp.before_app_request
def guard() -> Any:
    """Request ID and CSP nonce, authentication, and CSRF protection for state-changing API calls.

    The custom header forces a CORS preflight for cross-site requests, which
    this server never approves, so other websites cannot use your credentials.
    """
    incoming = request.headers.get("X-Request-Id") or request.headers.get("X-Vercel-Id") or ""
    g.request_id = incoming if _REQUEST_ID_RE.fullmatch(incoming) else secrets.token_hex(6)
    g.started = time.monotonic()
    g.csp_nonce = secrets.token_urlsafe(16)
    denied = require_login(services().config)
    if denied is not None:
        return denied
    if request.path.startswith("/api/") and request.method not in ("GET", "HEAD", "OPTIONS"):
        if request.headers.get("X-Poster-Request") != "1":
            return jsonify(error={"message": "Missing X-Poster-Request header.", "kind": "validation"}), 403
        origin = request.headers.get("Origin")
        if origin and urlparse(origin).netloc != request.host:
            return jsonify(error={"message": "Cross-origin requests are not allowed.", "kind": "validation"}), 403
    return None


@bp.after_app_request
def security_headers(response: Any) -> Any:
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    response.headers["X-Request-Id"] = getattr(g, "request_id", "")
    if response.mimetype == "text/html":
        nonce = getattr(g, "csp_nonce", "")
        # https: images, because the posts list shows each post's image from wherever it is hosted
        # (Vercel Blob, a media host or a URL pasted in the editor).
        response.headers["Content-Security-Policy"] = (
            f"default-src 'self'; script-src 'self' 'nonce-{nonce}'; style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data: blob: https:; connect-src 'self'; object-src 'none'; base-uri 'none'; "
            "form-action 'self'; frame-ancestors 'none'"
        )
    if request.path.startswith("/api/"):
        response.headers.setdefault("Cache-Control", "no-store")
        started = getattr(g, "started", None)
        if started is not None:
            log.info("%s %s -> %d in %.2fs [%s]", request.method, request.path, response.status_code,
                     time.monotonic() - started, g.request_id)
    return response


@bp.app_errorhandler(ServiceError)
def handle_service_error(error: ServiceError) -> Any:
    log.info("%s error (%s): %s", error.service or "Request", error.kind, error.message)
    return jsonify(error=error.to_dict()), error.http_status


@bp.app_errorhandler(HTTPException)
def handle_http_exception(error: HTTPException) -> Any:
    if not request.path.startswith("/api/"):
        return error
    message = error.description
    if error.code == 413:
        message = f"The upload is larger than the server allows ({LIMITS['max_image_bytes'] // (1024 * 1024)} MB)."
    return jsonify(error={"message": message, "kind": "validation" if (error.code or 500) < 500 else "server"}), error.code


@bp.app_errorhandler(Exception)
def handle_unexpected(error: Exception) -> Any:
    if isinstance(error, HTTPException):
        return handle_http_exception(error)
    log.exception("Unexpected error [%s]", getattr(g, "request_id", "-"))
    if request.path.startswith("/api/"):
        return jsonify(error={"message": "Unexpected server error. Check the server log.", "kind": "server"}), 500
    return "Unexpected server error. Check the server log.", 500


# =============================================================================
# Helpers
# =============================================================================

def public_base_url() -> str:
    """This server's public https:// address. Facebook and Instagram fetch post images from it (see media_image)."""
    configured = services().config.public_base_url
    if configured:
        problem = check_url(configured, "PUBLIC_BASE_URL", https_only=True)
        if problem:
            raise ServiceError(problem, kind="config", service="Media host")
        return configured.rstrip("/")
    if is_local_host(urlparse(request.host_url).hostname or ""):
        raise ServiceError("Facebook and Instagram fetch post images from this server, which is running on a local address "
                           "they cannot reach.",
                           kind="config", service="Media host",
                           hint="Deploy Poster (for example to Vercel), or set PUBLIC_BASE_URL to a public https:// address of this server.")
    return f"https://{request.host}"


def served_image_mapper(make: Callable[[str, str], str]) -> UrlMapper | None:
    """Maps an image URL this server serves from its AVIF copy on Vercel Blob (see media_image) to
    ``make(prefix, name)``, and other URLs to None. Image URLs start with PUBLIC_BASE_URL, or with this
    server's own address when it is unset."""
    media = services().media
    if not media.uses_blob or not media.configured:
        return None
    bases = (services().config.public_base_url, f"https://{request.host}")
    prefixes = [f"{base.rstrip('/')}{MEDIA_IMAGE_PATH}" for base in bases if base]

    def find(url: str) -> str | None:
        for prefix in prefixes:
            if url.startswith(prefix):
                match = _IMAGE_FILE_RE.fullmatch(url[len(prefix):].split("?")[0].lower())
                return make(prefix, match[1]) if match else None
        return None

    return find


def stored_thumbnails() -> UrlMapper | None:
    """An image's thumbnail on Vercel Blob, so the posts list shows the small thumbnail instead of pulling
    each full image through this server."""
    media = services().media
    return served_image_mapper(lambda _prefix, name: media.blob_url(f"{name}.thumb.avif"))


def served_as_jpeg() -> UrlMapper | None:
    """The .jpg URL of an image this server serves: Instagram takes JPEG only."""
    return served_image_mapper(lambda prefix, name: f"{prefix}{name}.jpg")


def run(label: str, action: Callable[[RetryLog], dict[str, Any]]) -> Any:
    """Runs a service action within the request's time budget and attaches its retry log to the response or error."""
    retries = RetryLog(label)
    try:
        with time_budget(services().config.request_budget):
            result = action(retries)
    except ServiceError as error:
        error.retries = list(retries)
        raise
    return jsonify({**result, "retries": list(retries)})


def log_safe(value: Any, limit: int = 200) -> str:
    """A browser-supplied name, safe to put in a log line."""
    return _CONTROL_RE.sub(" ", str(value))[:limit]


# =============================================================================
# Routes
# =============================================================================

@bp.get("/")
def index() -> Any:
    return render_template("index.html", server_config=server_config(), csp_nonce=g.csp_nonce,
                           asset_version=services().asset_version)


@bp.get("/assets/<version>/<path:filename>")
def assets(version: str, filename: str) -> Any:
    """The dashboard's CSS and JavaScript. URLs carry the content hash, so the current ones never change."""
    response = send_from_directory(STATIC_DIR, filename)
    response.headers["Cache-Control"] = IMMUTABLE if version == services().asset_version else "no-cache"
    return response


@bp.get("/api/health")
def health() -> Any:
    s = services()
    return jsonify(status="ok", version=APP_VERSION, groq=s.groq.configured, social=s.social.configured,
                   database=s.social.store.configured, media=s.media.configured, imageGen=s.image_gen.configured)


@bp.get("/api/config")
def config_endpoint() -> Any:
    return jsonify(server_config())


def generation_options(body: dict[str, Any]) -> Any:
    groq = services().groq
    return validation.parse_generation_options(body, default_model=groq.default_model, allowed_models=groq.allowed_models)


@bp.post("/api/groq/test")
def groq_test() -> Any:
    opts = generation_options(validation.json_body())
    return run("Groq test", lambda retries: services().groq.test(opts.model, retries))


@bp.post("/api/groq/generate")
def groq_generate() -> Any:
    body = validation.json_body()
    opts = generation_options(body)
    image_b64 = validation.parse_image_b64(body.get("image"))
    name = log_safe(body.get("fileName") or "image")
    return run(f"Groq generate ({name})", lambda retries: services().groq.generate(image_b64, opts, retries))


@bp.post("/api/image/generate")
def image_generate() -> Any:
    body = validation.json_body()
    prompt = validation.parse_prompt(body.get("prompt"))
    aspect = validation.parse_aspect(body.get("aspect"))
    return run("Image generate", lambda retries: services().image_gen.generate(prompt, aspect, retries))


@bp.post("/api/image/edit")
def image_edit() -> Any:
    """An updated copy of the image the browser sends (a downscaled JPEG), changed as the prompt describes."""
    body = validation.json_body()
    prompt = validation.parse_prompt(body.get("prompt"))
    image = base64.b64decode(validation.parse_image_b64(body.get("image")))
    return run("Image edit", lambda retries: services().image_gen.edit(image, prompt, retries))


@bp.post("/api/social/validate")
def social_validate() -> Any:
    return run("Meta validate", lambda retries: {"account": services().social.meta.validate_token(retries)})


@bp.post("/api/social/connect")
def social_connect() -> Any:
    meta = services().social.meta
    return run("Meta connect", lambda retries: {**meta.connect(retries), "tokenId": meta.token_id})


@bp.post("/api/social/schedule")
def social_schedule() -> Any:
    """Queues the post on each channel; the publisher publishes it when it falls due."""
    body = validation.json_body()
    text = validation.parse_post_text(body.get("text"))
    channel_ids = validation.parse_channel_ids(body.get("channelIds"))
    due_at = validation.parse_due_at(body.get("dueAt"))
    media_url = validation.parse_media_url(body.get("mediaUrl"))
    request_key = validation.parse_request_key(body.get("requestKey"))
    jpeg_for = served_as_jpeg()
    return run("Schedule", lambda retries: services().social.schedule(text, channel_ids, due_at, media_url, request_key,
                                                                       retries, jpeg_for))


@bp.post("/api/social/posts")
def social_posts() -> Any:
    """A page of the queued and published posts."""
    body = validation.json_body()
    status_filter = validation.parse_post_filter(body.get("filter"))
    after = validation.parse_cursor(body.get("after"))
    thumbnails = stored_thumbnails()
    return run("Posts", lambda _retries: services().social.list_posts(status_filter, after, thumbnails))


@bp.post("/api/social/posts/edit")
def social_posts_edit() -> Any:
    """Changes the text and/or publish time of posts that are not published yet."""
    body = validation.json_body()
    post_ids = validation.parse_post_ids(body.get("postIds"))
    text = validation.parse_post_text(body["text"]) if body.get("text") is not None else None
    due_at = validation.parse_due_at(body["dueAt"]) if body.get("dueAt") is not None else None
    if text is None and due_at is None:
        raise ServiceError('Nothing to change: send a new "text" or "dueAt".', kind="validation")
    thumbnails = stored_thumbnails()
    return run("Edit", lambda _retries: services().social.edit_posts(post_ids, text, due_at, thumbnails))


@bp.post("/api/social/posts/delete")
def social_posts_delete() -> Any:
    """Deletes posts that are not published yet."""
    post_ids = validation.parse_post_ids(validation.json_body().get("postIds"))
    return run("Delete", lambda _retries: services().social.delete_posts(post_ids))


@bp.post("/api/activity")
def activity_save() -> Any:
    """Saves activity log entries the dashboard recorded. An entry sent again (after a lost answer) is stored once."""
    entries = validation.parse_activity_entries(validation.json_body().get("entries"))
    return run("Activity", lambda _retries: services().activity.save(entries))


@bp.post("/api/activity/list")
def activity_list() -> Any:
    """A page of the activity log, newest first."""
    body = validation.json_body()
    name = validation.parse_activity_filter(body.get("filter"))
    after = validation.parse_cursor(body.get("after"))
    return run("Activity", lambda _retries: services().activity.page(name, after))


@bp.post("/api/activity/clear")
def activity_clear() -> Any:
    """Deletes every activity log entry."""
    return run("Activity", lambda _retries: services().activity.clear())


@bp.get("/api/publish/due")
def publish_due() -> Any:
    """Publishes the posts that are due. A scheduler calls it every minute with ``Authorization: Bearer
    <CRON_SECRET>``: cron-job.org or similar on Vercel's Hobby plan, Vercel Cron on Pro. It needs no
    dashboard password (see auth.PUBLIC_PATHS), only the secret."""
    secret = services().config.cron_secret
    if not secret:
        raise ServiceError("CRON_SECRET is not set on the server, so the publisher cannot be called.", kind="config",
                           service="Publisher", hint="Set CRON_SECRET to a long random value and send it as a Bearer token.")
    auth = request.headers.get("Authorization", "")
    supplied = auth[7:] if auth[:7].lower() == "bearer " else ""
    if not hmac.compare_digest(supplied.encode(), secret.encode()):
        return jsonify(error={"message": "Send the CRON_SECRET as a Bearer token.", "kind": "auth"}), 401
    return run("Publish", lambda retries: services().social.publish_due(retries))


@bp.post("/api/media/upload")
def media_upload() -> Any:
    file = request.files.get("file")
    if file is None or not file.filename:
        raise ServiceError('Send the image as a multipart "file" field.', kind="validation")
    data = file.read(LIMITS["max_image_bytes"] + 1)
    if len(data) > LIMITS["max_image_bytes"]:
        raise ServiceError("The image is larger than 20 MB.", kind="validation")
    mimetype = sniff_image(data)
    if not mimetype:
        raise ServiceError("The file is not a JPG, PNG or WEBP image.", kind="validation")
    filename = re.sub(r"[^\w.\-]", "_", file.filename)[:120] or "image"
    media = services().media
    if not media.uses_blob:
        return run("Media upload", lambda retries: {"url": media.upload(filename, data, mimetype, retries)})
    # Vercel Blob keeps a thumbnail and an AVIF copy, and the post gets this server's URL for the image.
    base_url = public_base_url()

    def store(retries: RetryLog) -> dict[str, Any]:
        stored = media.store_packed(filename, data, retries)
        image_file = stored["name"] + IMAGE_EXTENSIONS[mimetype]
        return {"url": base_url + url_for("poster.media_image", filename=image_file), **stored}

    return run("Media upload", store)


@bp.get(f"{MEDIA_IMAGE_PATH}<filename>")
def media_image(filename: str) -> Any:
    """Serves an uploaded image from its AVIF copy on Vercel Blob. Facebook and Instagram fetch post images from here.
    Takes the name /api/media/upload returned, with .jpg, .png or .webp to get the image converted to
    that format, or alone or with .avif to get the AVIF copy."""
    match = _IMAGE_FILE_RE.fullmatch(filename.lower())
    if not match:
        raise ServiceError("Image file names contain only letters, numbers and dashes.", kind="validation")
    if request.query_string:
        # One URL per image, so a CDN cannot be made to convert the same image again under new cache keys.
        return redirect(request.path, 308)
    mimetype = _IMAGE_FILE_TYPES[match[2]]
    try:
        with time_budget(services().config.request_budget):
            data = services().media.load_packed(match[1], mimetype, RetryLog(f"Media image ({filename})"))
    except ServiceError as error:
        if error.kind == "not_found":
            raise NotFound(f'There is no stored image named "{match[1]}".') from error
        raise
    response = current_app.response_class(data, mimetype=mimetype)
    # Every upload gets a new name, so the image behind a URL never changes.
    response.headers["Cache-Control"] = IMMUTABLE
    return response


def log_startup(app: Flask) -> None:
    s = cast(Services, app.extensions["poster"])
    named: tuple[tuple[str, Any], ...] = (("Groq", s.groq), ("Meta", s.social.meta), ("Database", s.social.store),
                                          ("Media host", s.media), ("Pollinations", s.image_gen))
    for name, service in named:
        if service.configured:
            log.info("%s: configured", name)
        else:
            log.warning("%s: not configured (%s)", name, service.problem)
    if not s.config.password:
        log.warning("POSTER_PASSWORD is not set, so only requests to a local address (such as 127.0.0.1) are served.")
    with app.app_context():
        log.info("Default mode: %s", default_mode())


def start_local_publisher(app: Flask, interval: int = 60) -> threading.Thread | None:
    """Publishes due posts every ``interval`` seconds while ``python app.py`` runs. (On Vercel nothing runs
    between requests, so a scheduler calls /api/publish/due instead.) A run that overlaps one from
    elsewhere is safe: each post is claimed atomically before it is published."""
    s = cast(Services, app.extensions["poster"])
    if not s.social.configured:
        log.warning("Publisher: not started (%s)", s.social.problem)
        return None

    def loop() -> None:
        while True:
            with app.app_context():
                try:
                    with time_budget(max(60, interval * 4)):
                        s.social.publish_due(RetryLog("Publish"))
                except ServiceError as error:
                    log.warning("Publisher: %s", error.message)
                except Exception:
                    log.exception("Publisher failed")
            time.sleep(interval)

    thread = threading.Thread(target=loop, name="poster-publisher", daemon=True)
    thread.start()
    log.info("Publisher: publishing due posts every %d s", interval)
    return thread
