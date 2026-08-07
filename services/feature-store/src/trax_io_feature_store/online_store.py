"""Generation-safe DynamoDB online feature storage.

Bundles are staged under immutable generation-prefixed sort keys. A single
tenant-scoped pointer item is conditionally replaced only after every bundle in
the new population succeeds. Readers pin that committed generation for both
key enumeration and point reads, so a failed writer cannot expose a mixed
old/new tenant snapshot and removed keys cannot leak from an older pass.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from pydantic import ValidationError

from trax_io_feature_store.client import FeatureStoreLookupError, TenantContext, _require_tenant
from trax_io_feature_store.schemas import FeatureBundle

_POINTER_SORT_KEY = "_meta#population"
_BUNDLE_PREFIX = "_bundle#"
_GENERATION_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


@dataclass(frozen=True)
class OnlineGeneration:
    """A tenant-bound committed population token used by serving reads."""

    tenant_id: str
    generation: str
    key_count: int


@dataclass(frozen=True)
class PopulationStage:
    """An uncommitted copy-on-write population and its compare-and-swap base."""

    tenant_id: str
    generation: str
    previous_generation: str | None


def _sort_key(pn: str, location: str) -> str:
    """Injective ``(pn, location)`` -> sort-key encoding.

    A plain ``f"{pn}#{location}"`` is ambiguous — ``("A#B","C")`` and ``("A","B#C")`` both encode
    to ``"A#B#C"`` and silently collide onto one DynamoDB item. eMRO part numbers and location
    codes can contain ``#`` (and any other punctuation), so the length prefix makes the encoding
    provably injective regardless of their contents.
    """
    return f"{len(pn)}#{pn}#{location}"


def _decode_sort_key(value: str) -> tuple[str, str]:
    """Inverse of :func:`_sort_key`, rejecting malformed or ambiguous values."""

    try:
        raw_length, remainder = value.split("#", 1)
        pn_length = int(raw_length)
    except (AttributeError, TypeError, ValueError) as exc:
        raise ValueError(f"invalid pn_location sort key: {value!r}") from exc
    if pn_length <= 0 or raw_length != str(pn_length):
        raise ValueError(f"invalid pn_location sort key: {value!r}")
    if len(remainder) <= pn_length or remainder[pn_length] != "#":
        raise ValueError(f"invalid pn_location sort key: {value!r}")
    pn = remainder[:pn_length]
    location = remainder[pn_length + 1 :]
    if not pn or not location or _sort_key(pn, location) != value:
        raise ValueError(f"invalid pn_location sort key: {value!r}")
    return pn, location


def _require_generation(value: str) -> str:
    if not isinstance(value, str) or not _GENERATION_PATTERN.fullmatch(value):
        raise ValueError(f"invalid online population generation: {value!r}")
    return value


def _bundle_prefix(generation: str) -> str:
    return f"{_BUNDLE_PREFIX}{_require_generation(generation)}#"


def _bundle_sort_key(generation: str, pn: str, location: str) -> str:
    return f"{_bundle_prefix(generation)}{_sort_key(pn, location)}"


def _decode_bundle_sort_key(value: str, generation: str) -> tuple[str, str]:
    prefix = _bundle_prefix(generation)
    if not isinstance(value, str) or not value.startswith(prefix):
        raise ValueError(
            f"online bundle key is outside generation {generation!r}: {value!r}"
        )
    return _decode_sort_key(value[len(prefix) :])


class DynamoDbOnlineStore:
    """Stage, commit, and read generation-pinned online feature bundles."""

    def __init__(self, *, table: Any) -> None:
        # `table` is a boto3 DynamoDB Table resource (real in prod, moto-backed in tests).
        self._table = table

    def _read_current_generation(
        self,
        *,
        tenant: TenantContext,
    ) -> OnlineGeneration | None:
        response = self._table.get_item(
            Key={
                "tenant_id": tenant.tenant_id,
                "pn_location": _POINTER_SORT_KEY,
            },
            ConsistentRead=True,
        )
        item = response.get("Item")
        if item is None:
            return None
        try:
            generation = _require_generation(item["generation"])
            raw_key_count = item["key_count"]
        except (KeyError, TypeError, ValueError) as exc:
            raise FeatureStoreLookupError(
                f"invalid committed online population for tenant={tenant.tenant_id}"
            ) from exc
        if isinstance(raw_key_count, bool):
            raise FeatureStoreLookupError(
                f"invalid committed online population for tenant={tenant.tenant_id}"
            )
        if type(raw_key_count) is int:
            key_count = raw_key_count
        elif (
            isinstance(raw_key_count, Decimal)
            and raw_key_count.is_finite()
            and raw_key_count == raw_key_count.to_integral_value()
        ):
            key_count = int(raw_key_count)
        else:
            raise FeatureStoreLookupError(
                f"invalid committed online population for tenant={tenant.tenant_id}"
            )
        if key_count < 0:
            raise FeatureStoreLookupError(
                f"invalid committed online population for tenant={tenant.tenant_id}"
            )
        return OnlineGeneration(
            tenant_id=tenant.tenant_id,
            generation=generation,
            key_count=key_count,
        )

    def current_generation(self, *, tenant: TenantContext) -> OnlineGeneration:
        """Return the tenant's one committed population pointer."""

        tenant = _require_tenant(tenant)
        generation = self._read_current_generation(tenant=tenant)
        if generation is None:
            raise FeatureStoreLookupError(
                f"no committed online population for tenant={tenant.tenant_id}"
            )
        return generation

    def begin_population(self, *, tenant: TenantContext) -> PopulationStage:
        """Allocate an invisible generation based on the current pointer."""

        tenant = _require_tenant(tenant)
        current = self._read_current_generation(tenant=tenant)
        return PopulationStage(
            tenant_id=tenant.tenant_id,
            generation=uuid.uuid4().hex,
            previous_generation=(
                current.generation
                if current is not None
                else None
            ),
        )

    def put_bundle(
        self,
        bundle: FeatureBundle,
        *,
        stage: PopulationStage,
    ) -> None:
        """Stage one bundle without changing anything visible to readers."""

        if not isinstance(stage, PopulationStage):
            raise TypeError("stage must be a PopulationStage")
        if bundle.tenant_id != stage.tenant_id:
            raise ValueError(
                "population stage tenant mismatch: "
                f"stage={stage.tenant_id!r}, bundle={bundle.tenant_id!r}"
            )
        self._table.put_item(
            Item={
                "tenant_id": bundle.tenant_id,
                "pn_location": _bundle_sort_key(
                    stage.generation,
                    bundle.pn,
                    bundle.location,
                ),
                "body": bundle.model_dump_json(),
            },
            # A generation key is content-addressed by (tenant, generation,
            # planning key). Retrying the same key must never overwrite an
            # earlier staged body: a duplicate indicates a broken/doubled
            # population and the writer must fail before pointer commit.
            ConditionExpression="attribute_not_exists(#pn_location)",
            ExpressionAttributeNames={"#pn_location": "pn_location"},
        )

    def commit_population(
        self,
        *,
        stage: PopulationStage,
        key_count: int,
    ) -> OnlineGeneration:
        """Atomically expose a staged generation with a tenant-local CAS pointer."""

        if not isinstance(stage, PopulationStage):
            raise TypeError("stage must be a PopulationStage")
        if (
            not isinstance(stage.tenant_id, str)
            or not stage.tenant_id.strip()
        ):
            raise ValueError("population stage tenant_id must be non-empty")
        _require_generation(stage.generation)
        if stage.previous_generation is not None:
            _require_generation(stage.previous_generation)
        if not isinstance(key_count, int) or isinstance(key_count, bool) or key_count < 0:
            raise ValueError("key_count must be a nonnegative integer")
        names = {"#generation": "generation"}
        values: dict[str, str] = {}
        if stage.previous_generation is None:
            condition = "attribute_not_exists(#generation)"
        else:
            condition = "#generation = :previous_generation"
            values[":previous_generation"] = stage.previous_generation
        kwargs: dict[str, Any] = {
            "Item": {
                "tenant_id": stage.tenant_id,
                "pn_location": _POINTER_SORT_KEY,
                "generation": stage.generation,
                "key_count": key_count,
                "committed_at": datetime.now(timezone.utc).isoformat(),
            },
            "ConditionExpression": condition,
            "ExpressionAttributeNames": names,
        }
        if values:
            kwargs["ExpressionAttributeValues"] = values
        self._table.put_item(**kwargs)
        return OnlineGeneration(
            tenant_id=stage.tenant_id,
            generation=stage.generation,
            key_count=key_count,
        )

    @staticmethod
    def _resolve_generation(
        *,
        tenant: TenantContext,
        generation: OnlineGeneration | None,
        store: DynamoDbOnlineStore,
    ) -> OnlineGeneration:
        resolved = (
            store.current_generation(tenant=tenant)
            if generation is None
            else generation
        )
        if (
            not isinstance(resolved, OnlineGeneration)
            or resolved.tenant_id != tenant.tenant_id
        ):
            raise FeatureStoreLookupError(
                "online generation tenant mismatch for "
                f"tenant={tenant.tenant_id}"
            )
        try:
            _require_generation(resolved.generation)
        except ValueError as exc:
            raise FeatureStoreLookupError(
                f"invalid online generation for tenant={tenant.tenant_id}"
            ) from exc
        if type(resolved.key_count) is not int or resolved.key_count < 0:
            raise FeatureStoreLookupError(
                f"invalid online generation for tenant={tenant.tenant_id}"
            )
        return resolved

    def iter_keys(
        self,
        *,
        tenant: TenantContext,
        generation: OnlineGeneration | None = None,
    ) -> tuple[tuple[str, str], ...]:
        """Query keys from exactly one committed tenant population."""

        tenant = _require_tenant(tenant)
        resolved = self._resolve_generation(
            tenant=tenant,
            generation=generation,
            store=self,
        )
        prefix = _bundle_prefix(resolved.generation)
        query: dict[str, Any] = {
            "KeyConditionExpression": (
                "#tenant_id = :tenant_id AND "
                "begins_with(#pn_location, :generation_prefix)"
            ),
            "ExpressionAttributeNames": {
                "#tenant_id": "tenant_id",
                "#pn_location": "pn_location",
            },
            "ExpressionAttributeValues": {
                ":tenant_id": tenant.tenant_id,
                ":generation_prefix": prefix,
            },
            "ProjectionExpression": "pn_location",
            "ConsistentRead": True,
        }
        keys: set[tuple[str, str]] = set()
        while True:
            response = self._table.query(**query)
            for item in response.get("Items", []):
                try:
                    keys.add(
                        _decode_bundle_sort_key(
                            item["pn_location"],
                            resolved.generation,
                        )
                    )
                except (KeyError, TypeError, ValueError) as exc:
                    raise FeatureStoreLookupError(
                        "invalid online key for "
                        f"tenant={tenant.tenant_id}: {item!r}"
                    ) from exc
            last_key = response.get("LastEvaluatedKey")
            if not last_key:
                break
            query["ExclusiveStartKey"] = last_key
        if len(keys) != resolved.key_count:
            raise FeatureStoreLookupError(
                "committed online population key-count mismatch for "
                f"tenant={tenant.tenant_id} generation={resolved.generation!r}: "
                f"pointer={resolved.key_count}, actual={len(keys)}"
            )
        return tuple(sorted(keys))

    def get_bundle(
        self,
        *,
        tenant: TenantContext,
        pn: str,
        location: str,
        generation: OnlineGeneration | None = None,
    ) -> FeatureBundle:
        """Fetch the online bundle for ``(tenant, pn, location)``.

        When ``generation`` is supplied, the caller remains pinned even if a
        newer population commits during a multi-key serving bootstrap.
        """
        tenant = _require_tenant(tenant)
        resolved = self._resolve_generation(
            tenant=tenant,
            generation=generation,
            store=self,
        )
        resp = self._table.get_item(
            Key={
                "tenant_id": tenant.tenant_id,
                "pn_location": _bundle_sort_key(
                    resolved.generation,
                    pn,
                    location,
                ),
            },
            ConsistentRead=True,
        )
        item = resp.get("Item")
        if not item or "body" not in item:
            raise FeatureStoreLookupError(
                f"no online bundle for tenant={tenant.tenant_id} pn={pn} location={location}"
            )
        try:
            bundle = FeatureBundle.model_validate_json(item["body"])
        except (ValidationError, TypeError, ValueError) as exc:
            raise FeatureStoreLookupError(
                f"invalid online bundle for tenant={tenant.tenant_id} "
                f"pn={pn} location={location}"
            ) from exc
        # Defense-in-depth against any future key drift: the item's own body must describe the
        # key we asked for (the body carries the true pn/location).
        if bundle.tenant_id != tenant.tenant_id or bundle.pn != pn or bundle.location != location:
            raise FeatureStoreLookupError(
                f"online bundle key mismatch for tenant={tenant.tenant_id} pn={pn} "
                f"location={location} (got {bundle.tenant_id}/{bundle.pn}/{bundle.location})"
            )
        return bundle
