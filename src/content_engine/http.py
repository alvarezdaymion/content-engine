"""A small HTTP client on the standard library.

`transport` is swappable, so tests and dry runs never touch the network. Two error types matter to the publisher:

- `HttpError`: the server answered with a 4xx or 5xx status.
- `TransportError`: no answer at all (timeout, refused or dropped connection). After a publish call this means the
  outcome is unknown, so the publisher holds that platform instead of retrying it.

Error messages never include request headers or query strings, so tokens can't leak into logs or the ledger.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

Transport = Callable[[str, str, dict[str, str], bytes | None, float], tuple[int, dict[str, str], bytes]]

USER_AGENT = "content-engine/0.1 (+https://github.com/alvarezdaymion/content-engine)"


class HttpError(RuntimeError):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


class TransportError(RuntimeError):
    """The request got no HTTP answer (timeout, connection refused or reset)."""


def urllib_transport(method: str, url: str, headers: dict[str, str], body: bytes | None,
                     timeout: float) -> tuple[int, dict[str, str], bytes]:
    req = urllib.request.Request(url, data=body, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, dict(resp.headers.items()), resp.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers.items()) if e.headers else {}, e.read()
    except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
        reason = getattr(e, "reason", e)
        raise TransportError("%s %s: no response (%s)" % (method, strip_query(url), reason)) from None


def strip_query(url: str) -> str:
    return url.split("?", 1)[0]


@dataclass
class Response:
    status: int
    headers: dict[str, str] = field(default_factory=dict)
    body: bytes = b""

    def json(self) -> Any:
        try:
            return json.loads(self.body or b"{}")
        except ValueError:
            return {}


def _error_text(raw: bytes) -> str:
    try:
        data = json.loads(raw or b"{}")
    except ValueError:
        return ""
    if not isinstance(data, dict):
        return ""
    err = data.get("error")
    if isinstance(err, dict):
        return str(err.get("message") or err.get("code") or "")
    if isinstance(err, str):
        return err
    detail = data.get("detail") or data.get("title") or data.get("message")
    return str(detail) if detail else ""


class Http:
    def __init__(self, transport: Transport | None = None, timeout: float = 60.0):
        self.transport = transport or urllib_transport
        self.timeout = timeout
        self.secrets: set[str] = set()

    def _remember(self, values: Any) -> None:
        if isinstance(values, dict):
            for key, value in values.items():
                name = str(key).lower().replace("-", "_")
                if (name in ("key", "authorization", "xi_api_key", "token")
                        or name.endswith(("_token", "_secret", "_key"))):
                    if isinstance(value, str) and value:
                        self.secrets.add(value)
                        if name == "authorization":
                            if value.lower().startswith(("bearer ", "oauth ")):
                                self.secrets.add(value.split(" ", 1)[1])
                            for part in re.findall(r'="([^"]+)"', value):
                                self.secrets.add(urllib.parse.unquote(part))
                self._remember(value)
        elif isinstance(values, list):
            for value in values:
                self._remember(value)

    def redact(self, text: str) -> str:
        for secret in sorted(self.secrets, key=len, reverse=True):
            for value in (secret, urllib.parse.quote(secret, safe=""), urllib.parse.quote_plus(secret)):
                text = text.replace(value, "[redacted]")
        return re.sub(r'https?://[^\s<>"\']+', lambda m: strip_query(m[0]), text)

    def request(self, method: str, url: str, *, params: dict[str, Any] | None = None, json_body: Any = None,
                form: dict[str, Any] | None = None, data: bytes | None = None,
                headers: dict[str, str] | None = None, timeout: float | None = None) -> Response:
        if params:
            url += ("&" if "?" in url else "?") + urllib.parse.urlencode(params)
        hdrs = {"User-Agent": USER_AGENT, **(headers or {})}
        self._remember(dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query)))
        for values in (hdrs, form, json_body):
            self._remember(values)
        body = data
        if json_body is not None:
            body = json.dumps(json_body).encode()
            hdrs.setdefault("Content-Type", "application/json")
        elif form is not None:
            body = urllib.parse.urlencode(form).encode()
            hdrs.setdefault("Content-Type", "application/x-www-form-urlencoded")
        try:
            status, resp_headers, raw = self.transport(method, url, hdrs, body, timeout or self.timeout)
        except TransportError as e:
            raise TransportError(self.redact(str(e))) from None
        response = Response(status, {k.lower(): v for k, v in resp_headers.items()}, raw)
        self._remember(response.json())
        if status >= 400:
            msg = self.redact(_error_text(raw))
            raise HttpError(status, "%s %s returned HTTP %d%s" % (method, strip_query(url), status,
                                                                  ": " + msg[:300] if msg else ""))
        return response

    def json(self, method: str, url: str, **kw: Any) -> Any:
        return self.request(method, url, **kw).json()
