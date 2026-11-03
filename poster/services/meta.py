"""Meta's Graph API: the Facebook Pages and Instagram accounts a token can post to, and publishing to them."""

from __future__ import annotations

import hashlib
import hmac
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from urllib.parse import urlparse

from poster.config import API, Config, check_url, is_placeholder
from poster.errors import ServiceError, truncate
from poster.http import HttpClient
from poster.retry import RetryCallback, browser_timeout, retry_request, worst_case_seconds
from poster.services import ConfiguredService

log = logging.getLogger("poster")

ContainerCallback = Callable[[str | None], None]  # remembers (or, with None, forgets) a post's Instagram container


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def same_text(a: Any, b: Any) -> bool:
    """Whether two captions match, ignoring how whitespace was changed on the way."""
    return isinstance(a, str) and isinstance(b, str) and a.split() == b.split()


def parse_time(value: Any) -> datetime | None:
    """A Graph API time (``2026-10-08T13:30:00+0000``)."""
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(re.sub(r"([+-]\d{2})(\d{2})$", r"\1:\2", value.replace("Z", "+00:00")))
    except ValueError:
        return None


def publish_retryable(error: ServiceError) -> bool:
    """Publishing is not idempotent: a timeout or 5xx may still have published the post. Only failures that
    guarantee nothing went out are retried."""
    return error.kind == "rate_limit" or error.status in (429, 503)


def may_have_published(error: ServiceError) -> bool:
    """Whether a failed publish may still have published the post: the request may have reached Meta."""
    return error.kind in ("timeout", "network") or (error.status >= 500 and error.status != 503)


class NotReadyError(ServiceError):
    """Instagram is still processing an uploaded image."""


@dataclass(frozen=True)
class Channel:
    """A Facebook Page or Instagram account the token can post to. ``token`` is the Page's own access token
    (an Instagram account uses its Page's), so it never leaves the server: see public()."""

    id: str
    service: str  # facebook | instagram
    name: str
    handle: str
    page_id: str
    page_name: str
    token: str
    problem: str | None

    def public(self) -> dict[str, Any]:
        return {"id": self.id, "service": self.service, "name": self.name, "handle": self.handle, "pageId": self.page_id,
                "pageName": self.page_name, "usable": self.problem is None, "problem": self.problem}


class MetaService(ConfiguredService):
    """Graph API with a system user (or long-lived user) access token."""

    service = "Meta"
    unconfigured = "Facebook and Instagram are not set up on the server"

    def __init__(self, http: HttpClient, config: Config) -> None:
        self.http = http
        self.token = "" if is_placeholder(config.meta_access_token) else config.meta_access_token
        self.app_secret = config.meta_app_secret
        self.version = config.meta_graph_version
        self.base_url = f"{config.meta_graph_url.rstrip('/')}/{config.meta_graph_version}"
        self.timeout = config.meta_timeout
        self.budget = config.request_budget
        self.problem = self._check_config(config)

    def _check_config(self, config: Config) -> str | None:
        if not self.token:
            return "META_ACCESS_TOKEN is not set."
        if re.search(r"\s", self.token):
            return "META_ACCESS_TOKEN contains whitespace."
        if not re.fullmatch(r"v\d+\.\d+", self.version):
            return "META_GRAPH_VERSION must look like v23.0."
        return check_url(config.meta_graph_url, "META_GRAPH_URL", https_only=True)

    @property
    def token_id(self) -> str:
        """Short, non-reversible fingerprint so the browser can tell when the token changed."""
        return hashlib.sha256(self.token.encode()).hexdigest()[:12] if self.token else ""

    def public_info(self) -> dict[str, Any]:
        return {
            "configured": self.configured, "problem": self.problem, "tokenId": self.token_id,
            "host": urlparse(self.base_url).netloc, "version": self.version, "timeout": self.timeout,
            "requestTimeout": browser_timeout(worst_case_seconds(self.timeout) + 10, self.budget),
        }

    # -- requests -------------------------------------------------------------------------------------------------

    def call(self, method: str, path: str, *, action: str, on_retry: RetryCallback, token: str | None = None,
             params: dict[str, Any] | None = None, should_retry: Callable[[ServiceError], bool] | None = None) -> dict[str, Any]:
        """One Graph API request, retried as ``should_retry`` allows. The token travels in the query (GET) or
        form body (POST), never in a logged URL: the HTTP client logs only the host."""
        self.require_configured()
        token = token or self.token
        fields = {**(params or {}), "access_token": token}
        if self.app_secret:
            fields["appsecret_proof"] = hmac.new(self.app_secret.encode(), token.encode(), hashlib.sha256).hexdigest()
        url = f"{self.base_url}/{path}"

        def execute() -> dict[str, Any]:
            sent = {"params": fields} if method == "GET" else {"data": fields}
            status, data, text = self.http.request(method, url, service=self.service, timeout=self.timeout, check=False,
                                                   **sent)
            if status >= 400 or isinstance(_dict(data).get("error"), dict):
                raise self.graph_error(status, data, text, action)
            if not isinstance(data, dict):
                raise ServiceError(f"Meta returned an invalid response while trying to {action}.", kind="parse",
                                   service=self.service, details=truncate(text, 300))
            return data

        return retry_request(execute, should_retry=should_retry, on_retry=on_retry)

    def graph_error(self, status: int, data: Any, text: str, action: str) -> ServiceError:
        """A classified error from the Graph API's error object (https://developers.facebook.com/docs/graph-api/guides/error-handling)."""
        error = _dict(_dict(data).get("error"))
        code, subcode = _int(error.get("code")), _int(error.get("error_subcode"))
        message = str(error.get("error_user_msg") or error.get("message") or "").strip()
        if not message:
            message = truncate(text.strip(), 200) if text and "<html" not in text.lower() else f"HTTP {status}"
        details = ", ".join(filter(None, [f"code {code}" if code else "", f"subcode {subcode}" if subcode else "",
                                          f"trace {error['fbtrace_id']}" if error.get("fbtrace_id") else ""]))
        common: dict[str, Any] = {"service": self.service, "details": details}
        if code == 190 or status == 401:
            return ServiceError(f"Meta rejected the access token ({message}).", kind="auth", status=401,
                                hint="Generate a new token for the system user in Meta Business settings and set "
                                     "META_ACCESS_TOKEN on the server.", **common)
        if code in (3, 10) or 200 <= code <= 299 or status == 403:
            return ServiceError(f"Meta denied access while trying to {action}: {message}", kind="auth", status=403,
                                hint="The token needs pages_show_list, pages_read_engagement, pages_manage_posts, "
                                     "instagram_basic and instagram_content_publish, and access to the Page.", **common)
        if code in API["meta"]["rate_limit_codes"] or 80001 <= code <= 80014 or status == 429:
            return ServiceError(f"Meta rate limit reached while trying to {action}: {message}", kind="rate_limit",
                                status=429, hint="Instagram publishes at most 100 posts per account per 24 hours.",
                                **common)
        if code == 9007 or subcode == 2207027:
            return NotReadyError("Instagram has not finished processing the image.", kind="server", status=503, **common)
        if code == 2 or error.get("is_transient") is True:
            # A temporary refusal: nothing was done, so it is safe to try again.
            return ServiceError(f"Meta had a temporary problem while trying to {action}: {message}", kind="server",
                                status=503, **common)
        if code == 1 or status >= 500:
            return ServiceError(f"Meta had a problem while trying to {action}: {message}", kind="server",
                                status=max(status, 500), **common)
        if code == 803 or (code == 100 and subcode == 33) or status == 404:
            return ServiceError(f"Meta could not find what was needed to {action}: {message}", kind="not_found",
                                status=404, **common)
        return ServiceError(f"Meta could not {action}: {message}", kind="client", status=status, **common)

    # -- account and channels -------------------------------------------------------------------------------------

    def validate_token(self, on_retry: RetryCallback) -> dict[str, str]:
        """Checks the token by reading who it belongs to (a system user, or a person)."""
        data = self.call("GET", "me", params={"fields": "id,name"}, action="verify the access token", on_retry=on_retry)
        if not data.get("id"):
            raise ServiceError("Meta returned unexpected account data.", kind="parse", service=self.service)
        return {"id": str(data["id"]), "name": str(data.get("name") or "")}

    def channels(self, on_retry: RetryCallback) -> list[Channel]:
        """Every Page the token manages, each followed by the Instagram professional account linked to it."""
        data = self.call("GET", "me/accounts", params={"fields": API["meta"]["account_fields"],
                                                       "limit": API["meta"]["accounts_limit"]},
                         action="load the Facebook Pages", on_retry=on_retry)
        pages = data.get("data")
        if not isinstance(pages, list):
            raise ServiceError("Meta returned an unexpected list of Pages.", kind="parse", service=self.service)
        channels: list[Channel] = []
        for page in pages:
            if not isinstance(page, dict) or not page.get("id"):
                continue
            page_id, page_name = str(page["id"]), str(page.get("name") or page["id"])
            token = str(page.get("access_token") or "")
            tasks = page.get("tasks")
            problem = None
            if not token or (isinstance(tasks, list) and "CREATE_CONTENT" not in tasks):
                problem = (f"The token cannot create content on {page_name}. In Meta Business settings, give the "
                           "system user full control of the Page.")
            channels.append(Channel(page_id, "facebook", page_name, "", page_id, page_name, token, problem))
            account = _dict(page.get("instagram_business_account"))
            if account.get("id"):
                username = str(account.get("username") or "")
                channels.append(Channel(str(account["id"]), "instagram", str(account.get("name") or username or account["id"]),
                                        username, page_id, page_name, token, problem))
        return channels

    def connect(self, on_retry: RetryCallback) -> dict[str, Any]:
        """Validates the token and lists the channels it can post to."""
        account = self.validate_token(on_retry)
        channels = self.channels(on_retry)
        if not channels:
            raise ServiceError("This token manages no Facebook Pages, so there are no channels to post to.", kind="client",
                               service=self.service, hint="In Meta Business settings, assign the Page (and its Instagram "
                                                          "account) to the system user, then generate a new token.")
        return {"account": account, "channels": [c.public() for c in channels]}

    # -- publishing -----------------------------------------------------------------------------------------------

    def permalink(self, channel: Channel, object_id: str, field: str, on_retry: RetryCallback) -> str | None:
        """The public link of a published post, when Meta gives one. A failure here costs only the link."""
        try:
            data = self.call("GET", object_id, token=channel.token, params={"fields": field}, action="read the post's link",
                             on_retry=on_retry)
        except ServiceError as error:
            log.warning("No link for %s post %s: %s", channel.service, object_id, error.message)
            return None
        link = data.get(field)
        return link if isinstance(link, str) and link.startswith("https://") else None

    def _publish_call(self, channel: Channel, path: str, params: dict[str, Any], action: str,
                      on_retry: RetryCallback) -> dict[str, Any]:
        """The request that makes a post public. A failure marked ``uncertain`` may still have published it."""
        try:
            return self.call("POST", path, token=channel.token, params=params, action=action, on_retry=on_retry,
                             should_retry=publish_retryable)
        except ServiceError as error:
            error.uncertain = may_have_published(error)
            raise

    def publish_facebook(self, channel: Channel, text: str, media_url: str | None, on_retry: RetryCallback) -> dict[str, Any]:
        """Publishes a photo post (or, without an image, a text post) on the Page now."""
        if media_url:
            data = self._publish_call(channel, f"{channel.page_id}/photos", {"url": media_url, "message": text},
                                      "publish the Facebook post", on_retry)
            post_id = data.get("post_id") or data.get("id")
        else:
            data = self._publish_call(channel, f"{channel.page_id}/feed", {"message": text}, "publish the Facebook post",
                                      on_retry)
            post_id = data.get("id")
        if not post_id:
            raise ServiceError("Meta did not return the new post's ID.", kind="parse", service=self.service, uncertain=True)
        return {"remoteId": str(post_id), "permalink": self.permalink(channel, str(post_id), "permalink_url", on_retry)}

    def publish_instagram(self, channel: Channel, text: str, media_url: str | None, container_id: str | None,
                          remember: ContainerCallback, on_retry: RetryCallback) -> dict[str, Any]:
        """Publishes an image post on the Instagram account now, in Instagram's two steps: upload the image
        into a media container, then publish the container. ``remember`` stores the container, so a later
        try skips the upload; a container already published (by a try whose answer was lost) is not
        published again."""
        if not media_url:
            raise ServiceError("Instagram posts need an image.", kind="validation", service=self.service)
        if not container_id:
            data = self.call("POST", f"{channel.id}/media", token=channel.token, params={"image_url": media_url, "caption": text},
                             action="upload the image to Instagram", on_retry=on_retry)
            if not data.get("id"):
                raise ServiceError("Meta did not return the Instagram upload's ID.", kind="parse", service=self.service)
            container_id = str(data["id"])
            remember(container_id)
        state = self.wait_for_container(channel, container_id, remember, on_retry)
        if state == "PUBLISHED":
            return self.find_published(channel, text, None, on_retry) or {"remoteId": None, "permalink": None}
        data = self._publish_call(channel, f"{channel.id}/media_publish", {"creation_id": container_id},
                                  "publish the Instagram post", on_retry)
        if not data.get("id"):
            raise ServiceError("Meta did not return the new post's ID.", kind="parse", service=self.service, uncertain=True)
        media_id = str(data["id"])
        return {"remoteId": media_id, "permalink": self.permalink(channel, media_id, "permalink", on_retry)}

    def container_state(self, channel: Channel, container_id: str, on_retry: RetryCallback) -> tuple[str, str]:
        """An Instagram container's status code (IN_PROGRESS, FINISHED, PUBLISHED, ERROR or EXPIRED) and its detail."""
        data = self.call("GET", container_id, token=channel.token, params={"fields": "status_code,status"},
                         action="check the Instagram upload", on_retry=on_retry)
        return str(data.get("status_code") or ""), str(data.get("status") or "")

    def wait_for_container(self, channel: Channel, container_id: str, remember: ContainerCallback,
                           on_retry: RetryCallback) -> str:
        """Waits (a few seconds at most) until the container can be published. Returns FINISHED or PUBLISHED."""

        def check() -> str:
            code, detail = self.container_state(channel, container_id, on_retry)
            if code in ("FINISHED", "PUBLISHED"):
                return code
            if code in ("ERROR", "EXPIRED"):
                remember(None)  # the next try uploads the image again
                if code == "EXPIRED":
                    raise ServiceError("The Instagram upload expired before it was published.", kind="server", status=503,
                                       service=self.service)
                raise ServiceError(f"Instagram could not use the image: {detail or 'unknown error'}. It takes JPEG "
                                   "images with an aspect ratio between 4:5 and 1.91:1.", kind="client", service=self.service)
            raise NotReadyError("Instagram has not finished processing the image.", kind="server", status=503,
                           service=self.service)

        return retry_request(check, should_retry=lambda error: isinstance(error, NotReadyError), on_retry=on_retry)

    def find_published(self, channel: Channel, text: str, since: datetime | None,
                       on_retry: RetryCallback) -> dict[str, Any] | None:
        """A recent post on the channel with this text (published at or after ``since``), or None."""
        limit = API["meta"]["recent_posts"]
        if channel.service == "facebook":
            params: dict[str, Any] = {"fields": "id,message,created_time,permalink_url", "limit": limit}
            if since is not None:
                params["since"] = int(since.timestamp())
            data = self.call("GET", f"{channel.page_id}/posts", token=channel.token, params=params,
                             action="look for the published post", on_retry=on_retry)
            text_field, time_field, link_field = "message", "created_time", "permalink_url"
        else:
            data = self.call("GET", f"{channel.id}/media", token=channel.token,
                             params={"fields": "id,caption,timestamp,permalink", "limit": limit},
                             action="look for the published post", on_retry=on_retry)
            text_field, time_field, link_field = "caption", "timestamp", "permalink"
        items = data.get("data")
        for item in items if isinstance(items, list) else []:
            if not isinstance(item, dict) or not item.get("id") or not same_text(item.get(text_field), text):
                continue
            published = parse_time(item.get(time_field))
            if since is not None and published is not None and published < since:
                continue
            link = item.get(link_field)
            return {"remoteId": str(item["id"]), "permalink": link if isinstance(link, str) and link.startswith("https://") else None,
                    "publishedAt": published}
        return None
