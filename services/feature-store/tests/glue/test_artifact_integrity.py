from __future__ import annotations

import hashlib

import pytest

from trax_io_feature_store.glue._common import verify_artifact_integrity


class _Frame:
    def __init__(self, rows: int):
        self._rows = rows

    def count(self) -> int:
        return self._rows


class _Bytes:
    def __init__(self, payload: bytes):
        self._payload = payload

    def read(self) -> bytes:
        return self._payload


class _BinaryFiles:
    def __init__(self, payload: bytes):
        self._payload = payload

    def collect(self):
        return [("s3://landing/domain.json", _Bytes(self._payload))]


class _SparkContext:
    def __init__(self, payload: bytes):
        self._payload = payload

    def binaryFiles(self, _uri: str):  # noqa: N802 - mirrors PySpark API
        return _BinaryFiles(self._payload)


class _Spark:
    def __init__(self, payload: bytes):
        self.sparkContext = _SparkContext(payload)


def test_manifest_row_count_and_sha256_are_verified() -> None:
    payload = b"[]"
    verify_artifact_integrity(
        _Spark(payload),
        {
            "domain": "order_plan",
            "s3_uri": "s3://landing/domain.json",
            "row_count": 0,
            "sha256": hashlib.sha256(payload).hexdigest(),
        },
        _Frame(0),
    )


@pytest.mark.parametrize(
    ("row_count", "sha256", "match"),
    [
        (2, None, "row_count mismatch"),
        (1, "0" * 64, "sha256 mismatch"),
    ],
)
def test_manifest_integrity_mismatch_fails_closed(
    row_count,
    sha256,
    match,
) -> None:
    payload = b'[{"id":1}]'
    artifact = {
        "domain": "order_plan",
        "s3_uri": "s3://landing/domain.json",
        "row_count": row_count,
    }
    if sha256 is not None:
        artifact["sha256"] = sha256

    with pytest.raises(ValueError, match=match):
        verify_artifact_integrity(
            _Spark(payload),
            artifact,
            _Frame(1),
        )
