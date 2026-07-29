"""Stable planning-run identity over immutable result-affecting inputs."""

from __future__ import annotations

import hashlib
import threading
import weakref

from trax_io_reco.candidate.identity import canonical_json
from trax_io_reco.contracts.planning import PortfolioSolveRequest

_IDENTITY_CACHE: dict[
    int,
    tuple[
        weakref.ReferenceType[PortfolioSolveRequest],
        tuple[str, str],
    ],
] = {}
_IDENTITY_CACHE_LOCK = threading.Lock()


def _menus_sha256(request: PortfolioSolveRequest) -> str:
    """Hash canonical menus incrementally without one giant JSON allocation."""

    digest = hashlib.sha256()
    digest.update(b"trax-io-portfolio-menu-sequence-v1\0")
    for menu in request.menus:
        encoded = canonical_json(
            menu,
            exclude_timestamps=True,
        ).encode()
        digest.update(len(encoded).to_bytes(8, byteorder="big"))
        digest.update(encoded)
    return digest.hexdigest()


def _compute_identity(
    request: PortfolioSolveRequest,
) -> tuple[str, str]:
    menus_sha256 = _menus_sha256(request)
    request_header = {
        field_name: getattr(request, field_name)
        for field_name in type(request).model_fields
        if field_name != "menus"
    }
    payload = canonical_json(
        {
            "namespace": "trax-io-portfolio-planning-input-v2",
            "request": request_header,
            "menus": {
                "count": len(request.menus),
                "sha256": menus_sha256,
            },
        },
        exclude_timestamps=True,
    ).encode()
    return (
        "planning_" + hashlib.sha256(payload).hexdigest(),
        "planning_menus_" + menus_sha256,
    )


def planning_identity(
    request: PortfolioSolveRequest,
) -> tuple[str, str]:
    """Return the run and menu identities, caching only this frozen instance.

    Submission, worker reconstruction, optimization, explanation, and terminal
    reconciliation all ask for the same identity. A weak identity cache avoids
    re-hashing 59K immutable menus at each boundary without retaining a request
    after its run leaves memory.
    """

    cache_key = id(request)
    with _IDENTITY_CACHE_LOCK:
        cached = _IDENTITY_CACHE.get(cache_key)
        if cached is not None and cached[0]() is request:
            return cached[1]

    identity = _compute_identity(request)

    def _discard(
        reference: weakref.ReferenceType[PortfolioSolveRequest],
        *,
        key: int = cache_key,
    ) -> None:
        with _IDENTITY_CACHE_LOCK:
            cached_entry = _IDENTITY_CACHE.get(key)
            if cached_entry is not None and cached_entry[0] is reference:
                _IDENTITY_CACHE.pop(key, None)

    reference = weakref.ref(request, _discard)
    with _IDENTITY_CACHE_LOCK:
        _IDENTITY_CACHE[cache_key] = (reference, identity)
    return identity


def planning_fingerprint(request: PortfolioSolveRequest) -> str:
    return planning_identity(request)[0]


def planning_menus_fingerprint(request: PortfolioSolveRequest) -> str:
    return planning_identity(request)[1]


__all__ = [
    "planning_fingerprint",
    "planning_identity",
    "planning_menus_fingerprint",
]
