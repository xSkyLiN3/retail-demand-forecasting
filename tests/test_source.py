from __future__ import annotations

import hashlib

import pytest

from retail_forecasting.source import SourceIntegrityError, sha256_file, verify_sha256


def test_sha256_verification_accepts_exact_file(tmp_path) -> None:
    payload = b"pinned-source"
    source = tmp_path / "source.zip"
    source.write_bytes(payload)
    expected = hashlib.sha256(payload).hexdigest()

    verify_sha256(source, expected)

    assert sha256_file(source) == expected


def test_sha256_verification_rejects_mismatch(tmp_path) -> None:
    source = tmp_path / "source.zip"
    source.write_bytes(b"unexpected")

    with pytest.raises(SourceIntegrityError, match="SHA-256 mismatch"):
        verify_sha256(source, "0" * 64)
