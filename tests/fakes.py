"""In-memory stand-ins for Ollama, Buffer, Vercel Blob, Cloudinary and Pollinations.

FakeUpstream replaces ``requests.Session.request``, so Poster's real HTTP client
(timeouts, error classification, JSON parsing) runs unchanged against it.
"""

from __future__ import annotations

import io
import json
import re
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qs, urlparse

import requests
from PIL import Image

OLLAMA_HOST = "ollama.test"
BUFFER_HOST = "api.buffer.test"
BLOB_API_HOST = "blob.test"
BLOB_STORE = "teststore123"
BLOB_PUBLIC_HOST = f"{BLOB_STORE}.public.blob.vercel-storage.com"
CLOUDINARY_HOST = "upload.media.test"
POLLINATIONS_HOST = "gen.pollinations.ai"

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


@dataclass
class FakeBuffer:
    """Just enough of Buffer's GraphQL API for Poster."""

    email: str = "owner@example.test"
    organizations: list[dict[str, str]] = field(default_factory=lambda: [{"id": "org1", "name": "Org One"}])
    channels: dict[str, list[dict[str, Any]]] = field(default_factory=lambda: {"org1": [
        {"id": "ch1", "name": "poster.studio", "displayName": "Poster Studio", "service": "instagram", "timezone": "UTC",
         "isDisconnected": False, "isLocked": False, "isQueuePaused": False},
        {"id": "ch2", "name": "poster-inc", "displayName": "Poster Inc.", "service": "linkedin", "timezone": "UTC",
         "isDisconnected": False, "isLocked": False, "isQueuePaused": False},
        {"id": "ch3", "name": "old", "displayName": "Old Page", "service": "facebook", "timezone": "UTC",
         "isDisconnected": True, "isLocked": False, "isQueuePaused": False},
    ]})
    posts: dict[str, dict[str, Any]] = field(default_factory=dict)
    next_id: int = 1

    def channel(self, channel_id: str) -> dict[str, Any] | None:
        return next((c for items in self.channels.values() for c in items if c["id"] == channel_id), None)

    def org_of(self, channel_id: str) -> str | None:
        return next((org for org, items in self.channels.items() if any(c["id"] == channel_id for c in items)), None)

    def add_post(self, channel_id: str, text: str, due_at: str, *, status: str = "scheduled", image: str | None = None,
                 created_at: str = "2026-01-01T00:00:00Z") -> dict[str, Any]:
        post_id = f"post{self.next_id}"
        self.next_id += 1
        channel = self.channel(channel_id) or {"name": channel_id, "displayName": channel_id, "service": "instagram"}
        self.posts[post_id] = {
            "id": post_id, "status": status, "dueAt": due_at, "sentAt": due_at if status == "sent" else None,
            "createdAt": created_at, "text": text, "channelId": channel_id, "channelService": channel["service"],
            "externalLink": f"https://social.test/{post_id}" if status == "sent" else None,
            "channel": {"name": channel["name"], "displayName": channel["displayName"]},
            "assets": [{"thumbnail": image, "source": image}] if image else [], "error": None,
        }
        return self.posts[post_id]

    @staticmethod
    def error(code: str, message: str) -> dict[str, Any]:
        return {"errors": [{"message": message, "extensions": {"code": code}}]}

    def handle(self, query: str, variables: dict[str, Any]) -> dict[str, Any]:
        operation = re.search(r"(?:query|mutation) (\w+)", query)
        name = operation.group(1) if operation else ""
        if name == "PosterAccount":
            return {"data": {"account": {"id": "acc1", "email": self.email, "organizations": self.organizations}}}
        if name == "PosterChannels":
            return {"data": {"channels": self.channels.get(variables["organizationId"], [])}}
        if name == "PosterCreatePost":
            data = variables["input"]
            if not self.channel(data["channelId"]):
                return {"data": {"createPost": {"__typename": "InvalidInputError", "message": "Unknown channel"}}}
            image = data["assets"][0]["image"]["url"] if data.get("assets") else None
            post = self.add_post(data["channelId"], data["text"], data["dueAt"], image=image)
            return {"data": {"createPost": {"__typename": "PostActionSuccess",
                                            "post": {"id": post["id"], "dueAt": post["dueAt"], "status": post["status"]}}}}
        if name == "PosterPostStatus":
            post = self.posts.get(variables["input"]["id"])
            if not post:
                return self.error("NOT_FOUND", "Post not found")
            return {"data": {"post": {"id": post["id"], "status": post["status"]}}}
        if name == "PosterPosts":
            return {"data": {"posts": self.page(variables)}}
        if name == "PosterEditPost":
            data = variables["input"]
            post = self.posts.get(data["id"])
            if not post:
                return self.error("NOT_FOUND", "Post not found")
            if "text" in data:
                post["text"] = data["text"]
            if "dueAt" in data:
                post["dueAt"] = data["dueAt"]
            return {"data": {"editPost": {"__typename": "PostActionSuccess", "post": post}}}
        if name == "PosterDeletePost":
            post_id = variables["input"]["id"]
            if self.posts.pop(post_id, None) is None:
                return self.error("NOT_FOUND", "Post not found")
            return {"data": {"deletePost": {"__typename": "DeletePostSuccess", "id": post_id}}}
        return self.error("BAD_REQUEST", f"Unknown operation {name}")

    def page(self, variables: dict[str, Any]) -> dict[str, Any]:
        data = variables["input"]
        statuses = (data.get("filter") or {}).get("status")
        posts = [p for p in self.posts.values()
                 if self.org_of(p["channelId"]) == data["organizationId"] and (not statuses or p["status"] in statuses)]
        reverse = data["sort"][0]["direction"] == "desc"
        posts.sort(key=lambda p: p["dueAt"] or "", reverse=reverse)
        start = int(variables.get("after") or 0)
        first = int(variables.get("first") or 30)
        chunk = posts[start:start + first]
        more = start + first < len(posts)
        return {"edges": [{"node": p} for p in chunk],
                "pageInfo": {"hasNextPage": more, "endCursor": str(start + first) if more else None}}


class FakeUpstream:
    """Routes Poster's outgoing HTTP calls to in-memory fakes. ``fail()`` scripts errors."""

    def __init__(self) -> None:
        self.calls: list[Call] = []
        self.buffer = FakeBuffer()
        self.ollama_replies: deque[str] = deque()
        self.ollama_models = ["gemma4:31b", "llava:13b"]
        self.blobs: dict[str, bytes] = {}
        self.generated_image = image_bytes("PNG", (96, 96), (200, 80, 40))
        self._failures: list[tuple[str, Callable[[Call], requests.Response]]] = []

    # -- scripting ------------------------------------------------------------------------------------------------
    def reply(self, *replies: str | dict[str, Any]) -> None:
        """Queues Ollama replies (message content). Without any, DEFAULT_REPLY is used."""
        for item in replies:
            self.ollama_replies.append(item if isinstance(item, str) else json.dumps(item))

    def fail(self, op: str, status: int = 500, data: Any = None, *, times: int = 1, exc: Exception | None = None,
             headers: dict[str, str] | None = None, after: bool = False) -> None:
        """The next ``times`` calls of ``op`` get this HTTP status (or raise ``exc``). With ``after``, the call
        takes effect first and only its answer is lost (a timeout after Buffer created the post)."""
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
        if host == OLLAMA_HOST:
            return "ollama.tags" if path.endswith("/api/tags") else "ollama.chat"
        if host == BUFFER_HOST:
            query = (kwargs.get("json") or {}).get("query", "")
            match = re.search(r"(?:query|mutation) (\w+)", query)
            return f"buffer.{match.group(1) if match else '?'}"
        if host == BLOB_API_HOST:
            return "blob.put"
        if host == BLOB_PUBLIC_HOST:
            return "blob.get"
        if host == CLOUDINARY_HOST:
            return "media.upload"
        if host == POLLINATIONS_HOST:
            return "pollinations"
        return f"unknown.{host}"

    def dispatch(self, call: Call) -> requests.Response:
        if call.op == "ollama.tags":
            return response(200, {"models": [{"name": name} for name in self.ollama_models]}, url=call.url)
        if call.op == "ollama.chat":
            reply = self.ollama_replies.popleft() if self.ollama_replies else json.dumps(DEFAULT_REPLY)
            if "/v1/chat/completions" in call.url:
                return response(200, {"choices": [{"message": {"content": reply}}]}, url=call.url)
            return response(200, {"message": {"role": "assistant", "content": reply}}, url=call.url)
        if call.op.startswith("buffer."):
            body = call.json or {}
            return response(200, self.buffer.handle(body.get("query", ""), body.get("variables") or {}), url=call.url)
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
            return response(200, content=self.generated_image, headers={"Content-Type": "image/png"}, url=call.url)
        raise requests.ConnectionError(f"FakeUpstream has no route for {call.url}")
