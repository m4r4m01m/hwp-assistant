"""Mattermost REST behavior with synthetic IDs and in-memory HTTP transport."""
import asyncio
import json
import logging

import httpx
import pytest

from app import mattermost_client as mm


BOT = "b" * 26
USER = "u" * 26
TEAM = "t" * 26
CHANNEL = "c" * 26
POST = "p" * 26
BOT_TOKEN = "a" * 26
COMMAND_TOKEN = "z" * 26


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setenv("HWP_MATTERMOST_BOT_ENABLED", "1")
    monkeypatch.setenv("HWP_MATTERMOST_URL", "https://mattermost.example.invalid/company/mattermost")
    monkeypatch.setenv("HWP_MATTERMOST_PUBLIC_APP_URL", "https://app.example.invalid/hwp-assistant/")
    monkeypatch.setenv("HWP_MATTERMOST_BOT_TOKEN", BOT_TOKEN)
    monkeypatch.setenv("HWP_MATTERMOST_COMMAND_TOKEN", COMMAND_TOKEN)
    monkeypatch.setenv("HWP_MATTERMOST_TEAM_ID", TEAM)
    monkeypatch.delenv("HWP_MATTERMOST_BOT_ALLOW_HTTP", raising=False)
    return mm.settings()


def test_disabled_bot_ignores_unconfigured_and_invalid_secret_settings(monkeypatch):
    monkeypatch.delenv("HWP_MATTERMOST_BOT_ENABLED", raising=False)
    monkeypatch.setenv("HWP_MATTERMOST_URL", "https://secret:password@invalid/hooks/private")
    assert mm.settings() is None


def test_settings_preserve_both_subpaths_and_hide_tokens(configured):
    assert configured.api_url == "https://mattermost.example.invalid/company/mattermost/api/v4/"
    assert configured.public_app_url == "https://app.example.invalid/hwp-assistant/"
    assert BOT_TOKEN not in repr(configured) and COMMAND_TOKEN not in repr(configured)


@pytest.mark.parametrize("value", [
    "", "file:///tmp/path", "https://user:secret@mattermost.invalid/",
    "https://@mattermost.invalid/", "https://mattermost.invalid/?token=secret",
    "https://mattermost.invalid/#secret", "https://mattermost.invalid:99999/",
    "https://mattermost.invalid:0/", "https://mattermost.invalid/a/../b/",
    "https://mattermost.invalid/a/%2e%2e/b/", "https://mattermost.invalid/a//b/",
    "https://mattermost.invalid/a\\b/", "https://mattermost.invalid/\nsecret",
    "http://mattermost.internal/",
])
def test_config_rejects_ambiguous_or_insecure_url_without_echo(configured, monkeypatch, value):
    monkeypatch.setenv("HWP_MATTERMOST_URL", value)
    with pytest.raises(mm.MattermostConfigurationError) as error:
        mm.settings()
    assert "secret" not in str(error.value) and "mattermost.invalid" not in str(error.value)


@pytest.mark.parametrize("key", ["HWP_MATTERMOST_BOT_TOKEN", "HWP_MATTERMOST_COMMAND_TOKEN", "HWP_MATTERMOST_TEAM_ID"])
def test_invalid_credential_or_team_fails_closed(configured, monkeypatch, key):
    monkeypatch.setenv(key, "SYNTHETIC-invalid-token")
    with pytest.raises(mm.MattermostConfigurationError):
        mm.settings()


def test_explicit_http_opt_in_and_app_url_validation(configured, monkeypatch):
    monkeypatch.setenv("HWP_MATTERMOST_BOT_ALLOW_HTTP", "1")
    monkeypatch.setenv("HWP_MATTERMOST_URL", "http://mattermost:8065/mattermost")
    monkeypatch.setenv("HWP_MATTERMOST_PUBLIC_APP_URL", "http://127.0.0.1:5173")
    assert mm.settings().api_url == "http://mattermost:8065/mattermost/api/v4/"
    assert mm.settings().public_app_url == "http://127.0.0.1:5173/"
    monkeypatch.setenv("HWP_MATTERMOST_PUBLIC_APP_URL", "http://127.0.0.1:5173?private=secret")
    with pytest.raises(mm.MattermostConfigurationError):
        mm.settings()


def test_dm_requires_bot_and_posts_only_in_recipient_direct_channel(configured):
    seen = []
    def handler(request):
        seen.append((request.method, request.url.path, json.loads(request.content) if request.content else None))
        assert request.headers["authorization"] == "Bearer " + BOT_TOKEN
        if request.url.path.endswith("/users/me"):
            return httpx.Response(200, json={"id": BOT, "is_bot": True, "delete_at": 0})
        if request.url.path.endswith("/channels/direct"):
            assert json.loads(request.content) == [BOT, USER]
            return httpx.Response(201, json={"id": CHANNEL, "type": "D"})
        assert json.loads(request.content) == {"channel_id": CHANNEL, "message": "완료 [초안 검토](https://app.example.invalid/hwp-assistant/?report=synthetic)"}
        return httpx.Response(201, json={"id": POST, "channel_id": CHANNEL, "user_id": BOT})
    client = mm.BotClient(configured, transport=httpx.MockTransport(handler))
    assert asyncio.run(client.send_dm(USER, "완료 [초안 검토](https://app.example.invalid/hwp-assistant/?report=synthetic)")) == POST
    assert len(seen) == 3
    assert all(path.startswith("/company/mattermost/api/v4/") for _, path, _ in seen)


@pytest.mark.parametrize("self_response", [
    {"id": BOT, "is_bot": False}, {"id": BOT}, {"id": BOT, "is_bot": True, "delete_at": 100},
    {"id": "invalid", "is_bot": True},
])
def test_human_or_disabled_or_invalid_bot_is_never_allowed_to_post(configured, self_response):
    requests = []
    def handler(request):
        requests.append(request.url.path)
        return httpx.Response(200, json=self_response)
    with pytest.raises(mm.MattermostError):
        asyncio.run(mm.BotClient(configured, transport=httpx.MockTransport(handler)).send_dm(USER, "합성 메시지"))
    assert requests == ["/company/mattermost/api/v4/users/me"]


@pytest.mark.parametrize("channel", [{"id": CHANNEL, "type": "O"}, {"id": CHANNEL, "type": "P"}, {"id": "invalid", "type": "D"}])
def test_never_posts_when_direct_channel_response_is_wrong(configured, channel):
    paths = []
    def handler(request):
        paths.append(request.url.path)
        return httpx.Response(200, json={"id": BOT, "is_bot": True} if request.url.path.endswith("users/me") else channel)
    with pytest.raises(mm.MattermostError):
        asyncio.run(mm.BotClient(configured, transport=httpx.MockTransport(handler)).send_dm(USER, "합성 메시지"))
    assert not any(path.endswith("/posts") for path in paths)


@pytest.mark.parametrize("prior", [{"id": POST, "channel_id": "d" * 26, "user_id": BOT}, {"id": POST, "channel_id": CHANNEL, "user_id": USER}])
def test_edit_cannot_modify_another_channel_or_another_authors_post(configured, prior):
    methods = []
    def handler(request):
        methods.append(request.method)
        if request.url.path.endswith("users/me"):
            return httpx.Response(200, json={"id": BOT, "is_bot": True})
        if request.url.path.endswith("channels/direct"):
            return httpx.Response(200, json={"id": CHANNEL, "type": "D"})
        return httpx.Response(200, json=prior)
    with pytest.raises(mm.MattermostError):
        asyncio.run(mm.BotClient(configured, transport=httpx.MockTransport(handler)).send_dm(USER, "수정", POST))
    assert methods == ["GET", "POST", "GET"]


def test_edit_updates_only_message_on_verified_bot_dm_post(configured):
    requests = []
    def handler(request):
        requests.append(request)
        if request.url.path.endswith("users/me"):
            return httpx.Response(200, json={"id": BOT, "is_bot": True})
        if request.url.path.endswith("channels/direct"):
            return httpx.Response(200, json={"id": CHANNEL, "type": "D"})
        return httpx.Response(200, json={"id": POST, "channel_id": CHANNEL, "user_id": BOT})
    assert asyncio.run(mm.BotClient(configured, transport=httpx.MockTransport(handler)).send_dm(USER, "완료", POST)) == POST
    assert requests[-1].method == "PUT" and requests[-1].url.path.endswith("/posts/" + POST + "/patch")
    assert json.loads(requests[-1].content) == {"message": "완료"}


@pytest.mark.parametrize("post_id", [None, POST])
def test_final_message_refresh_runs_after_remote_preflight(configured, post_id):
    events = []
    def handler(request):
        endpoint = request.url.path.rsplit("/", 1)[-1]
        events.append((request.method, endpoint))
        if endpoint == "me":
            return httpx.Response(200, json={"id": BOT, "is_bot": True})
        if endpoint == "direct":
            return httpx.Response(200, json={"id": CHANNEL, "type": "D"})
        if request.method != "GET":
            assert events[-2] == ("check", "live authorization")
            assert json.loads(request.content)["message"] == "최신 권한으로 확인한 내용"
        return httpx.Response(200, json={"id": POST, "channel_id": CHANNEL, "user_id": BOT})
    async def before_send():
        expected = [("GET", "me"), ("POST", "direct")]
        if post_id:
            expected.append(("GET", POST))
        assert events == expected
        events.append(("check", "live authorization"))
        return "최신 권한으로 확인한 내용"
    client = mm.BotClient(configured, transport=httpx.MockTransport(handler))
    assert asyncio.run(client.send_dm(USER, "", post_id, before_send=before_send)) == POST


@pytest.mark.parametrize("post_id", [None, POST])
def test_preflight_permission_change_can_suppress_post_mutation(configured, post_id):
    allowed = True
    requests = []
    def handler(request):
        nonlocal allowed
        requests.append(request)
        if request.url.path.endswith("users/me"):
            return httpx.Response(200, json={"id": BOT, "is_bot": True})
        if request.url.path.endswith("channels/direct"):
            allowed = False
            return httpx.Response(200, json={"id": CHANNEL, "type": "D"})
        assert request.method == "GET"
        return httpx.Response(200, json={"id": POST, "channel_id": CHANNEL, "user_id": BOT})
    async def before_send():
        return "민감한 문서 내용" if allowed else None
    client = mm.BotClient(configured, transport=httpx.MockTransport(handler))
    assert asyncio.run(client.send_dm(USER, "민감한 이전 내용", post_id, before_send=before_send)) is None
    assert len(requests) == (3 if post_id else 2)


@pytest.mark.parametrize("message", ["", "가" * 6000, "\ud800", {"message": "wrong type"}])
def test_final_message_is_validated_before_post(configured, message):
    requests = []
    def handler(request):
        requests.append(request)
        if request.url.path.endswith("users/me"):
            return httpx.Response(200, json={"id": BOT, "is_bot": True})
        assert request.url.path.endswith("channels/direct")
        return httpx.Response(200, json={"id": CHANNEL, "type": "D"})
    async def before_send():
        return message
    client = mm.BotClient(configured, transport=httpx.MockTransport(handler))
    with pytest.raises(mm.MattermostError):
        asyncio.run(client.send_dm(USER, "old", before_send=before_send))
    assert len(requests) == 2


def test_redirect_error_and_transport_logs_do_not_leak_secrets(configured, caplog):
    caplog.set_level(logging.DEBUG)
    seen = []
    def handler(request):
        seen.append(request)
        logging.getLogger("httpcore.http11").debug("%s %s", request.url, BOT_TOKEN)
        return httpx.Response(302, headers={"location": "https://attacker.invalid/secret"}, json={"message": "private-response"})
    with pytest.raises(mm.MattermostError) as error:
        asyncio.run(mm.BotClient(configured, transport=httpx.MockTransport(handler)).send_dm(USER, "private-request"))
    assert len(seen) == 1
    assert "private" not in str(error.value) and "attacker" not in str(error.value)
    assert BOT_TOKEN not in caplog.text and "mattermost.example.invalid" not in caplog.text


@pytest.mark.parametrize("response", [
    httpx.Response(200, content=b"not json: private-response"),
    httpx.Response(200, json=[{"id": BOT, "is_bot": True}]),
    httpx.Response(200, content=b" " * (mm.MAX_RESPONSE_BYTES + 1)),
    httpx.Response(403, json={"message": "private-response"}),
])
def test_bad_or_oversized_response_is_bounded_and_sanitized(configured, response):
    with pytest.raises(mm.MattermostError) as error:
        asyncio.run(mm.BotClient(configured, transport=httpx.MockTransport(lambda _: response)).get_self())
    assert "private-response" not in str(error.value)


def test_connection_error_is_sanitized(configured):
    def handler(request):
        raise httpx.ConnectError("secret-url-private-response", request=request)
    with pytest.raises(mm.MattermostError) as error:
        asyncio.run(mm.BotClient(configured, transport=httpx.MockTransport(handler)).get_self())
    assert "secret" not in str(error.value) and error.value.__cause__ is None


def test_dialog_callback_is_pinned_and_preserves_state(configured):
    seen = []
    def handler(request):
        seen.append(request)
        return httpx.Response(200, json={"id": BOT, "is_bot": True} if request.url.path.endswith("users/me") else {"status": "OK"})
    elements = [{"display_name": "작성할 내용", "name": "prompt", "type": "textarea", "max_length": 6000}]
    callback = configured.public_app_url + "api/integrations/mattermost/dialog"
    client = mm.BotClient(configured, transport=httpx.MockTransport(handler))
    asyncio.run(client.open_dialog("synthetic-trigger", callback, "s" * 43, elements))
    body = json.loads(seen[-1].content)
    assert body["url"] == callback and body["dialog"]["state"] == "s" * 43
    assert body["dialog"]["elements"] == elements and body["dialog"]["callback_id"] == "hwp-report"
    assert seen[-1].url.path == "/company/mattermost/api/v4/actions/dialogs/open"
    with pytest.raises(mm.MattermostError):
        asyncio.run(client.open_dialog("synthetic-trigger", "https://attacker.invalid/steal", "s" * 43, elements))
    assert len(seen) == 2


def test_invalid_outbound_payload_is_rejected_before_transport(configured):
    def unexpected(_):
        pytest.fail("Invalid payload must not reach transport")
    client = mm.BotClient(configured, transport=httpx.MockTransport(unexpected))
    for user_id, message, post_id in [("../users", "text", None), (USER, "", None), (USER, "가" * 6000, None), (USER, "text", "invalid")]:
        with pytest.raises(mm.MattermostError):
            asyncio.run(client.send_dm(user_id, message, post_id))


def test_untrusted_excerpts_cannot_inject_mentions_links_or_newlines():
    text = mm.safe_text("@channel\n[click](https://evil.invalid) <b>x</b> **bold** | `code`\x00 &#64;all www.evil.invalid", max_length=300)
    assert "@" not in text and "\n" not in text and "\x00" not in text
    assert "[" not in text and "]" not in text and "<" not in text and "`" not in text
    assert "https:" not in text and "|" not in text
    assert "&" not in text and "www." not in text
    assert "가" * 5 == mm.safe_text("가" * 10, max_length=5)
