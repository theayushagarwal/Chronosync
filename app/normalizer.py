"""URL normalization: many spellings of one page must map to ONE fetch.

This is the key to "200 users watch the same URL, fetched once".
"""
import asyncio
import ipaddress
import re
import socket
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from . import config

TRACKING_PREFIXES = ("utm_", "fbclid", "gclid", "mc_cid", "mc_eid", "ref_src", "igshid")


def normalize_url(raw: str) -> str:
    raw = (raw or "").strip()
    if not raw:
        raise ValueError("URL is empty")
    if "://" not in raw:
        raw = "https://" + raw
    parts = urlsplit(raw)
    if parts.scheme.lower() not in ("http", "https"):
        raise ValueError("Only http and https URLs are supported")
    if not parts.hostname:
        raise ValueError("URL has no host")
    scheme = parts.scheme.lower()
    host = parts.hostname.lower().rstrip(".")
    if ":" in host:  # IPv6 literal
        host = f"[{host}]"
    else:
        try:
            host = host.encode("idna").decode()
        except UnicodeError:
            raise ValueError("Invalid host name")
    port = parts.port  # raises ValueError for a bad port
    default = 443 if scheme == "https" else 80
    netloc = host if port in (None, default) else f"{host}:{port}"
    path = re.sub(r"/{2,}", "/", parts.path or "/")
    if path != "/":
        path = path.rstrip("/")
    query = sorted(
        (k, v)
        for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if not k.lower().startswith(TRACKING_PREFIXES)
    )
    return urlunsplit((scheme, netloc, path, urlencode(query), ""))


def domain_of(url: str) -> str:
    return urlsplit(url).hostname or ""


def host_is_public(host: str) -> bool:
    """True if every address the host resolves to is publicly routable."""
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return False
    if not infos:
        return False
    return all(ipaddress.ip_address(i[4][0]).is_global for i in infos)


async def assert_fetchable(url: str) -> None:
    """Block SSRF: refuse localhost/private targets unless explicitly allowed."""
    if config.ALLOW_PRIVATE_HOSTS:
        return
    host = domain_of(url)
    ok = await asyncio.to_thread(host_is_public, host)
    if not ok:
        raise ValueError("Host does not resolve to a public address")
