"""SSRF-safe outbound URL validation and fetching -- Batch 5 of
`docs/architecture/implementation-roadmap.md`.

**On the duplication, because it is deliberate and needs stating.** Two
older copies of the resolve-and-blocklist check already exist:
`media_gen/download.py` (which also has the redirect-revalidation loop
this module's `safe_get_json` is modelled on) and
`bi_integration/outbound_safety.py`, whose own module docstring explains
why it did not extract a shared helper -- `tests/test_media_gen.py`
patches `media_gen.download.log_security_event` directly, and moving that
call out of that namespace would silently break an audited, passing suite.

That reasoning still holds, so **this module does not touch either of
them.** It is the canonical home for *new* callers instead. What stops
three copies from drifting is a test, not a convention:
`tests/test_outbound_url.py::TestBlocklistsAreIdentical` asserts all three
blocklists match exactly, so a range added to one and forgotten in the
others fails CI. Consolidating the two older copies is its own task, and
has to deal with those patch targets.

What this module adds beyond the older two: a **host allowlist** checked
before DNS, and a bounded JSON GET. The allowlist matters because a
blocklist alone is a deny-known-bad control -- fine for a provider CDN URL
the app itself constructed, not sufficient for an operator-configured REST
endpoint, where the safe set is small and enumerable and should be stated
positively.
"""

from __future__ import annotations

import ipaddress
import json
import socket
from typing import Any
from urllib.parse import urljoin, urlparse

import requests

from security.audit_log import log_security_event
from security.redaction import redact_secrets

#: Private/loopback/link-local/reserved ranges, IPv4 and IPv6. **Kept
#: byte-identical to `media_gen/download.py::_BLOCKED_IP_NETWORKS` and
#: `bi_integration/outbound_safety.py::_BLOCKED_IP_NETWORKS`** -- pinned by
#: `tests/test_outbound_url.py::TestBlocklistsAreIdentical`. Add a range in
#: all three or the test fails, which is the point.
_BLOCKED_IP_NETWORKS = tuple(
    ipaddress.ip_network(net)
    for net in (
        "0.0.0.0/8",
        "10.0.0.0/8",
        "100.64.0.0/10",
        "127.0.0.0/8",
        "169.254.0.0/16",
        "172.16.0.0/12",
        "192.0.0.0/24",
        "192.0.2.0/24",
        "192.168.0.0/16",
        "198.18.0.0/15",
        "198.51.100.0/24",
        "203.0.113.0/24",
        "224.0.0.0/4",
        "240.0.0.0/4",
        "::1/128",
        "::/128",
        "64:ff9b::/96",
        "100::/64",
        "fc00::/7",
        "fe80::/10",
    )
)

#: Redirect statuses followed manually. Same set `media_gen/download.py`
#: follows.
_REDIRECT_STATUS_CODES = frozenset({301, 302, 303, 307, 308})


class OutboundUrlBlockedError(Exception):
    """Raised when a URL is not safe for this server to call.

    Carries a `safe_message` for the same reason
    `agent.exceptions.AgentError` does: a response body must never echo
    internal detail (`CLAUDE.md` 5.6). The full reason goes to the log and
    the audit event; the caller gets a fixed string.
    """

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.safe_message = "That destination is not permitted."


class OutboundFetchError(Exception):
    """A transport or HTTP failure, as distinct from a blocked URL.

    Kept separate so a caller can tell "we refused to call this" from "we
    called it and it failed" -- the first is a configuration or policy
    problem, the second an operational one.
    """

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.safe_message = "The external service could not be reached."


def assert_safe_url(url: str, *, allowed_hosts: tuple[str, ...], context: str) -> None:
    """Raises `OutboundUrlBlockedError` unless `url` is safe to call.

    Four checks, in this order, because each is cheaper and more
    conclusive than the next:

    1. **HTTPS only.** No `http`, and in particular no `file`, `gopher`,
       `ftp` or `data` -- a scheme check is the one control that cannot be
       defeated by DNS.
    2. **A hostname is present.**
    3. **The hostname is on `allowed_hosts`.** Exact, case-insensitive
       match -- not a suffix match, which `evil-example.com` would pass
       against an `example.com` entry. Checked *before* DNS so a
       non-allowlisted host is refused without this server making any
       network request on an attacker's behalf, not even a lookup.
    4. **Every resolved address is outside the blocked ranges.** Every
       address, not just the first: a host with both a public and a
       private A record must be refused, and taking `addrinfo[0]` would
       let record ordering decide.

    This is a resolve-then-connect check, not a pinned-IP call -- the OS
    could in principle re-resolve a different address between this check
    and the request. That is the same disclosed scope
    `media_gen/download.py::_validate_download_url` and
    `bi_integration/outbound_safety.py::assert_safe_outbound_url` already
    accept. The allowlist is what makes it a much smaller window here: a
    rebind has to be served by a host an operator explicitly named.

    Args:
        url: The URL about to be called. Re-validated on **every** use and
            on every redirect hop -- never once at configuration time
            (`CLAUDE.md` 5.6).
        allowed_hosts: Hostnames an operator has permitted
            (`Settings.rest_allowed_hosts`). An empty tuple blocks
            everything, which is the correct failing-closed default: a
            connector with no allowlist is not "unrestricted", it is
            unconfigured.
        context: A short, stable label for which feature this guards,
            included on every `ssrf_blocked` audit event so a pattern of
            rejections is attributable.
    """
    parsed = urlparse(url)

    if parsed.scheme != "https":
        log_security_event(
            "ssrf_blocked",
            "warning",
            "Refused an outbound call over a non-HTTPS scheme.",
            reason="non_https_scheme",
            scheme=parsed.scheme,
            context=context,
        )
        raise OutboundUrlBlockedError(f"Refusing a non-HTTPS URL: {parsed.scheme!r}")

    hostname = parsed.hostname
    if not hostname:
        log_security_event(
            "ssrf_blocked",
            "warning",
            "Refused an outbound call: URL has no hostname.",
            reason="no_hostname",
            context=context,
        )
        raise OutboundUrlBlockedError("Refusing a URL with no hostname")

    # Exact match only. A suffix check would accept `evil-example.com` for
    # an `example.com` entry, and `example.com.evil.net` for either.
    if hostname.lower() not in {host.lower() for host in allowed_hosts}:
        log_security_event(
            "ssrf_blocked",
            "warning",
            "Refused an outbound call to a host that is not on the configured allowlist.",
            reason="host_not_allowlisted",
            host=hostname,
            context=context,
        )
        raise OutboundUrlBlockedError(f"Host {hostname!r} is not on the outbound allowlist")

    try:
        addrinfo = socket.getaddrinfo(hostname, None)
    except socket.gaierror as exc:
        raise OutboundUrlBlockedError(f"Could not resolve host {hostname!r}: {exc}") from exc

    for *_unused, sockaddr in addrinfo:
        ip = ipaddress.ip_address(sockaddr[0])
        if any(ip in network for network in _BLOCKED_IP_NETWORKS):
            log_security_event(
                "ssrf_blocked",
                "warning",
                "Refused an outbound call to a private/internal address -- possible SSRF.",
                reason="private_or_reserved_address",
                host=hostname,
                resolved_ip=str(ip),
                context=context,
            )
            raise OutboundUrlBlockedError(
                f"Host {hostname!r} resolves to a private/internal address"
            )


def safe_get_json(
    url: str,
    *,
    allowed_hosts: tuple[str, ...],
    context: str,
    timeout: float,
    max_bytes: int,
    max_redirects: int,
    headers: dict[str, str] | None = None,
) -> Any:
    """GETs `url` and returns its parsed JSON body.

    Read-only by construction: this function issues `GET` and nothing
    else. A side-effecting request is a separate capability and does not
    belong behind a helper whose name says "get"
    (`docs/architecture/implementation-roadmap.md`'s connector rules).

    Redirects are followed manually (`allow_redirects=False`), each hop
    re-validated by `assert_safe_url` before it is followed. Without that,
    validating the original URL would be moot the moment its server
    returned a redirect -- `requests` follows redirects transparently by
    default, so an allowlisted host could bounce this call straight to
    `169.254.169.254` with no check at all. This is the same loop
    `media_gen/download.py::download_media_bytes` already runs, for the
    same reason.

    The body is streamed and aborted once `max_bytes` is exceeded, checked
    both against a declared `Content-Length` (before any body is read) and
    against bytes actually received (since a response can omit or lie
    about that header).

    Raises:
        OutboundUrlBlockedError: the URL, or any redirect hop, is not safe.
        OutboundFetchError: transport failure, HTTP >= 400, the response
            exceeded `max_bytes`, or the body was not valid JSON.
    """
    current_url = url
    assert_safe_url(current_url, allowed_hosts=allowed_hosts, context=context)

    for _hop in range(max_redirects + 1):
        try:
            response = requests.get(
                current_url,
                timeout=timeout,
                stream=True,
                allow_redirects=False,
                headers=headers or {},
            )
        except requests.RequestException as exc:
            raise OutboundFetchError(
                f"Network error calling external service: {redact_secrets(str(exc))}"
            ) from exc

        if response.status_code not in _REDIRECT_STATUS_CODES:
            break

        location = response.headers.get("Location")
        response.close()
        if not location:
            raise OutboundFetchError(
                f"Refusing to follow a redirect with no Location header from {current_url!r}"
            )
        # Resolve a relative Location the way a browser would, then
        # re-validate it exactly as the original was.
        current_url = urljoin(current_url, location)
        assert_safe_url(current_url, allowed_hosts=allowed_hosts, context=context)
    else:
        raise OutboundFetchError(f"Exceeded {max_redirects} redirect(s) calling external service")

    with response:
        if response.status_code >= 400:
            raise OutboundFetchError(f"External service returned HTTP {response.status_code}")

        declared = response.headers.get("Content-Length")
        if declared is not None:
            try:
                if int(declared) > max_bytes:
                    raise OutboundFetchError(
                        f"External response declares {declared} bytes, over the "
                        f"{max_bytes}-byte cap"
                    )
            except ValueError:
                # A malformed Content-Length is not fatal -- the streaming
                # check below is the real cap. Ignoring it here keeps a
                # merely sloppy server usable.
                pass

        chunks: list[bytes] = []
        received = 0
        for chunk in response.iter_content(chunk_size=8192):
            received += len(chunk)
            if received > max_bytes:
                raise OutboundFetchError(f"External response exceeded the {max_bytes}-byte cap")
            chunks.append(chunk)

    body = b"".join(chunks)
    try:
        return json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        # The body is untrusted data and is never echoed into the error --
        # it could carry anything, including injected instructions.
        raise OutboundFetchError(f"External response was not valid JSON: {exc}") from exc
