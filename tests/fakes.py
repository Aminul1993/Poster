"""In-memory stand-ins for the text AI providers (Groq, OpenAI, Anthropic, Gemini, Azure OpenAI), the image AI
providers (Pollinations, OpenAI Images, Stability AI, Flux, Gemini Image), Meta's Graph API, Vercel Blob and
Cloudinary.

FakeUpstream replaces ``requests.Session.request``, so Poster's real HTTP client
(timeouts, error classification, JSON parsing) runs unchanged against it.
"""

from __future__ import annotations

import base64
import io
import json
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from urllib.parse import parse_qs, urlparse

import requests
from PIL import Image

GROQ_HOST = "groq.test"
META_HOST = "graph.meta.test"
META_TOKEN = "test-meta-token"
BLOB_API_HOST = "blob.test"
BLOB_STORE = "teststore123"
BLOB_PUBLIC_HOST = f"{BLOB_STORE}.public.blob.vercel-storage.com"
CLOUDINARY_HOST = "upload.media.test"
POLLINATIONS_HOST = "gen.pollinations.ai"
OPENAI_HOST = "api.openai.com"
ANTHROPIC_HOST = "api.anthropic.com"
GEMINI_HOST = "generativelanguage.googleapis.com"
AZURE_HOST = "poster-test.openai.azure.com"
STABILITY_HOST = "api.stability.ai"
BFL_HOST = "api.bfl.ai"
BFL_DELIVERY_HOST = "delivery.eu.bfl.ai"
# A key every fake provider refuses with HTTP 401, as a real one refuses a revoked key.
BAD_KEY = "bad-key-0000000000000000"

DEFAULT_REPLY = {
    "description": "A calm morning by the sea, with soft golden light on the water.",
    "alternative_description": "Saltwater, sunshine and slow mornings by the coast.",
    "hashtags": ["#beach", "#sunrise", "#travel", "#ocean", "#calm", "#morning", "#seaside", "#wanderlust",
                 "#explore", "#nature", "#vacation"],
    "category": "travel",
    "engagement_score": 0.82,
}


def image_bytes(fmt: str = "JPEG", size: tuple[int, int] = (64, 48), color: tuple[int, ...] = (30, 120, 200)) -> bytes:
    """A small, valid image file."""
    mode = "RGBA" if len(color) == 4 else "RGB"
    out = io.BytesIO()
    Image.new(mode, size, color).save(out, fmt)
    return out.getvalue()


def response(status: int = 200, data: Any = None, *, content: bytes | None = None, headers: dict[str, str] | None = None,
             url: str = "") -> requests.Response:
    """A real requests.Response with the given body."""
    resp = requests.Response()
    resp.status_code = status
    resp.url = url
    resp.encoding = "utf-8"
    if content is None:
        content = b"" if data is None else json.dumps(data).encode()
        resp.headers["Content-Type"] = "application/json"
    resp._content = content
    resp.headers.update(headers or {})
    return resp


@dataclass
class Call:
    method: str
    url: str
    op: str
    kwargs: dict[str, Any]

    @property
    def json(self) -> Any:
        return self.kwargs.get("json")


def graph_time(value: datetime | None = None) -> str:
    """A time as the Graph API writes it: 2026-10-08T13:30:00+0000."""
    return (value or datetime.now(UTC)).strftime("%Y-%m-%dT%H:%M:%S+0000")


@dataclass
class FakeMeta:
    """Just enough of Meta's Graph API for Poster: a Page with a linked Instagram account, and a Page the
    token cannot post to. Published posts land in ``feed`` (Facebook) and ``media`` (Instagram)."""

    user: dict[str, str] = field(default_factory=lambda: {"id": "su1", "name": "Poster System User"})
    pages: list[dict[str, Any]] = field(default_factory=lambda: [
        {"id": "pg1", "name": "Poster Studio", "access_token": "page-token-1", "tasks": ["CREATE_CONTENT", "MANAGE"],
         "instagram_business_account": {"id": "ig1", "username": "poster.studio", "name": "Poster Studio IG"}},
        {"id": "pg2", "name": "Read-only Page", "access_token": "page-token-2", "tasks": ["ANALYZE"]},
    ])
    feed: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    media: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    containers: dict[str, dict[str, Any]] = field(default_factory=dict)
    # The status a new Instagram container reports at first (then FINISHED).
    container_status: str = "FINISHED"
    next_id: int = 1

    def page_token(self, object_id: str) -> str | None:
        """The access token that may act on a Page, its Instagram account or their posts."""
        for page in self.pages:
            account = page.get("instagram_business_account") or {}
            if object_id in (page["id"], account.get("id")) or object_id.startswith(f"{page['id']}_"):
                return str(page["access_token"])
        return None

    def new_id(self, prefix: str) -> str:
        value = f"{prefix}{self.next_id}"
        self.next_id += 1
        return value

    @staticmethod
    def error(code: int, message: str, status: int = 400, subcode: int = 0) -> tuple[int, dict[str, Any]]:
        error: dict[str, Any] = {"message": message, "type": "OAuthException", "code": code, "fbtrace_id": "trace1"}
        if subcode:
            error["error_subcode"] = subcode
        return status, {"error": error}

    def all_posts(self) -> list[dict[str, Any]]:
        return [post for posts in (*self.feed.values(), *self.media.values()) for post in posts]

    def handle(self, method: str, path: str, params: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        parts = path.strip("/").split("/")[1:]  # without the version
        token = params.get("access_token")
        if parts[0] == "me":
            if token != META_TOKEN:
                return self.error(190, "Invalid OAuth access token.", 400)
            if parts == ["me"]:
                return 200, self.user
            return 200, {"data": self.pages, "paging": {}}
        target = parts[0]
        expected = self.page_token(target) or self.page_token(self.containers.get(target, {}).get("ig", ""))
        if expected is None:
            if target in self.containers or any(p["id"] == target for p in self.all_posts()):
                expected = self.page_token(self.owner_of(target))
            else:
                return self.error(100, f"Unsupported get request. Object with ID '{target}' does not exist.", subcode=33)
        if token != expected:
            return self.error(190, "Invalid OAuth access token.", 400)
        edge = parts[1] if len(parts) > 1 else ""
        page = next((pg for pg in self.pages if pg["id"] == target), None)
        if method == "POST" and edge in ("photos", "feed") and page is not None:
            if "CREATE_CONTENT" not in page.get("tasks", []):
                return self.error(200, "(#200) The user hasn't authorized the application to perform this action", 403)
            post_id = f"{target}_{self.new_id('p')}"
            self.feed.setdefault(target, []).insert(0, {
                "id": post_id, "message": params.get("message", ""), "created_time": graph_time(),
                "permalink_url": f"https://www.facebook.com/{post_id}", "image": params.get("url")})
            return 200, ({"id": self.new_id("photo"), "post_id": post_id} if edge == "photos" else {"id": post_id})
        if method == "GET" and edge == "posts" and page is not None:
            return 200, {"data": self.feed.get(target, [])}
        if method == "POST" and edge == "media":
            container_id = self.new_id("c")
            self.containers[container_id] = {"ig": target, "image_url": params.get("image_url"),
                                              "caption": params.get("caption", ""), "status_code": self.container_status}
            return 200, {"id": container_id}
        if method == "POST" and edge == "media_publish":
            container = self.containers.get(str(params.get("creation_id")))
            if container is None or container["ig"] != target:
                return self.error(100, "Invalid parameter", subcode=2207001)
            if container["status_code"] != "FINISHED":
                return self.error(9007, "Media ID is not available", subcode=2207027)
            container["status_code"] = "PUBLISHED"
            media_id = self.new_id("m")
            self.media.setdefault(target, []).insert(0, {
                "id": media_id, "caption": container["caption"], "timestamp": graph_time(),
                "permalink": f"https://www.instagram.com/p/{media_id}/", "image": container["image_url"]})
            return 200, {"id": media_id}
        if method == "GET" and edge == "media":
            return 200, {"data": self.media.get(target, [])}
        if method == "GET" and not edge:
            if target in self.containers:
                container = self.containers[target]
                code = container["status_code"]
                if code == "IN_PROGRESS":
                    container["status_code"] = "FINISHED"  # ready on the next look
                return 200, {"id": target, "status_code": code, "status": "Finished: Media has been uploaded"}
            post = next((p for p in self.all_posts() if p["id"] == target), None)
            if post is not None:
                return 200, {"id": target, **{k: post[k] for k in ("permalink_url", "permalink") if k in post}}
        return self.error(100, f"Unknown path {method} {path}")

    def owner_of(self, object_id: str) -> str:
        if object_id in self.containers:
            return str(self.containers[object_id]["ig"])
        for owner, posts in (*self.feed.items(), *self.media.items()):
            if any(p["id"] == object_id for p in posts):
                return owner
        return ""


class FakeUpstream:
    """Routes Poster's outgoing HTTP calls to in-memory fakes. ``fail()`` scripts errors."""

    def __init__(self) -> None:
        self.calls: list[Call] = []
        self.meta = FakeMeta()
        self.groq_replies: deque[str] = deque()
        self.groq_models = ["qwen/qwen3.8-27b", "qwen/qwen3-32b"]
        self.blobs: dict[str, bytes] = {}
        self.generated_image = image_bytes("PNG", (96, 96), (200, 80, 40))
        # Answers to the next generation calls, in order: an image, or an HTTP status to fail with. Once they run
        # out, every call gets generated_image.
        self.generated_images: deque[bytes | int] = deque()
        # The same for image edits; edited_image once they run out.
        self.edited_images: deque[bytes | int] = deque()
        self.edited_image = image_bytes("PNG", (64, 48), (240, 120, 180))
        # Models each text AI provider's fake lists.
        self.provider_models: dict[str, list[str]] = {
            "openai": ["gpt-4.1-mini", "gpt-image-1"], "anthropic": ["claude-sonnet-5-5", "claude-haiku-5-5"],
            "gemini": ["gemini-2.5-flash", "gemini-2.5-flash-image"]}
        # Polls a Flux task answers Pending before it is Ready.
        self.flux_pending = 1
        self._flux_polls: dict[str, int] = {}
        self._failures: list[tuple[str, Callable[[Call], requests.Response]]] = []

    # -- scripting ------------------------------------------------------------------------------------------------
    def reply(self, *replies: str | dict[str, Any]) -> None:
        """Queues Groq replies (message content). Without any, DEFAULT_REPLY is used."""
        for item in replies:
            self.groq_replies.append(item if isinstance(item, str) else json.dumps(item))

    def fail(self, op: str, status: int = 500, data: Any = None, *, times: int = 1, exc: Exception | None = None,
             headers: dict[str, str] | None = None, after: bool = False) -> None:
        """The next ``times`` calls of ``op`` get this HTTP status (or raise ``exc``). With ``after``, the call
        takes effect first and only its answer is lost (a timeout after Meta published the post)."""
        def make(call: Call) -> requests.Response:
            if after:
                self.dispatch(call)
            if exc is not None:
                raise exc
            return response(status, data if data is not None else {"error": f"scripted {status}"}, headers=headers,
                            url=call.url)
        for _ in range(times):
            self._failures.append((op, make))

    def ops(self, prefix: str = "") -> list[str]:
        return [c.op for c in self.calls if c.op.startswith(prefix)]

    # -- dispatch -------------------------------------------------------------------------------------------------
    def session_request(self, _session: requests.Session, method: str, url: str, **kwargs: Any) -> requests.Response:
        call = Call(method.upper(), url, self.classify(method.upper(), url, kwargs), kwargs)
        self.calls.append(call)
        for index, (op, make) in enumerate(self._failures):
            if op == call.op:
                del self._failures[index]
                return make(call)
        return self.dispatch(call)

    @staticmethod
    def classify(method: str, url: str, kwargs: dict[str, Any]) -> str:
        parsed = urlparse(url)
        host, path = parsed.hostname or "", parsed.path
        if host == GROQ_HOST:
            return "groq.models" if path.endswith("/models") else "groq.chat"
        if host == META_HOST:
            parts = path.strip("/").split("/")[1:]
            edge = parts[1] if len(parts) > 1 else ""
            if parts == ["me"]:
                return "meta.me"
            if parts == ["me", "accounts"]:
                return "meta.accounts"
            if edge:
                return f"meta.{edge}" if method == "POST" or edge != "media" else "meta.media_list"
            fields = str((kwargs.get("params") or {}).get("fields", ""))
            return "meta.container" if "status_code" in fields else "meta.object"
        if host == BLOB_API_HOST:
            return "blob.list" if method == "GET" else "blob.put"
        if host == BLOB_PUBLIC_HOST:
            return "blob.get"
        if host == CLOUDINARY_HOST:
            return "media.upload"
        if host == POLLINATIONS_HOST:
            if path == "/image/models":
                return "pollinations.models"
            return "pollinations.edit" if path == "/v1/images/edits" else "pollinations"
        if host == OPENAI_HOST:
            return {"/v1/chat/completions": "openai.chat", "/v1/models": "openai.models",
                    "/v1/images/generations": "openai.images", "/v1/images/edits": "openai.edit"}.get(path, f"openai.{path}")
        if host == ANTHROPIC_HOST:
            return "anthropic.models" if path.startswith("/v1/models") else "anthropic.messages"
        if host == GEMINI_HOST:
            if path.endswith(":generateContent"):
                return "gemini.image" if "image" in path else "gemini.generate"
            return "gemini.models" if path.endswith("/models") else "gemini.model"
        if host == AZURE_HOST:
            return "azure.chat"
        if host == STABILITY_HOST:
            return "stability.account" if path == "/v1/user/account" else "stability.generate"
        if host == BFL_HOST:
            if path == "/v1/credits":
                return "bfl.credits"
            return "bfl.poll" if path == "/v1/get_result" else "bfl.submit"
        if host == BFL_DELIVERY_HOST:
            return "bfl.download"
        return f"unknown.{host}"

    def chat_reply(self) -> str:
        return self.groq_replies.popleft() if self.groq_replies else json.dumps(DEFAULT_REPLY)

    @staticmethod
    def key_of(call: Call) -> str:
        """The API key a call carries, however its provider sends it."""
        headers = call.kwargs.get("headers") or {}
        for name in ("x-api-key", "x-goog-api-key", "api-key", "x-key"):
            if headers.get(name):
                return str(headers[name])
        return str(headers.get("Authorization", "")).removeprefix("Bearer ")

    def providers(self, call: Call) -> requests.Response | None:
        """The text and image AI providers added with Credentials & Integrations."""
        op, url = call.op, call.url
        family = op.split(".")[0]
        if family in ("openai", "anthropic", "gemini", "azure", "stability", "bfl") and op != "bfl.download" \
                and self.key_of(call) == BAD_KEY:
            return response(401, {"error": {"message": "Invalid API key"}}, url=url)
        if op in ("openai.chat", "azure.chat"):
            return response(200, {"choices": [{"message": {"role": "assistant", "content": self.chat_reply()}}]}, url=url)
        if op in ("openai.models", "anthropic.models"):
            return response(200, {"data": [{"id": m} for m in self.provider_models[family]]}, url=url)
        if op == "anthropic.messages":
            return response(200, {"type": "message", "content": [{"type": "text", "text": self.chat_reply()}]}, url=url)
        if op == "gemini.generate":
            return response(200, {"candidates": [{"content": {"role": "model", "parts": [{"text": self.chat_reply()}]}}]},
                            url=url)
        if op == "gemini.models":
            return response(200, {"models": [{"name": f"models/{m}", "supportedGenerationMethods": ["generateContent"]}
                                             for m in self.provider_models["gemini"]]}, url=url)
        if op == "gemini.model":
            return response(200, {"name": urlparse(url).path.split("/v1beta/")[-1]}, url=url)
        if op == "gemini.image":
            image = self.edited_image if "inline_data" in json.dumps(call.json) else self.generated_image
            return response(200, {"candidates": [{"content": {"parts": [
                {"text": "Here it is."}, {"inlineData": {"mimeType": "image/png", "data": base64.b64encode(image).decode()}}]}}]},
                url=url)
        if op in ("openai.images", "openai.edit"):
            image = self.edited_image if op == "openai.edit" else self.generated_image
            return response(200, {"created": 1, "data": [{"b64_json": base64.b64encode(image).decode()}]}, url=url)
        if op == "stability.generate":
            image = self.edited_image if "image" in (call.kwargs.get("files") or {}) else self.generated_image
            return response(200, content=image, headers={"Content-Type": "image/png"}, url=url)
        if op == "stability.account":
            return response(200, {"id": "user-1", "organizations": []}, url=url)
        if op == "bfl.credits":
            return response(200, {"credits": 42.5}, url=url)
        if op == "bfl.submit":
            task = f"task{len(self._flux_polls) + 1}"
            self._flux_polls[task] = 0
            kind = "edit" if (call.json or {}).get("input_image") else "new"
            return response(200, {"id": task, "polling_url": f"https://{BFL_HOST}/v1/get_result?id={task}&kind={kind}"},
                            url=url)
        if op == "bfl.poll":
            query = parse_qs(urlparse(url).query)
            task, kind = query["id"][0], query["kind"][0]
            self._flux_polls[task] += 1
            if self._flux_polls[task] <= self.flux_pending:
                return response(200, {"id": task, "status": "Pending"}, url=url)
            return response(200, {"id": task, "status": "Ready",
                                  "result": {"sample": f"https://{BFL_DELIVERY_HOST}/{task}/{kind}.png?sig=abc"}}, url=url)
        if op == "bfl.download":
            image = self.edited_image if "/edit.png" in url else self.generated_image
            return response(200, content=image, headers={"Content-Type": "image/png"}, url=url)
        if op == "pollinations.models":
            return response(200, [{"name": "flux", "supported_endpoints": ["/image"]},
                                  {"name": "kontext", "supported_endpoints": ["/image", "/v1/images/edits"]},
                                  {"name": "nanobanana", "supported_endpoints": ["/image", "/v1/images/edits"]}], url=url)
        if op == "blob.list":
            return response(200, {"blobs": [], "hasMore": False}, url=url)
        return None

    def dispatch(self, call: Call) -> requests.Response:
        answer = self.providers(call)
        if answer is not None:
            return answer
        if call.op == "groq.models":
            return response(200, {"object": "list", "data": [{"id": name} for name in self.groq_models]}, url=call.url)
        if call.op == "groq.chat":
            return response(200, {"choices": [{"message": {"role": "assistant", "content": self.chat_reply()}}]}, url=call.url)
        if call.op.startswith("meta."):
            params = call.kwargs.get("params") or call.kwargs.get("data") or {}
            status, data = self.meta.handle(call.method, urlparse(call.url).path, dict(params))
            return response(status, data, url=call.url)
        if call.op == "blob.put":
            pathname = parse_qs(urlparse(call.url).query)["pathname"][0]
            public = f"https://{BLOB_PUBLIC_HOST}/{pathname}"
            self.blobs[public] = call.kwargs["data"]
            return response(200, {"url": public, "pathname": pathname}, url=call.url)
        if call.op == "blob.get":
            if call.url not in self.blobs:
                return response(404, {"error": "The requested blob does not exist"}, url=call.url)
            return response(200, content=self.blobs[call.url], headers={"Content-Type": "image/avif"}, url=call.url)
        if call.op == "media.upload":
            return response(200, {"secure_url": "https://res.media.test/poster/uploaded.jpg"}, url=call.url)
        if call.op == "pollinations":
            image = self.generated_images.popleft() if self.generated_images else self.generated_image
            if isinstance(image, int):
                return response(image, {"error": f"scripted {image}"}, url=call.url)
            return response(200, content=image, headers={"Content-Type": "image/png"}, url=call.url)
        if call.op == "pollinations.edit":
            edited = self.edited_images.popleft() if self.edited_images else self.edited_image
            if isinstance(edited, int):
                return response(edited, {"error": f"scripted {edited}"}, url=call.url)
            usage = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0,
                     "input_tokens_details": {"text_tokens": 0, "image_tokens": 0}}
            return response(200, {"created": 1, "data": [{"b64_json": base64.b64encode(edited).decode()}],
                                  "usage": usage}, url=call.url)
        raise requests.ConnectionError(f"FakeUpstream has no route for {call.url}")
