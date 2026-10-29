"""Bounded, opt-in Mattermost REST transport. No credentials enter receipts/logs.

Creating a post is not an exactly-once operation: an uncertain response may have
committed remotely. Callers must retain delivery state and avoid blind retries.
"""
import asyncio
import json
import logging
import os
import re
from contextvars import ContextVar
from dataclasses import dataclass, field
from urllib.parse import urlsplit, urlunsplit

import httpx


ID = re.compile(r"[a-z0-9]{26}\Z")
MAX_RESPONSE_BYTES = 256 * 1024
MAX_MESSAGE_BYTES = 16000
_transport_active = ContextVar("mattermost_transport_active", default=False)


class MattermostConfigurationError(ValueError):
    def __init__(self):
        super().__init__("매터모스트 봇 연결 설정을 확인하세요.")


class MattermostError(RuntimeError):
    def __init__(self):
        super().__init__("매터모스트 연결을 완료하지 못했습니다.")


class _TransportLogFilter(logging.Filter):
    def filter(self, record):
        # httpx/httpcore may include URL, request headers and network exceptions.
        return not _transport_active.get()


_log_filter = _TransportLogFilter()
for _name in ("httpx", "httpcore", "httpcore.connection", "httpcore.http11",
              "httpcore.http2", "httpcore.proxy", "httpcore.socks"):
    logging.getLogger(_name).addFilter(_log_filter)


def valid_id(value):
    return isinstance(value, str) and ID.fullmatch(value) is not None


def _valid_message(value):
    try:
        return isinstance(value, str) and bool(value.strip()) and len(value.encode("utf-8")) <= MAX_MESSAGE_BYTES
    except UnicodeError:
        return False


def _base_url(value, *, allow_http=False):
    """Validate a configured SiteURL; never construct a host from a callback."""
    try:
        if not isinstance(value, str) or not value or len(value) > 2048:
            raise ValueError
        if any(ord(char) <= 32 or ord(char) == 127 for char in value) or "\\" in value:
            raise ValueError
        url = urlsplit(value)
        if (url.scheme not in ({"https", "http"} if allow_http else {"https"})
                or not url.hostname or url.username is not None or url.password is not None
                or url.query or url.fragment or not url.netloc or url.port == 0):
            raise ValueError
        # Reject ambiguous/encoded path traversal and URL parser discrepancies.
        if "%" in url.path or "//" in url.path or any(part in (".", "..") for part in url.path.split("/")):
            raise ValueError
        return urlunsplit((url.scheme, url.netloc, url.path.rstrip("/") + "/", "", ""))
    except ValueError:
        raise MattermostConfigurationError() from None


@dataclass(frozen=True)
class BotSettings:
    base_url: str = field(repr=False)
    public_app_url: str = field(repr=False)
    bot_token: str = field(repr=False)
    command_token: str = field(repr=False)
    team_id: str

    @property
    def api_url(self):
        return self.base_url + "api/v4/"


def settings():
    """An unconfigured/disabled bot never affects ordinary app startup."""
    if os.getenv("HWP_MATTERMOST_BOT_ENABLED", "0") != "1":
        return None
    allow_http = os.getenv("HWP_MATTERMOST_BOT_ALLOW_HTTP", "0") == "1"
    base = _base_url(os.getenv("HWP_MATTERMOST_URL", ""), allow_http=allow_http)
    app = _base_url(os.getenv("HWP_MATTERMOST_PUBLIC_APP_URL", ""), allow_http=allow_http)
    token = os.getenv("HWP_MATTERMOST_BOT_TOKEN", "")
    command = os.getenv("HWP_MATTERMOST_COMMAND_TOKEN", "")
    team = os.getenv("HWP_MATTERMOST_TEAM_ID", "")
    if not all(valid_id(value) for value in (token, command, team)):
        raise MattermostConfigurationError()
    return BotSettings(base, app, token, command, team)


def safe_text(value, max_length=700):
    """Render untrusted names/excerpts as one inert Markdown line.

    Call before composing trusted app links; never run over the final message.
    Fullwidth punctuation avoids Markdown links, HTML, raw URL autolinking and
    channel/user mentions without trusting a client's Markdown escape behavior.
    """
    value = str(value)
    value = " ".join("".join(char if ord(char) >= 32 and ord(char) != 127 else " "
                              for char in value).split())[:max_length]
    table = str.maketrans({"@": "＠", "[": "［", "]": "］", "(": "（", ")": "）",
                          "<": "＜", ">": "＞", "`": "｀", "*": "＊", "_": "＿",
                          "~": "～", "#": "＃", "|": "｜", "\\": "＼", ":": "：", "&": "＆"})
    return re.sub(r"(?i)\bwww\.", "www．", value.translate(table))


class BotClient:
    def __init__(self, config, *, transport=None):
        self.config = config
        self.transport = transport
        self._bot_id = None

    async def _request(self, method, endpoint, payload=None):
        active = _transport_active.set(True)
        try:
            async with asyncio.timeout(5):
                async with httpx.AsyncClient(
                    timeout=httpx.Timeout(4, connect=2), follow_redirects=False, trust_env=False,
                    transport=self.transport,
                    headers={"Authorization": "Bearer " + self.config.bot_token,
                             "Accept": "application/json", "Accept-Encoding": "identity"},
                ) as client:
                    async with client.stream(method, self.config.api_url + endpoint, json=payload) as response:
                        if not 200 <= response.status_code < 300:
                            raise MattermostError()
                        if response.headers.get("content-encoding", "identity").lower() not in ("", "identity"):
                            raise MattermostError()
                        content = bytearray()
                        async for part in response.aiter_bytes():
                            if len(content) + len(part) > MAX_RESPONSE_BYTES:
                                raise MattermostError()
                            content.extend(part)
                        result = json.loads(content) if content else {}
                        if not isinstance(result, dict):
                            raise MattermostError()
                        return result
        except (httpx.HTTPError, TimeoutError, ValueError, TypeError, RecursionError):
            # No remote body, URL, auth header, trigger/state, or exception text.
            raise MattermostError() from None
        finally:
            _transport_active.reset(active)

    async def get_self(self):
        if self._bot_id is None:
            user = await self._request("GET", "users/me")
            if not valid_id(user.get("id")) or user.get("is_bot") is not True or user.get("delete_at", 0) != 0:
                raise MattermostError()
            self._bot_id = user["id"]
        return self._bot_id

    async def send_dm(self, user_id, text, post_id=None, *, before_send=None):
        """Create a private post, or safely update a known bot post in that DM.

        post_id is a server-issued existing ID, not an idempotency key. The API's
        create-post contract does not promise pending_post_id deduplication.
        before_send refreshes authorization/content after the remote preflight;
        its None result suppresses delivery, otherwise its message replaces text.
        """
        if (not valid_id(user_id) or before_send is None and not _valid_message(text)
                or before_send is not None and not callable(before_send)
                or post_id is not None and not valid_id(post_id)):
            raise MattermostError()
        bot_id = await self.get_self()
        if user_id == bot_id:
            raise MattermostError()
        channel = await self._request("POST", "channels/direct", [bot_id, user_id])
        channel_id = channel.get("id")
        if not valid_id(channel_id) or channel.get("type") != "D":
            raise MattermostError()
        if post_id:
            previous = await self._request("GET", "posts/" + post_id)
            if previous.get("user_id") != bot_id or previous.get("channel_id") != channel_id:
                raise MattermostError()
        if before_send is not None:
            text = await before_send()
            if text is None:
                return None
            if not _valid_message(text):
                raise MattermostError()
        if post_id:
            post = await self._request("PUT", "posts/" + post_id + "/patch", {"message": text})
        else:
            post = await self._request("POST", "posts", {"channel_id": channel_id, "message": text})
        if (not valid_id(post.get("id")) or post.get("channel_id") != channel_id
                or post.get("user_id") != bot_id or post_id and post["id"] != post_id):
            raise MattermostError()
        return post["id"]

    async def open_dialog(self, trigger_id, callback_url, state, elements):
        # Callback is an app-owned route, never a URL supplied by a command.
        expected = self.config.public_app_url + "api/integrations/mattermost/dialog"
        if (callback_url != expected or not isinstance(trigger_id, str) or not 1 <= len(trigger_id) <= 2048
                or any(ord(char) < 33 or ord(char) == 127 for char in trigger_id)
                or not isinstance(state, str) or not re.fullmatch(r"[A-Za-z0-9_-]{32,128}", state)
                or not isinstance(elements, list) or not 1 <= len(elements) <= 5
                or len(json.dumps(elements, ensure_ascii=False).encode("utf-8")) > 12000):
            raise MattermostError()
        try:
            # Trigger IDs are short lived; do not consume the slash-command
            # response window waiting through multiple long connection retries.
            async with asyncio.timeout(2.5):
                await self.get_self()
                await self._request("POST", "actions/dialogs/open", {
                    "trigger_id": trigger_id,
                    "url": callback_url,
                    "dialog": {"callback_id": "hwp-report", "title": "문서 초안 작성",
                               "submit_label": "초안 작성", "state": state,
                               "elements": elements},
                })
        except TimeoutError:
            raise MattermostError() from None
