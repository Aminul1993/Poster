"""Media host: gives Buffer a public URL for an image (Buffer only takes public image URLs)."""

from __future__ import annotations

import logging
import re
import secrets
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urlparse

from PIL import features

from poster.config import API, Config, check_url
from poster.errors import ServiceError, truncate
from poster.http import HttpClient
from poster.imaging import make_thumbnail, pack_avif, unpack_avif
from poster.retry import RetryCallback, browser_timeout, retry_request, worst_case_seconds
from poster.services import ConfiguredService

log = logging.getLogger("poster")


class MediaService(ConfiguredService):
    """Gives Buffer a public URL for an image.

    With BLOB_READ_WRITE_TOKEN, a thumbnail and a compressed AVIF copy of the
    image go to Vercel Blob (never the original file), and GET
    /api/media/image/<name>.jpg serves the AVIF copy converted back to JPEG (or
    PNG/WEBP). Otherwise the original goes to MEDIA_UPLOAD_ENDPOINT (e.g. a
    Cloudinary unsigned preset).
    """

    service = "Media host"
    unconfigured = "No media host is configured on the server"
    setup_hint = "Set BLOB_READ_WRITE_TOKEN (Vercel Blob) or MEDIA_UPLOAD_ENDPOINT, or paste a public image URL in the editor."

    def __init__(self, http: HttpClient, config: Config) -> None:
        self.http = http
        self.blob_token = config.blob_read_write_token
        token_parts = self.blob_token.split("_")  # vercel_blob_rw_<store id>_<secret>
        self.blob_store_id = config.blob_store_id.removeprefix("store_") or (token_parts[3] if len(token_parts) > 4 else "")
        self.blob_api_url = config.blob_api_url.rstrip("/")
        self.endpoint = config.media_upload_endpoint
        self.preset = config.media_upload_preset
        self.timeout = config.media_timeout
        self.budget = config.request_budget
        self.problem = self._check_config()

    @property
    def uses_blob(self) -> bool:
        return bool(self.blob_token)

    def _check_config(self) -> str | None:
        if self.uses_blob:
            if not self.blob_token.startswith("vercel_blob_rw_"):
                return "BLOB_READ_WRITE_TOKEN is not a Vercel Blob read-write token (vercel_blob_rw_...)."
            if not re.fullmatch(r"[A-Za-z0-9]{1,64}", self.blob_store_id):
                return "The Blob store ID could not be read from BLOB_READ_WRITE_TOKEN. Set BLOB_STORE_ID."
            if not features.check("avif"):
                return "This Pillow build cannot write AVIF files. Install Pillow 11.3 or newer."
            return check_url(self.blob_api_url, "VERCEL_BLOB_API_URL", https_only=True)
        if not self.endpoint:
            return "MEDIA_UPLOAD_ENDPOINT is not set."
        if self.preset and not re.fullmatch(r"[\w.\-]{1,100}", self.preset):
            return "MEDIA_UPLOAD_PRESET contains invalid characters."
        return check_url(self.endpoint, "MEDIA_UPLOAD_ENDPOINT", https_only=True)

    def public_info(self) -> dict[str, Any]:
        host = "Vercel Blob" if self.uses_blob else urlparse(self.endpoint).netloc if self.endpoint else ""
        return {"configured": self.configured, "problem": self.problem, "host": host,
                "requestTimeout": browser_timeout(worst_case_seconds(self.timeout) + 10, self.budget)}

    def upload(self, filename: str, data: bytes, mimetype: str, on_retry: RetryCallback) -> str:
        """Uploads the original image to MEDIA_UPLOAD_ENDPOINT and returns its public URL."""
        self.require_configured()
        form = {API["media"]["preset_field"]: self.preset} if self.preset else {}
        _, payload, text = retry_request(
            lambda: self.http.request("POST", self.endpoint, service=self.service, timeout=self.timeout,
                                      files={API["media"]["file_field"]: (filename, data, mimetype)}, data=form),
            on_retry=on_retry,
        )
        for path in API["media"]["url_paths"]:
            value: Any = payload
            for key in path.split("."):
                value = value.get(key) if isinstance(value, dict) else None
            if isinstance(value, str) and re.match(r"^https?://", value, re.I):
                if not value.startswith("https://"):
                    raise ServiceError("The media host returned an http:// URL. Buffer needs https:// image URLs.",
                                       kind="client", service=self.service)
                return value
        raise ServiceError("The media host accepted the upload but did not return a public URL.", kind="parse",
                           service=self.service, details=truncate(text, 300))

    def store_packed(self, filename: str, data: bytes, on_retry: RetryCallback) -> dict[str, str]:
        """Saves a thumbnail and an AVIF copy of the image to Vercel Blob, never the original file.
        Returns the stored name, which load_packed() takes, and both files' Blob URLs."""
        self.require_configured()
        thumbnail = make_thumbnail(data)
        avif = pack_avif(data)
        slug = re.sub(r"[^a-z0-9]+", "-", Path(filename).stem.lower()).strip("-")[:60] or "image"
        name = f"{slug}-{secrets.token_hex(6)}"
        avif_url = self.put_blob(f"{name}.avif", avif, "image/avif", on_retry)
        thumbnail_url = self.put_blob(f"{name}.thumb.avif", thumbnail, "image/avif", on_retry)
        log.info("Stored %s on Vercel Blob as %s (%d bytes; AVIF %d bytes, thumbnail %d bytes)", filename, name, len(data),
                 len(avif), len(thumbnail))
        return {"name": name, "file": f"{name}.avif", "avifUrl": avif_url, "thumbnailUrl": thumbnail_url}

    def load_packed(self, name: str, mimetype: str, on_retry: RetryCallback) -> bytes:
        """The image saved by store_packed(), as `mimetype` (see unpack_avif)."""
        if not self.uses_blob:
            raise ServiceError("Vercel Blob is not configured on the server.", kind="config", service=self.service,
                               hint="Set BLOB_READ_WRITE_TOKEN.")
        self.require_configured()
        data = retry_request(
            lambda: self.http.request_bytes("GET", self.blob_url(f"{name}.avif"), service=self.service, timeout=self.timeout),
            on_retry=on_retry,
        )
        return unpack_avif(data, mimetype)

    def put_blob(self, name: str, data: bytes, mimetype: str, on_retry: RetryCallback) -> str:
        """Uploads one file to the Blob folder and returns its public URL."""
        pathname = f"{API['media']['blob_folder']}/{name}"
        url = f"{self.blob_api_url}/?{urlencode({'pathname': pathname})}"
        _, payload, text = retry_request(
            lambda: self.http.request("PUT", url, service=self.service, timeout=self.timeout, data=data,
                                      headers=self.blob_headers(mimetype)),
            on_retry=on_retry,
        )
        blob_url = payload.get("url") if isinstance(payload, dict) else None
        # load_packed() finds files by name alone, so they must be where blob_url() says.
        if not isinstance(blob_url, str) or blob_url.lower() != self.blob_url(name).lower():
            raise ServiceError(f"Vercel Blob did not store {pathname} at the expected public URL.", kind="parse",
                               service=self.service, details=truncate(text, 300))
        return blob_url

    def blob_url(self, name: str) -> str:
        """Public URL of a file in the Blob folder."""
        base = API["media"]["blob_public_url"].format(store_id=self.blob_store_id.lower())
        return f"{base}/{API['media']['blob_folder']}/{name}"

    def blob_headers(self, mimetype: str) -> dict[str, str]:
        """Headers for a Vercel Blob put. Public access, because Buffer's fetch reaches the files without
        credentials. No random suffix: store_packed() picks unique names itself, so load_packed() can find
        a file by name, and a retried put may overwrite its own earlier attempt."""
        headers = {
            "Authorization": f"Bearer {self.blob_token}",
            "x-api-version": API["media"]["blob_api_version"],
            "x-vercel-blob-access": "public",
            "x-content-type": mimetype,
            "x-add-random-suffix": "0",
            "x-allow-overwrite": "1",
        }
        if self.blob_store_id:
            headers["x-vercel-blob-store-id"] = self.blob_store_id
        return headers
