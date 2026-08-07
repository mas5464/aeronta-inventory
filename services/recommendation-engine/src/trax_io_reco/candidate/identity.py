"""Canonical, timestamp-independent identities for candidate planning."""

from __future__ import annotations

import dataclasses
import hashlib
import json
import math
from collections.abc import Iterator, Mapping
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Any

from pydantic import BaseModel

from trax_io_reco.contracts.candidate import (
    CANDIDATE_CONTRACT_VERSION,
    CandidateFingerprintInputs,
    PolicyCandidate,
)

_TIMESTAMP_METADATA_FIELDS = frozenset(
    {
        "created_at",
        "generated_at",
        "requested_at",
        "started_at",
        "completed_at",
        "updated_at",
        "timestamp",
    }
)


def _decimal_text(value: Decimal) -> str:
    if not value.is_finite():
        raise ValueError("non-finite decimals cannot be hashed")
    if value == 0:
        return "0"
    normalized = value.normalize()
    return format(normalized, "f")


def _canonical_value(value: Any, *, exclude_timestamps: bool) -> Any:
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="python")
    elif dataclasses.is_dataclass(value) and not isinstance(value, type):
        value = dataclasses.asdict(value)
    elif isinstance(value, Enum):
        value = value.value

    if isinstance(value, Mapping):
        normalized: dict[str, Any] = {}
        for raw_key, item in value.items():
            key = str(raw_key)
            if exclude_timestamps and key in _TIMESTAMP_METADATA_FIELDS:
                continue
            normalized[key] = _canonical_value(
                item,
                exclude_timestamps=exclude_timestamps,
            )
        return {key: normalized[key] for key in sorted(normalized)}
    if isinstance(value, (tuple, list)):
        return [_canonical_value(item, exclude_timestamps=exclude_timestamps) for item in value]
    if isinstance(value, (set, frozenset)):
        items = [_canonical_value(item, exclude_timestamps=exclude_timestamps) for item in value]
        return sorted(
            items,
            key=lambda item: json.dumps(item, sort_keys=True, separators=(",", ":")),
        )
    if isinstance(value, Decimal):
        return {"$decimal": _decimal_text(value)}
    if isinstance(value, datetime):
        return {"$datetime": value.isoformat()}
    if isinstance(value, date):
        return {"$date": value.isoformat()}
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("non-finite floats cannot be hashed")
        return {"$float": repr(value)}
    if value is None or isinstance(value, (str, int, bool)):
        return value
    raise TypeError(f"unsupported canonical-hash value: {type(value).__name__}")


def canonical_json(value: Any, *, exclude_timestamps: bool = False) -> str:
    """Return stable JSON with explicit decimal/date type markers."""

    normalized = _canonical_value(value, exclude_timestamps=exclude_timestamps)
    return json.dumps(
        normalized,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )


def _canonical_chunks(
    value: Any,
    *,
    exclude_timestamps: bool,
) -> Iterator[str]:
    """Yield canonical JSON without materializing a second nested object graph.

    ``canonical_json`` remains the public text representation. Large portfolio
    identities only need a digest, though, and building both a recursively
    normalized 59K-key dictionary and one giant JSON string creates avoidable
    multi-gigabyte peaks. This encoder intentionally mirrors
    :func:`_canonical_value` byte-for-byte while keeping only one shallow
    container at a time.
    """

    if isinstance(value, BaseModel):
        value = {
            field_name: getattr(value, field_name)
            for field_name in type(value).model_fields
        }
    elif dataclasses.is_dataclass(value) and not isinstance(value, type):
        value = {
            field.name: getattr(value, field.name)
            for field in dataclasses.fields(value)
        }
    elif isinstance(value, Enum):
        value = value.value

    if isinstance(value, Mapping):
        normalized: dict[str, Any] = {}
        for raw_key, item in value.items():
            key = str(raw_key)
            if exclude_timestamps and key in _TIMESTAMP_METADATA_FIELDS:
                continue
            normalized[key] = item
        yield "{"
        for index, key in enumerate(sorted(normalized)):
            if index:
                yield ","
            yield json.dumps(key, ensure_ascii=False)
            yield ":"
            yield from _canonical_chunks(
                normalized[key],
                exclude_timestamps=exclude_timestamps,
            )
        yield "}"
        return
    if isinstance(value, (tuple, list)):
        yield "["
        for index, item in enumerate(value):
            if index:
                yield ","
            yield from _canonical_chunks(
                item,
                exclude_timestamps=exclude_timestamps,
            )
        yield "]"
        return
    if isinstance(value, (set, frozenset)):
        items = [
            "".join(
                _canonical_chunks(
                    item,
                    exclude_timestamps=exclude_timestamps,
                )
            )
            for item in value
        ]
        yield "["
        yield ",".join(sorted(items))
        yield "]"
        return
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError("non-finite decimals cannot be hashed")
        yield '{"$decimal":'
        yield json.dumps(_decimal_text(value), ensure_ascii=False)
        yield "}"
        return
    if isinstance(value, datetime):
        yield '{"$datetime":'
        yield json.dumps(value.isoformat(), ensure_ascii=False)
        yield "}"
        return
    if isinstance(value, date):
        yield '{"$date":'
        yield json.dumps(value.isoformat(), ensure_ascii=False)
        yield "}"
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("non-finite floats cannot be hashed")
        yield '{"$float":'
        yield json.dumps(repr(value), ensure_ascii=False)
        yield "}"
        return
    if value is None or isinstance(value, (str, int, bool)):
        yield json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
        )
        return
    raise TypeError(
        f"unsupported canonical-hash value: {type(value).__name__}"
    )


def canonical_sha256(
    value: Any,
    *,
    exclude_timestamps: bool = False,
) -> str:
    """Hash canonical content incrementally with the canonical JSON semantics."""

    digest = hashlib.sha256()
    pending: list[str] = []
    pending_characters = 0
    for chunk in _canonical_chunks(
        value,
        exclude_timestamps=exclude_timestamps,
    ):
        pending.append(chunk)
        pending_characters += len(chunk)
        if pending_characters >= 1024 * 1024:
            digest.update("".join(pending).encode())
            pending.clear()
            pending_characters = 0
    if pending:
        digest.update("".join(pending).encode())
    return digest.hexdigest()


def _sha256(value: Any, *, exclude_timestamps: bool = False) -> str:
    payload = canonical_json(value, exclude_timestamps=exclude_timestamps).encode()
    return hashlib.sha256(payload).hexdigest()


def frontier_fingerprint(inputs: CandidateFingerprintInputs | Mapping[str, Any]) -> str:
    """Hash every supplied result input while excluding operational timestamps."""

    return "frontier_" + _sha256(
        {
            "namespace": "trax-io-candidate-frontier-input-v1",
            "inputs": inputs,
        },
        exclude_timestamps=True,
    )


def content_digest(value: Any) -> str:
    """Digest canonical content for snapshot/context fingerprint components."""

    return "content_" + _sha256(value, exclude_timestamps=True)


def candidate_payload(candidate: PolicyCandidate | Mapping[str, Any]) -> dict[str, Any]:
    """Return the canonical candidate content whose identity excludes its id."""

    if isinstance(candidate, PolicyCandidate):
        payload = candidate.model_dump(mode="python")
    else:
        payload = dict(candidate)
    payload.pop("candidate_id", None)
    payload.setdefault("contract_version", CANDIDATE_CONTRACT_VERSION)
    return payload


def candidate_identifier(
    frontier_id: str,
    candidate: PolicyCandidate | Mapping[str, Any],
) -> str:
    """Derive a stable id from the input frontier and canonical candidate content."""

    return "cand_" + _sha256(
        {
            "namespace": "trax-io-policy-candidate-v1",
            "frontier_fingerprint": frontier_id,
            "candidate": candidate_payload(candidate),
        },
        exclude_timestamps=True,
    )


def output_digest(
    *,
    frontier_id: str,
    tenant_id: str,
    decision_key: str,
    member_keys: tuple[str, ...],
    currency: str,
    candidates: tuple[PolicyCandidate, ...],
    dominated_options_removed: int,
) -> str:
    """Hash produced content separately from the result-affecting input identity."""

    return "output_" + _sha256(
        {
            "namespace": "trax-io-candidate-frontier-output-v1",
            "frontier_fingerprint": frontier_id,
            "tenant_id": tenant_id,
            "decision_key": decision_key,
            "member_keys": member_keys,
            "currency": currency,
            "candidates": candidates,
            "dominated_options_removed": dominated_options_removed,
        },
        exclude_timestamps=True,
    )


__all__ = [
    "candidate_identifier",
    "candidate_payload",
    "canonical_json",
    "canonical_sha256",
    "content_digest",
    "frontier_fingerprint",
    "output_digest",
]
