import ipaddress
import re
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

TRACKING = {"fbclid", "gclid", "dclid", "msclkid", "mc_cid", "mc_eid"}
SKIP_EXTENSIONS = re.compile(r"\.(?:jpg|jpeg|png|gif|webp|svg|mp4|mp3|woff2?|ttf|zip|exe)$", re.I)


def canonicalize(url: str, base: str = "") -> str | None:
    try:
        if any(ord(c) < 32 for c in url) or "\\" in url:
            return None
        parts = urlsplit(urljoin(base, url.strip()))
        if parts.scheme.lower() not in {"http", "https"} or not parts.hostname:
            return None
        if parts.username is not None or parts.password is not None:
            return None
        host = parts.hostname.encode("idna").decode().lower().rstrip(".")
        if ":" in host:
            host = f"[{host}]"
        port = parts.port
        scheme = parts.scheme.lower()
        netloc = host if port is None or (scheme, port) in {("http", 80), ("https", 443)} \
            else f"{host}:{port}"
        query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
                 if not k.lower().startswith("utm_") and k.lower() not in TRACKING]
        # Preserve query order and duplicate keys because servers can give them meaning.
        cleaned_query = parts.query if len(query) == len(parse_qsl(parts.query, keep_blank_values=True)) \
            else urlencode(query)
        return urlunsplit((scheme, netloc, parts.path or "/", cleaned_query, ""))
    except (ValueError, UnicodeError):
        return None


def origin(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}"


def route_key(url: str) -> str:
    parts = urlsplit(url)
    segments = [re.sub(r"\d+", "{n}", s) for s in parts.path.split("/") if s]
    prefix = "/".join(segments[:2])
    return f"{origin(url)}/{prefix}"


def is_public_ip(address: str) -> bool:
    ip = ipaddress.ip_address(address.split("%")[0])
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return ip.is_global

