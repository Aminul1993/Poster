"""The Flask app: dashboard page, JSON API, static assets, security headers and error handling."""

from __future__ import annotations

import hashlib
import logging
import mimetypes
import re
import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast
from urllib.parse import urlparse

from flask import Blueprint, Flask, current_app, g, jsonify, redirect, render_template, request, send_from_directory, url_for
from werkzeug.exceptions import HTTPException, NotFound

from poster import validation
from poster.auth import require_login
from poster.config import API, APP_VERSION, BASE_DIR, LIMITS, TONES, Config, check_url, is_local_host
from poster.content import FALLBACK_TAGS, STOP_WORDS
from poster.errors import ServiceError
from poster.http import HttpClient
from poster.imaging import IMAGE_EXTENSIONS, sniff_image
from poster.retry import RetryLog, time_budget
from poster.services.buffer import BufferService, ThumbnailFinder
from poster.services.imagegen import ImageGenService
from poster.services.media import MediaService
from poster.services.ollama import OllamaService

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
    ollama: OllamaService
    buffer: BufferService
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
    app.extensions["poster"] = Services(
        config=config, ollama=OllamaService(http, config), buffer=BufferService(http, config),
        media=MediaService(http, config), image_gen=ImageGenService(http, config), asset_version=asset_version(STATIC_DIR),
    )
    app.register_blueprint(bp)
    return app


def default_mode() -> str:
    mode = services().config.mode
    if mode in ("demo", "live"):
        return mode
    return "live" if services().ollama.configured else "demo"


def server_config() -> dict[str, Any]:
    """Non-secret configuration and the shared content rules, for the browser."""
    s = services()
    retry = API["retry"]
    return {
        "version": APP_VERSION,
        "mode": default_mode(),
        "ollama": s.ollama.public_info(),
        "buffer": s.buffer.public_info(),
        "media": s.media.public_info(),
        "imageGen": s.image_gen.public_info(),
        "limits": {"maxImageBytes": LIMITS["max_image_bytes"], "maxUploadBytes": s.config.max_upload_bytes,
                   "maxChannels": LIMITS["max_channels"]},
        # One source of truth for rules the browser applies too (validation, Demo mode, the posts list).
        "rules": {
            "tones": TONES,
            "hashtags": {"min": LIMITS["min_hashtags"], "max": LIMITS["max_hashtags"], "fallback": list(FALLBACK_TAGS),
                         "stopWords": sorted(STOP_WORDS)},
            "maxTextChars": LIMITS["max_text_chars"],
            "maxExtraChars": LIMITS["max_extra_chars"],
            "maxCategoryChars": LIMITS["max_category_chars"],
            "caption": {"minLow": LIMITS["min_caption_chars"], "minHigh": LIMITS["max_caption_min"],
                        "maxLow": LIMITS["min_caption_max"], "maxHigh": LIMITS["max_caption_chars"],
                        "defaultMin": LIMITS["default_caption_min"], "defaultMax": LIMITS["default_caption_max"]},
            "prompt": {"min": LIMITS["min_prompt_chars"], "max": LIMITS["max_prompt_chars"]},
            "postFilters": API["buffer"]["post_filters"],
            "publishedStatuses": sorted(API["buffer"]["published_statuses"]),
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
        # https: images, because the posts list shows Buffer's thumbnails from wherever each image is hosted
        # (Buffer, Vercel Blob, a media host or a URL pasted in the editor).
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
    """This server's public https:// address. Buffer fetches post images from it (see media_image)."""
    configured = services().config.public_base_url
    if configured:
        problem = check_url(configured, "PUBLIC_BASE_URL", https_only=True)
        if problem:
            raise ServiceError(problem, kind="config", service="Media host")
        return configured.rstrip("/")
    if is_local_host(urlparse(request.host_url).hostname or ""):
        raise ServiceError("Buffer fetches post images from this server, which is running on a local address it cannot reach.",
                           kind="config", service="Media host",
                           hint="Deploy Poster (for example to Vercel), or set PUBLIC_BASE_URL to a public https:// address of this server.")
    return f"https://{request.host}"


def stored_thumbnails() -> ThumbnailFinder | None:
    """Maps an image this server serves from its AVIF copy (see media_image) to its thumbnail on Vercel Blob,
    so the posts list shows the small thumbnail instead of pulling each full image through this server.
    Image URLs start with PUBLIC_BASE_URL, or with this server's own address when it is unset."""
    media = services().media
    if not media.uses_blob or not media.configured:
        return None
    bases = (services().config.public_base_url, f"https://{request.host}")
    prefixes = [f"{base.rstrip('/')}{MEDIA_IMAGE_PATH}" for base in bases if base]

    def find(url: str) -> str | None:
        for prefix in prefixes:
            if url.startswith(prefix):
                match = _IMAGE_FILE_RE.fullmatch(url[len(prefix):].split("?")[0].lower())
                return media.blob_url(f"{match[1]}.thumb.avif") if match else None
        return None

    return find


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
    return jsonify(status="ok", version=APP_VERSION, ollama=s.ollama.configured, buffer=s.buffer.configured,
                   media=s.media.configured, imageGen=s.image_gen.configured)


@bp.get("/api/config")
def config_endpoint() -> Any:
    return jsonify(server_config())


def generation_options(body: dict[str, Any]) -> Any:
    ollama = services().ollama
    return validation.parse_generation_options(body, default_model=ollama.default_model, allowed_models=ollama.allowed_models)


@bp.post("/api/ollama/test")
def ollama_test() -> Any:
    opts = generation_options(validation.json_body())
    return run("Ollama test", lambda retries: services().ollama.test(opts.model, retries))


@bp.post("/api/ollama/generate")
def ollama_generate() -> Any:
    body = validation.json_body()
    opts = generation_options(body)
    image_b64 = validation.parse_image_b64(body.get("image"))
    name = log_safe(body.get("fileName") or "image")
    return run(f"Ollama generate ({name})", lambda retries: services().ollama.generate(image_b64, opts, retries))


@bp.post("/api/image/generate")
def image_generate() -> Any:
    body = validation.json_body()
    prompt = validation.parse_prompt(body.get("prompt"))
    aspect = validation.parse_aspect(body.get("aspect"))
    return run("Image generate", lambda retries: services().image_gen.generate(prompt, aspect, retries))


@bp.post("/api/buffer/validate")
def buffer_validate() -> Any:
    return run("Buffer validate", lambda retries: {"account": services().buffer.validate_token(retries)})


@bp.post("/api/buffer/connect")
def buffer_connect() -> Any:
    buffer = services().buffer
    return run("Buffer connect", lambda retries: {**buffer.connect(retries), "tokenId": buffer.token_id})


@bp.post("/api/buffer/schedule")
def buffer_schedule() -> Any:
    body = validation.json_body()
    text = validation.parse_post_text(body.get("text"))
    channel_ids = validation.parse_channel_ids(body.get("channelIds"))
    due_at = validation.parse_due_at(body.get("dueAt"))
    media_url = validation.parse_media_url(body.get("mediaUrl"))
    return run("Buffer schedule", lambda retries: services().buffer.schedule(text, channel_ids, due_at, media_url, retries))


@bp.post("/api/buffer/posts")
def buffer_posts() -> Any:
    """A page of the account's posts, read straight from Buffer (nothing is stored)."""
    body = validation.json_body()
    status_filter = validation.parse_post_filter(body.get("filter"))
    organization_ids = validation.parse_organization_ids(body.get("organizationIds"))
    after = validation.parse_cursors(body.get("after"))
    thumbnails = stored_thumbnails()
    return run("Buffer posts", lambda retries: services().buffer.list_posts(organization_ids, status_filter, after, retries,
                                                                            thumbnails))


@bp.post("/api/buffer/posts/edit")
def buffer_posts_edit() -> Any:
    """Changes the text and/or publish time of posts that are not published yet."""
    body = validation.json_body()
    post_ids = validation.parse_post_ids(body.get("postIds"))
    text = validation.parse_post_text(body["text"]) if body.get("text") is not None else None
    due_at = validation.parse_due_at(body["dueAt"]) if body.get("dueAt") is not None else None
    if text is None and due_at is None:
        raise ServiceError('Nothing to change: send a new "text" or "dueAt".', kind="validation")
    thumbnails = stored_thumbnails()
    return run("Buffer edit", lambda retries: services().buffer.edit_posts(post_ids, text, due_at, retries, thumbnails))


@bp.post("/api/buffer/posts/delete")
def buffer_posts_delete() -> Any:
    """Deletes posts that are not published yet."""
    post_ids = validation.parse_post_ids(validation.json_body().get("postIds"))
    return run("Buffer delete", lambda retries: services().buffer.delete_posts(post_ids, retries))


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
    # Vercel Blob keeps a thumbnail and an AVIF copy, and Buffer gets this server's URL for the image.
    base_url = public_base_url()

    def store(retries: RetryLog) -> dict[str, Any]:
        stored = media.store_packed(filename, data, retries)
        image_file = stored["name"] + IMAGE_EXTENSIONS[mimetype]
        return {"url": base_url + url_for("poster.media_image", filename=image_file), **stored}

    return run("Media upload", store)


@bp.get(f"{MEDIA_IMAGE_PATH}<filename>")
def media_image(filename: str) -> Any:
    """Serves an uploaded image from its AVIF copy on Vercel Blob. Buffer fetches post images from here.
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
    for name, service in (("Ollama", s.ollama), ("Buffer", s.buffer), ("Media host", s.media), ("Pollinations", s.image_gen)):
        if service.configured:
            log.info("%s: configured", name)
        else:
            log.warning("%s: not configured (%s)", name, service.problem)
    if not s.config.password:
        log.warning("POSTER_PASSWORD is not set, so only requests to a local address (such as 127.0.0.1) are served.")
    with app.app_context():
        log.info("Default mode: %s", default_mode())
