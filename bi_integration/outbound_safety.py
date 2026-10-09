"""SSRF hardening for `bi_integration/push.py`'s own outbound calls --
Prompt 48.

A Looker connector's own webhook URL is caller-supplied (each tenant's
own Looker instance) -- the identical threat `media_gen/download.py`'s
own module docstring describes for a provider-returned URL (a
compromised/malicious value could otherwise point this server-side call
at internal infrastructure: a cloud metadata endpoint, an internal admin
panel, `localhost`).

**Deliberately a second, independent copy of that module's own resolve-
and-blocklist-check logic, not an extraction into a shared helper.**
`media_gen/download.py`'s own test suite (`tests/test_media_gen.py`)
patches `media_gen.download.log_security_event` directly -- moving that
call into a shared module would silently break that patch target (the
call would then happen inside the *new* module's own namespace, not
`media_gen.download`'s), a real risk to an already-audited, passing test
suite for a marginal de-duplication benefit on ~15 lines of logic. This
module's own `assert_safe_outbound_url` calls `security.audit_log
.log_security_event` from *its own* namespace instead, the same
per-module-logs-its-own-event convention this codebase already uses at
every other `ssrf_blocked` call site.
"""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse

from security.audit_log import log_security_event

#: Identical range list to `media_gen/download.py::_BLOCKED_IP_NETWORKS`
#: -- see that module's own docstring for why each range is here
#: (private/loopback/link-local/reserved, IPv4 and IPv6 alike).
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


class OutboundUrlBlockedError(Exception):
    """Raised when a BI-connector outbound URL is not safe to call."""


def assert_safe_outbound_url(url: str, *, context: str) -> None:
    """Raises `OutboundUrlBlockedError` if `url` isn't safe to POST to
    server-side -- HTTPS-only, and the resolved address must not fall in
    a private/loopback/link-local/reserved range. A resolve-then-connect
    check, not a fully rebinding-proof pinned-IP call (the OS could
    theoretically re-resolve a different address between this check and
    the actual POST) -- the identical, disclosed scope
    `media_gen/download.py::_validate_download_url`'s own docstring
    already accepts for the same low-value-internal-call threat model.

    Args:
        url: The connector's own configured push/webhook URL.
        context: A short, stable label distinguishing *which* connector
            action this check is guarding (e.g. `"looker_webhook_push"`)
            -- included on every logged `ssrf_blocked` event so a
            pattern of rejections is attributable to the right feature.
    """
    parsed = urlparse(url)
    if parsed.scheme != "https":
        log_security_event(
            "ssrf_blocked",
            "warning",
            "Refused a BI-connector outbound call over a non-HTTPS scheme.",
            reason="non_https_scheme",
            scheme=parsed.scheme,
            context=context,
        )
        raise OutboundUrlBlockedError(
            f"Refusing to call a non-HTTPS BI-connector URL: {parsed.scheme!r}"
        )

    hostname = parsed.hostname
    if not hostname:
        log_security_event(
            "ssrf_blocked",
            "warning",
            "Refused a BI-connector outbound call: URL has no hostname.",
            reason="no_hostname",
            context=context,
        )
        raise OutboundUrlBlockedError("Refusing to call a BI-connector URL with no hostname")

    try:
        addrinfo = socket.getaddrinfo(hostname, None)
    except socket.gaierror as exc:
        raise OutboundUrlBlockedError(
            f"Could not resolve BI-connector host {hostname!r}: {exc}"
        ) from exc

    for _family, _type, _proto, _canonname, sockaddr in addrinfo:
        ip = ipaddress.ip_address(sockaddr[0])
        if any(ip in network for network in _BLOCKED_IP_NETWORKS):
            log_security_event(
                "ssrf_blocked",
                "warning",
                "Refused a BI-connector outbound call to a private/internal address -- "
                "possible SSRF via a tenant-supplied webhook/push URL.",
                reason="private_or_reserved_address",
                host=hostname,
                resolved_ip=str(ip),
                context=context,
            )
            raise OutboundUrlBlockedError(
                f"Refusing to call a BI-connector URL resolving to a private/internal "
                f"address ({ip})"
            )
