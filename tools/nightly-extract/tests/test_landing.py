from __future__ import annotations

from datetime import date

from trax_io_extract.landing import LandingSink, LocalFsSink, S3Sink, landing_prefix


def test_landing_prefix() -> None:
    assert landing_prefix(date(2026, 4, 17), "01JABC") == "extract_date=2026-04-17/run_id=01JABC"


def test_local_fs_sink_writes_and_returns_path(tmp_path) -> None:
    sink = LocalFsSink(tmp_path / "run")
    uri = sink.write("stock_amount.json", b"[]")
    assert (tmp_path / "run" / "stock_amount.json").read_bytes() == b"[]"
    assert uri.endswith("stock_amount.json")
    assert isinstance(sink, LandingSink)


class _FakeS3:
    def __init__(self) -> None:
        self.puts: list[dict] = []

    def put_object(self, **kw):  # noqa: ANN003
        self.puts.append(kw)
        return {"ETag": "fake"}


def test_s3_sink_puts_with_prefix() -> None:
    client = _FakeS3()
    sink = S3Sink(client, "trax-io-acme-prod-landing", prefix="extract_date=2026-04-17/run_id=01J")
    uri = sink.write("manifest.json", b'{"x":1}')
    assert uri == "s3://trax-io-acme-prod-landing/extract_date=2026-04-17/run_id=01J/manifest.json"
    put = client.puts[0]
    assert put["Bucket"] == "trax-io-acme-prod-landing"
    assert put["Key"] == "extract_date=2026-04-17/run_id=01J/manifest.json"
    assert put["Body"] == b'{"x":1}'
    assert "ServerSideEncryption" not in put  # no KMS unless a key id is given


def test_s3_sink_sse_kms_when_key_given() -> None:
    client = _FakeS3()
    sink = S3Sink(client, "b", sse_kms_key_id="arn:aws:kms:...:key/abc")
    sink.write("part_master.json", b"[]")
    put = client.puts[0]
    assert put["ServerSideEncryption"] == "aws:kms"
    assert put["SSEKMSKeyId"] == "arn:aws:kms:...:key/abc"
    assert put["Key"] == "part_master.json"  # no prefix -> bare key


# --- CLI sink construction (pure; no boto3 needed) ---
from trax_io_extract.cli import _build_sink, _s3_bucket_and_prefix  # noqa: E402


def test_s3_bucket_and_prefix_bucket_only() -> None:
    assert _s3_bucket_and_prefix("s3://my-bucket", "extract_date=2026-04-17/run_id=01J") == (
        "my-bucket", "extract_date=2026-04-17/run_id=01J")


def test_s3_bucket_and_prefix_with_base() -> None:
    assert _s3_bucket_and_prefix("s3://my-bucket/landing", "extract_date=2026-04-17/run_id=01J") == (
        "my-bucket", "landing/extract_date=2026-04-17/run_id=01J")


def test_build_sink_local(tmp_path) -> None:
    sink, desc = _build_sink(landing=None, output_dir=tmp_path, prefix="extract_date=X/run_id=Y",
                             kms_key_id=None)
    assert isinstance(sink, LocalFsSink)
    assert desc == str(tmp_path / "extract_date=X/run_id=Y")


# --- S3Sink edge guards (review fixes) ---
import pytest  # noqa: E402


def test_s3_sink_rejects_empty_bucket() -> None:
    with pytest.raises(ValueError):
        S3Sink(_FakeS3(), "")


def test_s3_sink_strips_leading_slash_no_double_segment() -> None:
    client = _FakeS3()
    sink = S3Sink(client, "b", prefix="p")
    sink.write("/manifest.json", b"x")
    assert client.puts[0]["Key"] == "p/manifest.json"  # not "p//manifest.json"
