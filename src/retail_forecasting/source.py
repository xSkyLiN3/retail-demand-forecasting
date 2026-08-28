from __future__ import annotations

import hashlib
import os
import shutil
from pathlib import Path, PurePosixPath
from urllib.request import Request, urlopen
from zipfile import BadZipFile, ZipFile

from retail_forecasting.config import (
    SOURCE_ARCHIVE_NAME,
    SOURCE_SHA256,
    SOURCE_URL,
    SOURCE_WORKBOOK_NAME,
)

DOWNLOAD_CHUNK_BYTES = 1024 * 1024
MAX_WORKBOOK_BYTES = 200 * 1024 * 1024


class SourceIntegrityError(RuntimeError):
    """Raised when the downloaded source does not match its contract."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(DOWNLOAD_CHUNK_BYTES), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_sha256(path: Path, expected_sha256: str) -> None:
    actual = sha256_file(path)
    if actual.lower() != expected_sha256.lower():
        raise SourceIntegrityError(
            f"SHA-256 mismatch for {path}: expected {expected_sha256}, got {actual}"
        )


def download_archive(
    raw_dir: Path,
    *,
    url: str = SOURCE_URL,
    expected_sha256: str = SOURCE_SHA256,
) -> Path:
    if not expected_sha256 or expected_sha256.startswith("__"):
        raise SourceIntegrityError("The official source SHA-256 has not been pinned yet.")

    raw_dir.mkdir(parents=True, exist_ok=True)
    destination = raw_dir / SOURCE_ARCHIVE_NAME

    if destination.is_file():
        verify_sha256(destination, expected_sha256)
        return destination

    temporary = destination.with_suffix(destination.suffix + ".part")
    temporary.unlink(missing_ok=True)
    request = Request(url, headers={"User-Agent": "retail-demand-forecasting/0.1"})

    try:
        with urlopen(request, timeout=60) as response, temporary.open("wb") as target:
            shutil.copyfileobj(response, target, length=DOWNLOAD_CHUNK_BYTES)
        verify_sha256(temporary, expected_sha256)
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)

    return destination


def extract_workbook(archive_path: Path, raw_dir: Path) -> Path:
    if not archive_path.is_file():
        raise FileNotFoundError(f"Source archive not found: {archive_path}")

    raw_dir.mkdir(parents=True, exist_ok=True)
    destination = raw_dir / SOURCE_WORKBOOK_NAME
    temporary = destination.with_suffix(destination.suffix + ".part")
    temporary.unlink(missing_ok=True)

    try:
        with ZipFile(archive_path) as archive:
            corrupt_member = archive.testzip()
            if corrupt_member is not None:
                raise SourceIntegrityError(f"Corrupt ZIP member: {corrupt_member}")

            matches = [
                info
                for info in archive.infolist()
                if PurePosixPath(info.filename).name.casefold() == SOURCE_WORKBOOK_NAME.casefold()
                and not info.is_dir()
            ]
            if len(matches) != 1:
                raise SourceIntegrityError(
                    f"Expected one {SOURCE_WORKBOOK_NAME!r} member, found {len(matches)}"
                )

            member = matches[0]
            if member.file_size <= 0 or member.file_size > MAX_WORKBOOK_BYTES:
                raise SourceIntegrityError(
                    f"Unexpected workbook size in archive: {member.file_size} bytes"
                )

            with archive.open(member) as source, temporary.open("wb") as target:
                shutil.copyfileobj(source, target, length=DOWNLOAD_CHUNK_BYTES)
        os.replace(temporary, destination)
    except BadZipFile as exc:
        raise SourceIntegrityError(f"Invalid ZIP archive: {archive_path}") from exc
    finally:
        temporary.unlink(missing_ok=True)

    return destination
