"""OAuth 1.0a request signing (RFC 5849, HMAC-SHA1), for X's user-context endpoints. Standard library only.

Query and form parameters are signed; JSON and multipart bodies are not, as the spec says.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import time
import urllib.parse
from typing import Any


def enc(value: Any) -> str:
    return urllib.parse.quote(str(value), safe="~-._")


def header(method: str, url: str, params: dict[str, Any], consumer_key: str, consumer_secret: str, token: str,
           token_secret: str, nonce: str | None = None, timestamp: str | None = None,
           version: str | None = "1.0") -> str:
    oauth = {"oauth_consumer_key": consumer_key, "oauth_nonce": nonce or secrets.token_hex(16),
             "oauth_signature_method": "HMAC-SHA1", "oauth_timestamp": timestamp or str(int(time.time())),
             "oauth_token": token}
    if version:
        oauth["oauth_version"] = version
    parts = urllib.parse.urlsplit(url)
    host = parts.hostname or ""
    if parts.port and (parts.scheme, parts.port) not in (("http", 80), ("https", 443)):
        host += ":%d" % parts.port
    base_url = "%s://%s%s" % (parts.scheme.lower(), host.lower(), parts.path or "/")
    pairs = urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
    pairs += [(k, str(v)) for k, v in params.items()] + list(oauth.items())
    normalized = "&".join("%s=%s" % kv for kv in sorted((enc(k), enc(v)) for k, v in pairs))
    base = "&".join((method.upper(), enc(base_url), enc(normalized)))
    key = "%s&%s" % (enc(consumer_secret), enc(token_secret))
    oauth["oauth_signature"] = base64.b64encode(hmac.new(key.encode(), base.encode(), hashlib.sha1).digest()).decode()
    return "OAuth " + ", ".join('%s="%s"' % (enc(k), enc(v)) for k, v in sorted(oauth.items()))
