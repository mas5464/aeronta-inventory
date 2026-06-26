"""Landing sinks — where the extract writes its per-domain artifacts + manifest.

A `LandingSink` is injected into the runner so the same extract code lands either to local
disk (dev / offline) or to S3 (production), and so the upload mechanism is swappable. Two
implementations ship here:

- ``LocalFsSink`` — writes under a root directory (today's behavior).
- ``S3Sink`` — PUTs to an S3 bucket via an injected boto3-style client (so it is testable
  with a fake client; ``boto3`` is only needed to build the real client, in the CLI).

A future credential-less variant (the customer DBA holds no AWS creds) is just another
``LandingSink`` — e.g. a ``PresignedUrlSink`` that PUTs to presigned URLs minted by a Trax
service — and plugs in here without touching the runner. Per-tenant KMS (SSE-KMS) is wired
into ``S3Sink`` as an optional key id, gated on sub-project #9 exporting the key ARN.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Protocol, runtime_checkable


def landing_prefix(extract_date: date, run_id: str) -> str:
    """The contract landing partition prefix: ``extract_date=YYYY-MM-DD/run_id=<ulid>``."""
    return f"extract_date={extract_date.isoformat()}/run_id={run_id}"


@runtime_checkable
class LandingSink(Protocol):
    def write(self, relative_path: str, payload: bytes) -> str:
        """Write one object; return its landing URI (s3:// or a local path)."""
        ...


class LocalFsSink:
    """Writes artifacts under ``root`` on local disk."""

    def __init__(self, root: Path) -> None:
        self._root = Path(root)

    def write(self, relative_path: str, payload: bytes) -> str:
        path = self._root / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
        return str(path)


class S3Sink:
    """PUTs artifacts to ``s3://<bucket>/<prefix>/<relative_path>`` via an injected client.

    ``client`` is any object exposing boto3's ``put_object(Bucket=, Key=, Body=, **kw)`` —
    a real ``boto3.client("s3")`` in production, a fake in tests. ``sse_kms_key_id`` enables
    SSE-KMS envelope encryption with the per-tenant CMK when supplied.
    """

    def __init__(
        self, client: object, bucket: str, *, prefix: str = "", sse_kms_key_id: str | None = None
    ) -> None:
        if not bucket:
            raise ValueError("S3Sink requires a non-empty bucket")
        self._client = client
        self._bucket = bucket
        self._prefix = prefix.strip("/")
        self._sse_kms_key_id = sse_kms_key_id

    def write(self, relative_path: str, payload: bytes) -> str:
        relative_path = relative_path.lstrip("/")  # avoid an empty path segment in the key
        key = f"{self._prefix}/{relative_path}" if self._prefix else relative_path
        extra: dict[str, str] = {}
        if self._sse_kms_key_id:
            extra["ServerSideEncryption"] = "aws:kms"
            extra["SSEKMSKeyId"] = self._sse_kms_key_id
        self._client.put_object(Bucket=self._bucket, Key=key, Body=payload, **extra)  # type: ignore[attr-defined]
        return f"s3://{self._bucket}/{key}"
