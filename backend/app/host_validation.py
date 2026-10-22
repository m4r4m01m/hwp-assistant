"""Host parsing shared by the API and stdlib-only host deployment preflight."""
import ipaddress
import os
import re

HOST_PATTERN = re.compile(r"(?P<name>\[[0-9a-fA-F:.]+\]|[A-Za-z0-9.-]+)(?::(?P<port>[0-9]{1,5}))?\Z")
DNS_LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\Z")


def host_name(authority):
    """Parse only HTTP Host syntax; never reconstruct a URL from untrusted Host."""
    match = HOST_PATTERN.fullmatch(authority)
    if not match or len(authority) > 260:
        raise ValueError("Invalid Host header")
    name, port = match.group("name", "port")
    if port is not None and not 1 <= int(port) <= 65535:
        raise ValueError("Invalid Host port")
    if name.startswith("["):
        name = f"[{ipaddress.IPv6Address(name[1:-1]).compressed}]"
    else:
        name = name.lower().removesuffix(".")
        if len(name) > 253 or not all(DNS_LABEL.fullmatch(label) for label in name.split(".")):
            raise ValueError("Invalid Host name")
    return name, int(port) if port is not None else None


def allowed_hosts(raw=None):
    """Unset is useful for local development; production requires an explicit list."""
    if raw is None:
        raw = os.getenv("HWP_ALLOWED_HOSTS", "")
    if not raw.strip():
        return ()
    result = []
    for value in raw.split(","):
        value = value.strip()
        wildcard = value.startswith("*.")
        try:
            name, port = host_name(value[2:] if wildcard else value)
        except ValueError as exc:
            raise ValueError("HWP_ALLOWED_HOSTS must contain host names, not URLs or '*'") from exc
        if port is not None or (wildcard and (name.startswith("[") or "." not in name)):
            raise ValueError("HWP_ALLOWED_HOSTS must omit ports and use only '*.example.com' wildcards")
        result.append("*." + name if wildcard else name)
    return tuple(result)
