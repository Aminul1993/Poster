"""SocialService: Facebook and Instagram posts scheduled in Poster's own queue, and the publisher that
publishes them when they fall due.

Instagram's API publishes immediately and cannot schedule, so Poster keeps the queue itself (PostStore)
for both networks. Something must call publish_due() every minute: on Vercel an outside scheduler that
requests /api/publish/due, and with ``python app.py`` a background thread. What the publisher does goes into
the activity log, since no browser may be open to see it.
"""

from __future__ import annotations

import logging
import re
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any, TypeVar

from poster.activity import ActivityLog
from poster.config import API
from poster.errors import ServiceError, truncate
from poster.retry import RetryCallback, remaining_budget
from poster.services import ConfiguredService
from poster.services.meta import Channel, MetaService
from poster.store import PostStore, Row, iso

log = logging.getLogger("poster")

T = TypeVar("T")
UrlMapper = Callable[[str], str | None]  # image URL -> another URL for it, if any
LAST_RUN = "publisher_last_run"
_HASHTAG_RE = re.compile(r"(?<!\w)#\w+")


def _rules() -> dict[str, Any]:
    rules: dict[str, Any] = API["social"]
    return rules


class SocialService(ConfiguredService):
    """The post queue in the database, published through Meta's Graph API."""

    service = "Meta"
    unconfigured = "Facebook and Instagram are not set up on the server"

    def __init__(self, meta: MetaService, store: PostStore, activity: ActivityLog, *, cron_secret: str = "") -> None:
        self.meta = meta
        self.store = store
        self.activity = activity
        self.cron_secret = cron_secret
        self.problem = meta.problem or store.problem

    def public_info(self) -> dict[str, Any]:
        return {**self.meta.public_info(), "configured": self.configured, "problem": self.problem,
                "database": self.store.public_info(), "cronSecretSet": bool(self.cron_secret)}

    # -- scheduling -----------------------------------------------------------------------------------------------

    @staticmethod
    def instagram_problem(text: str, media_url: str | None) -> str | None:
        """Why Instagram would refuse the post, or None."""
        rules = _rules()
        if not media_url:
            return "Instagram posts need an image."
        if len(text) > rules["instagram_caption_chars"]:
            return f"Instagram captions are limited to {rules['instagram_caption_chars']:,} characters."
        if len(set(_HASHTAG_RE.findall(text.lower()))) > rules["instagram_hashtags"]:
            return f"Instagram allows at most {rules['instagram_hashtags']} hashtags per post."
        return None

    @classmethod
    def channel_problem(cls, channel: Channel | None, text: str, media_url: str | None) -> str | None:
        """Why the post cannot go to this channel, or None."""
        if channel is None:
            return "The server's Meta token cannot post to this channel any more. Reconnect in Settings."
        if channel.problem:
            return channel.problem
        return cls.instagram_problem(text, media_url) if channel.service == "instagram" else None

    def schedule(self, text: str, channel_ids: list[str], due_at: datetime, media_url: str | None, request_key: str | None,
                 on_retry: RetryCallback, jpeg_for: UrlMapper | None = None) -> dict[str, Any]:
        """Queues one post per channel. Channels are independent: the ones that cannot take the post are
        reported while the others are queued. Sending the same ``request_key`` again returns the posts
        already queued for it instead of adding them twice."""
        self.require_configured()
        channels = {c.id: c for c in self.meta.channels(on_retry)}
        now = iso(datetime.now(UTC))
        key = request_key or uuid.uuid4().hex
        rows: list[Row] = []
        failures: list[dict[str, Any]] = []
        for channel_id in channel_ids:
            channel = channels.get(channel_id)
            problem = self.channel_problem(channel, text, media_url)
            if problem or channel is None:
                failures.append({"channelId": channel_id, "message": problem, "uncertain": False})
                continue
            url = media_url
            if url and channel.service == "instagram" and jpeg_for is not None:
                url = jpeg_for(url) or url  # Instagram takes JPEG only
            rows.append({"id": uuid.uuid4().hex, "request_key": key, "channel_id": channel.id, "service": channel.service,
                         "channel_name": channel.name, "text": text, "media_url": url, "due_at": iso(due_at),
                         "status": "scheduled", "created_at": now})
        stored = self.store.insert(rows) if rows else []
        if not stored:
            summary = " · ".join(f"{channels[f['channelId']].name if f['channelId'] in channels else f['channelId']}: "
                                 f"{f['message']}" for f in failures)
            raise ServiceError(f"The post was not scheduled. {summary}", kind="client", service=self.service)
        log.info("Queued post for %s on %d channel(s)", iso(due_at), len(stored))
        return {"posts": [{"channelId": row["channel_id"], "postId": row["id"], "dueAt": row["due_at"], "status": row["status"]}
                          for row in stored],
                "failures": failures}

    # -- the posts list -------------------------------------------------------------------------------------------

    @staticmethod
    def post_summary(row: Row, thumbnail_for: UrlMapper | None = None) -> dict[str, Any]:
        """What the browser shows for a post. ``thumbnail_for`` swaps the image for a smaller stored thumbnail
        when it knows one."""
        status = str(row["status"])
        media = row.get("media_url")
        https_media = media if isinstance(media, str) and media.startswith("https://") else None
        thumbnail = (thumbnail_for(https_media) if thumbnail_for and https_media else None) or https_media
        link = row.get("permalink")
        return {
            "postId": str(row["id"]), "status": status, "published": status in _rules()["published_statuses"],
            "dueAt": row.get("due_at") or None, "sentAt": row.get("sent_at") or None, "createdAt": row.get("created_at") or None,
            "text": str(row.get("text") or ""), "channelId": str(row.get("channel_id") or ""),
            "channelName": str(row.get("channel_name") or ""), "service": str(row.get("service") or ""),
            "thumbnail": thumbnail, "externalLink": link if isinstance(link, str) and link.startswith("https://") else None,
            "error": row.get("error") or None, "attempts": int(row.get("attempts") or 0),
        }

    def list_posts(self, status_filter: str, after: int | None, thumbnail_for: UrlMapper | None = None) -> dict[str, Any]:
        """One page of posts from the queue. ``after`` is the previous page's ``next``."""
        rules = _rules()
        size = rules["posts_page_size"]
        offset = after or 0
        rows = self.store.page(rules["post_filters"][status_filter], rules["post_sort"][status_filter], offset, size + 1)
        return {
            "posts": [self.post_summary(row, thumbnail_for) for row in rows[:size]],
            "next": str(offset + size) if len(rows) > size else None,
            "publisher": {"lastRunAt": self.store.get_state(LAST_RUN)},
        }

    def _refusal(self, post_id: str) -> ServiceError:
        """Why a post could not be changed: it is gone, or it is published or being published."""
        row = self.store.get(post_id)
        if row is None:
            return ServiceError("This post is no longer in the queue.", kind="not_found", service=self.service)
        if row["status"] == "sending":
            return ServiceError("This post is being published right now, so it can no longer be changed.", kind="conflict",
                                service=self.service)
        return ServiceError("This post has already been published, so it can no longer be changed.", kind="conflict",
                            service=self.service)

    @staticmethod
    def each_post(post_ids: list[str], action: Callable[[str], T]) -> tuple[list[T], list[dict[str, str]]]:
        """Runs ``action`` on each post. Posts are independent: failures on some are reported, with a
        reason (published, missing or error), while the others still go through."""
        done: list[T] = []
        failures: list[dict[str, str]] = []
        for post_id in post_ids:
            try:
                done.append(action(post_id))
            except ServiceError as error:
                if error.kind in ("config", "server") and error.service == "Database" and not done:
                    raise
                reason = {"conflict": "published", "not_found": "missing"}.get(error.kind, "error")
                failures.append({"postId": post_id, "message": error.message, "reason": reason})
        return done, failures

    def edit_posts(self, post_ids: list[str], text: str | None, due_at: datetime | None,
                   thumbnail_for: UrlMapper | None = None) -> dict[str, Any]:
        """Changes the text and/or publish time of posts that are not published yet. A post that failed goes
        back in the queue: at its new time, or at the next run when its time has passed."""
        self.store.require_configured()
        changes: dict[str, Any] = {}
        if text is not None:
            changes["text"] = text
        if due_at is not None:
            changes["due_at"] = iso(due_at)
        # A new caption needs a new Instagram container, and a failed post starts its tries over.
        changes.update(status="scheduled", error=None, attempts=0, retry_at=None, container_id=None)

        def edit(post_id: str) -> dict[str, Any]:
            if text is not None:
                current = self.store.get(post_id)
                problem = (self.instagram_problem(text, current["media_url"])
                           if current is not None and current["service"] == "instagram" else None)
                if problem:
                    raise ServiceError(problem, kind="validation", service=self.service)
            row = self.store.update_editable(post_id, changes)
            if row is None:
                raise self._refusal(post_id)
            return self.post_summary(row, thumbnail_for)

        posts, failures = self.each_post(post_ids, edit)
        log.info("Edited %d post(s) (%s); %d failed", len(posts), ", ".join(k for k in ("text", "due_at") if k in changes),
                 len(failures))
        return {"posts": posts, "failures": failures}

    def delete_posts(self, post_ids: list[str]) -> dict[str, Any]:
        """Deletes posts that are not published yet. A post that is already gone counts as deleted."""
        self.store.require_configured()

        def delete(post_id: str) -> str:
            if self.store.delete_editable(post_id):
                return post_id
            error = self._refusal(post_id)
            if error.kind == "not_found":
                return post_id
            raise error

        deleted, failures = self.each_post(post_ids, delete)
        log.info("Deleted %d post(s); %d failed", len(deleted), len(failures))
        return {"deleted": deleted, "failures": failures}

    # -- the publisher --------------------------------------------------------------------------------------------

    def publish_due(self, on_retry: RetryCallback, now: datetime | None = None) -> dict[str, int]:
        """Publishes the posts that are due, after settling posts a cut-off run left sending. Every post is
        claimed atomically first, so runs that overlap never publish one twice."""
        self.require_configured()
        rules = _rules()
        now = now or datetime.now(UTC)
        stale_before = now - timedelta(minutes=rules["stale_minutes"])
        summary = {"published": 0, "retrying": 0, "failed": 0, "checking": 0}
        self.store.set_state(LAST_RUN, iso(now))
        stale = self.store.stale_ids(stale_before, rules["publish_batch"])
        due = self.store.due_ids(now, rules["publish_batch"])
        if not stale and not due:
            return summary
        channels = {c.id: c for c in self.meta.channels(on_retry)}
        for post_id in stale:
            row = self.store.reclaim(post_id, now, stale_before)
            if row is not None:
                self._settle(row, channels.get(row["channel_id"]), now, summary, on_retry)
        for post_id in due:
            left = remaining_budget()
            if left is not None and left < rules["min_publish_seconds"]:
                break  # the next run takes the rest
            row = self.store.claim(post_id, now)
            if row is not None:
                self._publish(row, channels.get(row["channel_id"]), now, summary, on_retry)
        log.info("Publisher: %s", ", ".join(f"{count} {what}" for what, count in summary.items()))
        return summary

    def _publish(self, row: Row, channel: Channel | None, now: datetime, summary: dict[str, int],
                 on_retry: RetryCallback) -> None:
        problem = self.channel_problem(channel, row["text"], row["media_url"])
        if problem or channel is None:
            self._give_up(row, problem or "", summary)
            return
        try:
            if channel.service == "facebook":
                result = self.meta.publish_facebook(channel, row["text"], row["media_url"], on_retry)
            else:
                result = self.meta.publish_instagram(
                    channel, row["text"], row["media_url"], row["container_id"],
                    lambda container: self.store.update(row["id"], container_id=container), on_retry)
        except ServiceError as error:
            self._after_failure(row, error, now, summary)
            return
        self._mark_sent(row, result, now, summary)

    def _mark_sent(self, row: Row, result: dict[str, Any], now: datetime, summary: dict[str, int]) -> None:
        published = result.get("publishedAt")
        self.store.update(row["id"], status="sent", sent_at=iso(published if isinstance(published, datetime) else now),
                          remote_id=result.get("remoteId"), permalink=result.get("permalink"), error=None, retry_at=None)
        summary["published"] += 1
        log.info("Published %s post %s on %s", row["service"], row["id"], row["channel_name"])
        link = result.get("permalink")
        self.activity.record("success", "publish", f"{self._label(row)}: published.", link if isinstance(link, str) else None)

    def _give_up(self, row: Row, message: str, summary: dict[str, int]) -> None:
        self.store.update(row["id"], status="error", error=message, retry_at=None)
        summary["failed"] += 1
        log.warning("Gave up on %s post %s: %s", row["service"], row["id"], message)
        self.activity.record("error", "publish", f"{self._label(row)}: not published.", message)

    def _retry_later(self, row: Row, message: str, now: datetime, summary: dict[str, int]) -> None:
        rules = _rules()
        attempts = int(row["attempts"] or 0)
        if attempts >= rules["max_attempts"]:
            self._give_up(row, f"{message} (gave up after {attempts} tries)", summary)
            return
        wait = rules["retry_minutes"][min(max(attempts - 1, 0), len(rules["retry_minutes"]) - 1)]
        retry_at = now + timedelta(minutes=wait)
        self.store.update(row["id"], status="scheduled", retry_at=iso(retry_at),
                          error=f"{message} Poster tries again after {iso(retry_at)}.")
        summary["retrying"] += 1
        self.activity.record("warn", "retry", f"{self._label(row)}: not published yet; trying again in {wait} minutes.", message)

    @staticmethod
    def _label(row: Row) -> str:
        """A post as the activity log names it: 'My Page (Facebook) post "Hello world…"'."""
        service = {"facebook": "Facebook", "instagram": "Instagram"}.get(row["service"], row["service"])
        return f'{row["channel_name"]} ({service}) post "{truncate(" ".join(str(row["text"]).split()), 40)}"'

    def _after_failure(self, row: Row, error: ServiceError, now: datetime, summary: dict[str, int]) -> None:
        if error.uncertain:
            # It may have gone out: stay "sending", so the run after the stale period checks before trying again.
            self.store.update(row["id"], error=f"{error.message} Checking whether it was published.")
            summary["checking"] += 1
        elif error.kind == "auth" and error.status == 401:
            # The token itself is bad, so every post would fail: keep this one queued and stop the run.
            self.store.update(row["id"], status="scheduled", attempts=max(0, int(row["attempts"] or 1) - 1), error=error.message)
            raise error
        elif error.retryable:
            self._retry_later(row, error.message, now, summary)
        else:
            self._give_up(row, error.message, summary)

    def _settle(self, row: Row, channel: Channel | None, now: datetime, summary: dict[str, int],
                on_retry: RetryCallback) -> None:
        """Finds out whether a post left sending went out: then it is marked sent, otherwise it is retried."""
        if channel is None or channel.problem:
            self._give_up(row, self.channel_problem(channel, row["text"], row["media_url"]) or "", summary)
            return
        claimed = datetime.fromisoformat(str(row["claimed_at"]).replace("Z", "+00:00"))
        try:
            if row["service"] == "instagram" and row["container_id"]:
                state, _ = self.meta.container_state(channel, row["container_id"], on_retry)
                if state == "PUBLISHED":
                    found = self.meta.find_published(channel, row["text"], claimed - timedelta(minutes=2), on_retry)
                    self._mark_sent(row, found or {}, now, summary)
                    return
                if state not in ("FINISHED", "IN_PROGRESS"):
                    self.store.update(row["id"], container_id=None)
            else:
                found = self.meta.find_published(channel, row["text"], claimed - timedelta(minutes=2), on_retry)
                if found:
                    self._mark_sent(row, found, now, summary)
                    return
        except ServiceError as error:
            log.warning("Could not check %s post %s yet: %s", row["service"], row["id"], error.message)
            summary["checking"] += 1
            return
        self._retry_later(row, "The post did not go out.", now, summary)
