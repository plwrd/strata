"""Refuse fetches aimed at the user's own machine or network (SSRF).

Anything Strata fetches on a page's say-so — an imported URL, a video a page
points at — must not be able to reach ``localhost``, the router, or another
machine on the LAN: a hostile page would otherwise use Strata as a proxy into
places the page itself can never reach. Every address a host resolves to is
checked, and callers check again on each redirect hop.

Residual, documented in THREAT_MODEL.md: DNS rebinding between this check and
the connection.
"""

from __future__ import annotations

import ipaddress
import socket
from collections.abc import Callable
from urllib.parse import urlsplit

from app.domain.errors import InvalidRequestError, PermissionDeniedError

Address = ipaddress.IPv4Address | ipaddress.IPv6Address
Resolver = Callable[[str], list[Address]]


def resolve(host: str) -> list[Address]:
    try:
        return [ipaddress.ip_address(host)]
    except ValueError:
        pass
    try:
        infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    except OSError:
        raise InvalidRequestError("The host could not be resolved.") from None
    addresses: list[Address] = []
    for info in infos:
        try:
            addresses.append(ipaddress.ip_address(str(info[4][0])))
        except ValueError:
            continue
    if not addresses:
        raise InvalidRequestError("The host could not be resolved.")
    return addresses


def is_forbidden(address: Address) -> bool:
    mapped = getattr(address, "ipv4_mapped", None)
    if mapped is not None:
        address = mapped  # ::ffff:127.0.0.1 is loopback too
    return (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_multicast
        or address.is_reserved
        or address.is_unspecified
    )


def guard_public_url(
    url: str,
    *,
    message: str = "This address is not reachable from Strata.",
    resolver: Resolver = resolve,
) -> None:
    """Raise unless ``url`` is http(s) and every address of its host is public."""
    parts = urlsplit(url)
    if parts.scheme.lower() not in ("http", "https"):
        raise PermissionDeniedError("Only http and https addresses can be fetched.")
    host = parts.hostname or ""
    if not host:
        raise InvalidRequestError("That is not a valid URL.")
    if parts.username or parts.password:
        raise PermissionDeniedError("URLs with embedded credentials are not fetched.")
    try:
        # A literal address needs no lookup, and must not be at the mercy of one.
        addresses = [ipaddress.ip_address(host)]
    except ValueError:
        addresses = resolver(host)
    for address in addresses:
        if is_forbidden(address):
            # One generic message: the guard does not confirm what exists on
            # the network it just refused to touch.
            raise PermissionDeniedError(message)
