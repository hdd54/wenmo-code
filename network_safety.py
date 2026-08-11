"""Fail-closed outbound URL validation for model-controlled tools."""

import ipaddress
import socket
import urllib.parse
import urllib.request


def validate_public_http_url(url, https_only=False):
    value = str(url or "").strip()
    try:
        parsed = urllib.parse.urlparse(value)
        port = parsed.port
    except ValueError as exc:
        raise ValueError("invalid URL") from exc
    schemes = {"https"} if https_only else {"http", "https"}
    if parsed.scheme.lower() not in schemes:
        raise ValueError("URL scheme is not allowed")
    if not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("URL must have a hostname and no embedded credentials")
    effective_port = port or (443 if parsed.scheme.lower() == "https" else 80)
    try:
        addresses = socket.getaddrinfo(
            parsed.hostname, effective_port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise ValueError("URL hostname cannot be resolved") from exc
    if not addresses:
        raise ValueError("URL hostname has no addresses")
    for address in addresses:
        ip = ipaddress.ip_address(address[4][0].split("%", 1)[0])
        if not ip.is_global:
            raise ValueError("private, loopback, link-local, and reserved hosts are blocked")
    return value


class _SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    def __init__(self, https_only=False):
        super().__init__()
        self.https_only = https_only

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        validate_public_http_url(newurl, https_only=self.https_only)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def safe_urlopen(request_or_url, timeout=20, https_only=False):
    url = (request_or_url.full_url if isinstance(request_or_url, urllib.request.Request)
           else str(request_or_url))
    validate_public_http_url(url, https_only=https_only)
    opener = urllib.request.build_opener(_SafeRedirectHandler(https_only=https_only))
    return opener.open(request_or_url, timeout=timeout)
