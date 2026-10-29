"""Explicit internal embedding transport; no provider discovery or paid fallback."""
import ipaddress
import json
import logging
import math
import os
import queue
import socket
import threading
import time
from contextvars import ContextVar
from dataclasses import dataclass, field
from urllib.parse import urlsplit, urlunsplit

import httpx

from .hwpx import digest

MAX_DIMENSIONS = 2048
MAX_BATCH = 64
MAX_RESPONSE = 4 * 1024 * 1024
_sending = ContextVar("hwp_search_embedding_transport", default=False)
_calls = threading.BoundedSemaphore(2)
_dns_slots = threading.BoundedSemaphore(2)
DNS_TIMEOUT = 2


class EmbeddingUnavailable(ValueError):
    """Safe error: never include an endpoint, query, document or response body."""


class _TransportFilter(logging.Filter):
    def filter(self, record):
        return not _sending.get()


_filter = _TransportFilter()
for _name in ("httpx", "httpcore.connection", "httpcore.http11", "httpcore.http2", "httpcore.proxy", "httpcore.socks"):
    logging.getLogger(_name).addFilter(_filter)


@dataclass(frozen=True)
class EmbeddingSettings:
    url: str = field(repr=False)
    model: str
    fingerprint: str


def embedding_settings():
    url = os.getenv("HWP_SEARCH_EMBEDDING_URL", "").strip()
    model = os.getenv("HWP_SEARCH_EMBEDDING_MODEL", "").strip()
    if not url or not model:
        return None
    try:
        parsed = urlsplit(url)
        allowed = parsed.scheme == "https" or (parsed.scheme == "http" and os.getenv("HWP_SEARCH_EMBEDDING_ALLOW_HTTP") == "1")
        if (not allowed or not parsed.hostname or parsed.username is not None or parsed.password is not None
                or parsed.query or parsed.fragment or not parsed.path.endswith("/embeddings")
                or any(ord(c) < 33 for c in url) or "%" in parsed.netloc or "\\" in url
                or len(url) > 2048 or len(model) > 200 or any(ord(c) < 32 for c in model)):
            return None
        if parsed.port == 0:
            return None
        return EmbeddingSettings(url, model, digest((url + "\0" + model).encode()))
    except ValueError:
        return None


def _internal_address(value):
    try:
        address = ipaddress.ip_address(value)
        return address.is_loopback or (address.is_private and not (
            address.is_link_local or address.is_multicast or address.is_unspecified or address.is_reserved))
    except ValueError:
        return False


def _pinned_endpoint(settings):
    parsed = urlsplit(settings.url)
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    # Resolve once and connect to that validated address. Re-resolving the host in
    # HTTPX would reopen the public-address check to DNS rebinding.
    if not _dns_slots.acquire(blocking=False):
        raise EmbeddingUnavailable("embedding_unavailable")
    reply = queue.Queue(maxsize=1)
    def resolve():
        try:
            reply.put(socket.getaddrinfo(parsed.hostname, port, type=socket.SOCK_STREAM))
        except Exception:
            reply.put(None)
        finally:
            _dns_slots.release()
    try:
        # OS name resolution has no portable per-call timeout. At most two
        # daemon resolvers can remain blocked; callers and app shutdown do not.
        threading.Thread(target=resolve, name="hwp-search-dns", daemon=True).start()
    except Exception:
        _dns_slots.release()
        raise EmbeddingUnavailable("embedding_unavailable") from None
    try:
        resolved = reply.get(timeout=DNS_TIMEOUT)
    except queue.Empty:
        raise EmbeddingUnavailable("embedding_unavailable") from None
    if not resolved:
        raise EmbeddingUnavailable("embedding_unavailable")
    addresses = list(dict.fromkeys(info[4][0] for info in resolved))
    if not addresses or not all(_internal_address(address) for address in addresses):
        raise EmbeddingUnavailable("internal_endpoint_required")
    address = addresses[0]
    host = f"[{address}]" if ":" in address else address
    return urlunsplit((parsed.scheme, f"{host}:{port}", parsed.path, "", "")), parsed.netloc, parsed.hostname


def normalize_vector(value):
    if not isinstance(value, list) or not 1 <= len(value) <= MAX_DIMENSIONS:
        raise EmbeddingUnavailable("invalid_vector")
    if any(isinstance(n, bool) or not isinstance(n, (int, float)) or not math.isfinite(n) or abs(n) > 1e10 for n in value):
        raise EmbeddingUnavailable("invalid_vector")
    norm = math.sqrt(math.fsum(n * n for n in value))
    if not math.isfinite(norm) or norm <= 0:
        raise EmbeddingUnavailable("invalid_vector")
    return [float(n / norm) for n in value]


def embed_texts(settings, texts):
    if (not texts or len(texts) > MAX_BATCH or any(not isinstance(text, str) or not text.strip() or len(text) > 1200 for text in texts)
            or sum(len(text) for text in texts) > 64000):
        raise EmbeddingUnavailable("input_limit")
    if not _calls.acquire(blocking=False):
        raise EmbeddingUnavailable("embedding_unavailable")
    token = _sending.set(True)
    try:
        endpoint, host, hostname = _pinned_endpoint(settings)
        started = time.monotonic()
        with httpx.Client(timeout=httpx.Timeout(5), follow_redirects=False, trust_env=False) as client:
            with client.stream("POST", endpoint, headers={"Host": host, "Accept-Encoding": "identity"}, extensions={"sni_hostname": hostname},
                               json={"model": settings.model, "input": texts, "encoding_format": "float"}) as response:
                if response.status_code != 200 or response.headers.get("content-encoding", "identity").lower() != "identity":
                    raise EmbeddingUnavailable("embedding_unavailable")
                raw = bytearray()
                for chunk in response.iter_bytes():
                    raw.extend(chunk)
                    if len(raw) > MAX_RESPONSE or time.monotonic() - started > 12:
                        raise EmbeddingUnavailable("response_limit")
        body = json.loads(raw)
        data = body.get("data") if isinstance(body, dict) else None
        if not isinstance(data, list) or len(data) != len(texts):
            raise EmbeddingUnavailable("invalid_response")
        ordered = {}
        for item in data:
            if not isinstance(item, dict) or type(item.get("index")) is not int or item["index"] in ordered:
                raise EmbeddingUnavailable("invalid_response")
            ordered[item["index"]] = normalize_vector(item.get("embedding"))
        if set(ordered) != set(range(len(texts))) or len({len(vector) for vector in ordered.values()}) != 1:
            raise EmbeddingUnavailable("invalid_response")
        return [ordered[index] for index in range(len(texts))]
    except EmbeddingUnavailable:
        raise
    except Exception:
        raise EmbeddingUnavailable("embedding_unavailable") from None
    finally:
        _sending.reset(token)
        _calls.release()
