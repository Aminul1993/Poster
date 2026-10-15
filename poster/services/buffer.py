"""Buffer's GraphQL API: token check, channels, and creating, listing, editing and deleting posts."""

from __future__ import annotations

import hashlib
import logging
import re
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, TypeVar
from urllib.parse import urlparse

from poster.config import API, Config, check_url, is_placeholder
from poster.errors import ServiceError, truncate
from poster.http import HttpClient
from poster.retry import RetryCallback, browser_timeout, retry_request, worst_case_seconds
from poster.services import ConfiguredService

log = logging.getLogger("poster")

T = TypeVar("T")
ThumbnailFinder = Callable[[str], str | None]  # image URL -> URL of a smaller stored thumbnail, if any


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def utc_iso(value: datetime) -> str:
    """ISO 8601 in UTC with a Z suffix, as Buffer's DateTime fields take it."""
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _create_post_retryable(error: ServiceError) -> bool:
    """createPost is not idempotent: a timeout or 5xx may still have created the
    post. Only failures that guarantee nothing was created are retried."""
    return error.kind == "rate_limit" or error.status in (429, 503)


def _may_have_created(error: ServiceError) -> bool:
    """Whether a failed createPost may still have created the post: the request may have reached Buffer."""
    return error.kind in ("timeout", "network") or (error.status >= 500 and error.status != 503)


class BufferService(ConfiguredService):
    """Buffer GraphQL API with a personal access token."""

    service = "Buffer"
    unconfigured = "Buffer is not configured on the server"

    def __init__(self, http: HttpClient, config: Config) -> None:
        self.http = http
        self.token = "" if is_placeholder(config.buffer_access_token) else config.buffer_access_token
        self.api_url = config.buffer_api_url
        self.timeout = config.buffer_timeout
        self.budget = config.request_budget
        self.problem = self._check_config()

    def _check_config(self) -> str | None:
        if not self.token:
            return "BUFFER_ACCESS_TOKEN is not set."
        if re.search(r"\s", self.token):
            return "BUFFER_ACCESS_TOKEN contains whitespace."
        return check_url(self.api_url, "BUFFER_API_URL", https_only=True)

    @property
    def token_id(self) -> str:
        """Short, non-reversible fingerprint so the browser can tell when the token changed."""
        return hashlib.sha256(self.token.encode()).hexdigest()[:12] if self.token else ""

    def public_info(self) -> dict[str, Any]:
        return {
            "configured": self.configured, "problem": self.problem, "tokenId": self.token_id,
            "host": urlparse(self.api_url).netloc, "timeout": self.timeout,
            "requestTimeout": browser_timeout(worst_case_seconds(self.timeout) + 10, self.budget),
        }

    def graphql(self, query: str, variables: dict[str, Any], *, action: str, on_retry: RetryCallback,
                should_retry: Callable[[ServiceError], bool] | None = None) -> dict[str, Any]:
        """Runs a GraphQL document. GraphQL errors (which may arrive with HTTP
        200) are mapped inside the retried function, so rate limits and
        transient server errors are retried like HTTP failures."""
        self.require_configured()

        def execute() -> dict[str, Any]:
            _, data, text = self.http.request(
                "POST", self.api_url, service=self.service, timeout=self.timeout,
                headers={"Content-Type": "application/json", "Accept": "application/json",
                         "Authorization": f"Bearer {self.token}"},
                json={"query": query, "variables": variables},
            )
            if not isinstance(data, dict):
                raise ServiceError(f"Buffer returned an invalid response while trying to {action}.", kind="parse",
                                   service=self.service, details=truncate(text, 300))
            errors = data.get("errors")
            if isinstance(errors, list) and errors:
                raise self.map_graphql_error(errors[0], action)
            payload = data.get("data")
            if not isinstance(payload, dict):
                raise ServiceError(f"Buffer returned no data while trying to {action}.", kind="parse", service=self.service,
                                   details=truncate(text, 300))
            return payload

        return retry_request(execute, should_retry=should_retry, on_retry=on_retry)

    def map_graphql_error(self, error: Any, action: str) -> ServiceError:
        error = error if isinstance(error, dict) else {}
        extensions = error.get("extensions") if isinstance(error.get("extensions"), dict) else {}
        code = str(extensions.get("code") or "").upper()
        message = error.get("message") if isinstance(error.get("message"), str) and error.get("message") else "unknown error"
        if code in ("UNAUTHORIZED", "UNAUTHENTICATED"):
            return ServiceError(f"Authentication failed. Buffer rejected the access token ({message}).", kind="auth",
                                status=401, service=self.service,
                                hint="Create a personal API key in Buffer and set BUFFER_ACCESS_TOKEN on the server.")
        if code == "FORBIDDEN":
            return ServiceError(f"Buffer denied access while trying to {action}: {message}.", kind="auth", status=403,
                                service=self.service, hint="Check that the channel and organization belong to this account.")
        if code == "NOT_FOUND":
            return ServiceError(f"Buffer could not find what was needed to {action}: {message}. The channel or organization "
                                "may have been removed.", kind="not_found", service=self.service)
        if code == "RATE_LIMIT_EXCEEDED":
            return ServiceError(f"Buffer rate limit reached while trying to {action}.", kind="rate_limit", status=429,
                                service=self.service, hint="Wait a minute and try again.")
        if code == "UNEXPECTED":
            return ServiceError(f"Buffer had a temporary problem while trying to {action}: {message}.", kind="server",
                                status=500, service=self.service)
        return ServiceError(f"Buffer could not {action}: {message}.", kind="client", service=self.service)

    def validate_token(self, on_retry: RetryCallback) -> dict[str, Any]:
        """Checks the token by reading the account and its organizations."""
        data = self.graphql(API["buffer"]["queries"]["account"], {}, action="verify the access token", on_retry=on_retry)
        account = data.get("account")
        if not isinstance(account, dict) or not isinstance(account.get("organizations"), list):
            raise ServiceError("Buffer returned unexpected account data.", kind="parse", service=self.service)
        return {
            "id": str(account.get("id") or ""),
            "email": str(account.get("email") or ""),
            "organizations": [{"id": str(o["id"]), "name": str(o.get("name") or "Organization")}
                              for o in account["organizations"] if isinstance(o, dict) and o.get("id")],
        }

    def connect(self, on_retry: RetryCallback) -> dict[str, Any]:
        """Validates the token and loads every channel of every organization."""
        account = self.validate_token(on_retry)
        if not account["organizations"]:
            raise ServiceError("This Buffer account has no organizations, so there are no channels to post to.",
                               kind="client", service=self.service)
        channels = []
        for organization in account["organizations"]:
            data = self.graphql(API["buffer"]["queries"]["channels"], {"organizationId": organization["id"]},
                                action="load channels", on_retry=on_retry)
            items = data.get("channels")
            if not isinstance(items, list):
                raise ServiceError("Buffer returned an unexpected channel list.", kind="parse", service=self.service)
            for channel in items:
                if not isinstance(channel, dict) or not channel.get("id"):
                    continue
                channels.append({
                    "id": str(channel["id"]),
                    "name": str(channel.get("displayName") or channel.get("name") or channel["id"]),
                    "handle": str(channel.get("name") or ""),
                    "service": str(channel.get("service") or ""),
                    "timezone": str(channel.get("timezone") or ""),
                    "organizationId": organization["id"],
                    "organizationName": organization["name"],
                    "isDisconnected": bool(channel.get("isDisconnected")),
                    "isLocked": bool(channel.get("isLocked")),
                    "isQueuePaused": bool(channel.get("isQueuePaused")),
                    "usable": not channel.get("isDisconnected") and not channel.get("isLocked"),
                })
        return {"account": account, "channels": channels}

    def schedule(self, text: str, channel_ids: list[str], due_at: datetime, media_url: str | None,
                 on_retry: RetryCallback) -> dict[str, Any]:
        """Creates one scheduled post per channel. Channels are independent:
        failures on some are reported while the others still go out. A failure
        marked ``uncertain`` may still have created the post in Buffer."""
        self.require_configured()
        posts: list[dict[str, str]] = []
        failures: list[dict[str, Any]] = []
        due_iso = utc_iso(due_at)
        for channel_id in channel_ids:
            post_input: dict[str, Any] = {
                "channelId": channel_id, "text": text, "schedulingType": "automatic",
                "mode": "customScheduled", "dueAt": due_iso, "aiAssisted": True,
            }
            if media_url:
                post_input["assets"] = [{"image": {"url": media_url}}]
            try:
                data = self.graphql(API["buffer"]["queries"]["create_post"], {"input": post_input}, action="create the post",
                                    on_retry=on_retry, should_retry=_create_post_retryable)
            except ServiceError as error:
                uncertain = _may_have_created(error)
                if error.kind in ("auth", "config", "network") and not posts:
                    error.uncertain = uncertain or any(f["uncertain"] for f in failures)
                    raise
                failures.append({"channelId": channel_id, "message": error.message, "uncertain": uncertain})
                continue
            result = data.get("createPost")
            post = result.get("post") if isinstance(result, dict) else None
            if isinstance(post, dict) and post.get("id"):
                posts.append({"channelId": channel_id, "postId": str(post["id"]), "dueAt": str(post.get("dueAt") or due_iso),
                              "status": str(post.get("status") or "scheduled")})
            else:
                failures.append({"channelId": channel_id, "message": self.mutation_message(result), "uncertain": False})
        if not posts:
            summary = " · ".join(f"{f['channelId']}: {f['message']}" for f in failures)
            raise ServiceError(f"Buffer did not schedule the post. {summary}", kind="client", service=self.service,
                               uncertain=any(f["uncertain"] for f in failures))
        log.info("Scheduled post for %s on %d channel(s)", due_iso, len(posts))
        return {"posts": posts, "failures": failures}

    @staticmethod
    def mutation_message(result: Any) -> str:
        """Why a mutation did not succeed: Buffer's MutationError message, or the type it returned."""
        if isinstance(result, dict) and result.get("message"):
            return str(result["message"])
        return f"Buffer returned {result.get('__typename') if isinstance(result, dict) else 'an unexpected response'}."

    def post_summary(self, post: Any, thumbnail_for: ThumbnailFinder | None = None) -> dict[str, Any]:
        """What the browser shows for a post read with the PosterPostFields fragment. ``thumbnail_for``
        swaps an image URL for a smaller stored thumbnail when it knows one."""
        if not isinstance(post, dict) or not post.get("id"):
            raise ServiceError("Buffer returned unexpected post data.", kind="parse", service=self.service)
        status = str(post.get("status") or "")
        channel = _dict(post.get("channel"))
        assets = _list(post.get("assets"))
        # The first https image: each asset's thumbnail, else its source; a stored thumbnail beats both.
        images = [url for a in assets if isinstance(a, dict) for url in (a.get("thumbnail"), a.get("source"))
                  if isinstance(url, str) and url.startswith("https://")]
        stored = next(filter(None, map(thumbnail_for, images)), None) if thumbnail_for else None
        thumbnail = stored or next(iter(images), None)
        link = post.get("externalLink")
        error = _dict(post.get("error"))
        return {
            "postId": str(post["id"]), "status": status, "published": status in API["buffer"]["published_statuses"],
            "dueAt": post.get("dueAt") or None, "sentAt": post.get("sentAt") or None, "createdAt": post.get("createdAt") or None,
            "text": str(post.get("text") or ""), "channelId": str(post.get("channelId") or ""),
            "channelName": str(channel.get("displayName") or channel.get("name") or ""),
            "service": str(post.get("channelService") or ""), "thumbnail": thumbnail,
            "externalLink": link if isinstance(link, str) and link.startswith("https://") else None,
            "error": str(error.get("message") or "") or None,
        }

    def graphql_posts(self, query: str, variables: dict[str, Any], **options: Any) -> dict[str, Any]:
        """Runs a document that uses the PosterPostFields fragment."""
        return self.graphql(f"{query} {API['buffer']['post_fields']}", variables, **options)

    def list_posts(self, organization_ids: list[str], status_filter: str, after: dict[str, str] | None,
                   on_retry: RetryCallback, thumbnail_for: ThumbnailFinder | None = None) -> dict[str, Any]:
        """One page of posts per organization, read straight from Buffer. ``after`` holds each organization's
        cursor from the previous page; the result's ``next`` does the same for the page after this one."""
        self.require_configured()
        if not organization_ids:
            organization_ids = [org["id"] for org in self.validate_token(on_retry)["organizations"]]
        statuses = API["buffer"]["post_filters"][status_filter]
        direction = API["buffer"]["post_sort"][status_filter]
        posts: list[dict[str, Any]] = []
        cursors: dict[str, str] = {}
        for organization_id in organization_ids:
            if after is not None and organization_id not in after:
                continue  # no more pages there
            post_input: dict[str, Any] = {
                "organizationId": organization_id,
                "sort": [{"field": "dueAt", "direction": direction}, {"field": "createdAt", "direction": "desc"}],
            }
            if statuses:
                post_input["filter"] = {"status": statuses}
            data = self.graphql_posts(
                API["buffer"]["queries"]["posts"],
                {"input": post_input, "first": API["buffer"]["posts_page_size"], "after": (after or {}).get(organization_id)},
                action="load posts", on_retry=on_retry,
            )
            result = data.get("posts")
            if not isinstance(result, dict) or not isinstance(result.get("edges"), list):
                raise ServiceError("Buffer returned an unexpected post list.", kind="parse", service=self.service)
            posts.extend(self.post_summary(edge.get("node"), thumbnail_for) for edge in result["edges"] if isinstance(edge, dict))
            page = _dict(result.get("pageInfo"))
            if page.get("hasNextPage") and page.get("endCursor"):
                cursors[organization_id] = str(page["endCursor"])
        posts.sort(key=lambda p: p["dueAt"] or p["createdAt"] or "", reverse=direction == "desc")
        return {"posts": posts, "next": cursors or None}

    def require_unpublished(self, post_id: str, on_retry: RetryCallback) -> None:
        """Reads the post's status from Buffer and refuses it if it is published or publishing right now."""
        data = self.graphql(API["buffer"]["queries"]["post_status"], {"input": {"id": post_id}},
                            action="load the post", on_retry=on_retry)
        post = data.get("post")
        status = str(post.get("status") or "") if isinstance(post, dict) else ""
        if status == "sending":
            raise ServiceError("Buffer is publishing this post right now, so it can no longer be changed.",
                               kind="conflict", service=self.service)
        if status in API["buffer"]["published_statuses"]:
            raise ServiceError("This post has already been published, so it can no longer be changed.",
                               kind="conflict", service=self.service)

    def each_post(self, post_ids: list[str], action: Callable[[str], T]) -> tuple[list[T], list[dict[str, str]]]:
        """Runs ``action`` on each post. Posts are independent: failures on some are reported, with a
        reason (published, missing or error), while the others still go through."""
        done: list[T] = []
        failures: list[dict[str, str]] = []
        for post_id in post_ids:
            try:
                done.append(action(post_id))
            except ServiceError as error:
                if error.kind in ("auth", "config", "network") and not done:
                    raise
                reason = {"conflict": "published", "not_found": "missing"}.get(error.kind, "error")
                failures.append({"postId": post_id, "message": error.message, "reason": reason})
        return done, failures

    def edit_posts(self, post_ids: list[str], text: str | None, due_at: datetime | None,
                   on_retry: RetryCallback, thumbnail_for: ThumbnailFinder | None = None) -> dict[str, Any]:
        """Changes the text and/or publish time of posts that are not published yet."""
        self.require_configured()
        changes: dict[str, Any] = {}
        if text is not None:
            changes["text"] = text
        if due_at is not None:
            # Without a mode, Buffer keeps the post's current schedule and ignores the new time.
            changes.update(dueAt=utc_iso(due_at), mode="customScheduled")

        def edit(post_id: str) -> dict[str, Any]:
            self.require_unpublished(post_id, on_retry)
            data = self.graphql_posts(API["buffer"]["queries"]["edit_post"], {"input": {"id": post_id, **changes}},
                                      action="edit the post", on_retry=on_retry)
            result = data.get("editPost")
            if isinstance(result, dict) and isinstance(result.get("post"), dict):
                return self.post_summary(result["post"], thumbnail_for)
            raise ServiceError(f"Buffer did not change the post: {self.mutation_message(result)}", kind="client",
                               service=self.service)

        posts, failures = self.each_post(post_ids, edit)
        log.info("Edited %d post(s) (%s); %d failed", len(posts), ", ".join(changes) or "no changes", len(failures))
        return {"posts": posts, "failures": failures}

    def delete_posts(self, post_ids: list[str], on_retry: RetryCallback) -> dict[str, Any]:
        """Deletes posts that are not published yet. A post that is already gone from Buffer counts as deleted."""
        self.require_configured()

        def delete(post_id: str) -> str:
            try:
                self.require_unpublished(post_id, on_retry)
            except ServiceError as error:
                if error.kind == "not_found":
                    return post_id
                raise
            data = self.graphql(API["buffer"]["queries"]["delete_post"], {"input": {"id": post_id}},
                                action="delete the post", on_retry=on_retry)
            result = data.get("deletePost")
            if isinstance(result, dict) and result.get("id"):
                return post_id
            raise ServiceError(f"Buffer did not delete the post: {self.mutation_message(result)}", kind="client",
                               service=self.service)

        deleted, failures = self.each_post(post_ids, delete)
        log.info("Deleted %d post(s); %d failed", len(deleted), len(failures))
        return {"deleted": deleted, "failures": failures}
