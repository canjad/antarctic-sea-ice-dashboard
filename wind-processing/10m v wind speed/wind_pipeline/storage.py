"""Publish complete wind products without replacing an existing generation."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


def _occupied(path: Path) -> bool:
    """Treat a dangling symbolic link as an occupied destination too."""
    return path.exists() or path.is_symlink()


@contextmanager
def staged_product(destination: Path) -> Iterator[Path]:
    """Validate a fresh generation in isolation, then publish by directory rename.

    The caller writes and verifies every artifact inside the yielded directory.
    A failed run removes its staging directory and leaves existing products
    intact. Each run has a unique directory, so concurrent runs cannot modify
    each other's files. Completed products are nonempty; the final rename also
    fails if another run publishes to the same destination first.
    """
    destination = Path(destination).expanduser().absolute()
    if _occupied(destination):
        raise FileExistsError(
            f"Output destination already exists: {destination}. "
            "Select a new --output-dir to preserve the completed generation."
        )
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(
        tempfile.mkdtemp(prefix=f".{destination.name}.staging-", dir=destination.parent)
    )
    try:
        yield staging
        if not any(staging.iterdir()):
            raise ValueError("A complete product must contain verified artifacts.")
        if _occupied(destination):
            raise FileExistsError(
                f"Another writer created the output destination: {destination}. "
                "Select a new --output-dir."
            )
        os.rename(staging, destination)
    finally:
        if staging.exists():
            shutil.rmtree(staging)


def atomic_write_text(path: Path, content: str) -> None:
    """Replace a UTF-8 document only after its complete contents are written."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as stream:
            temporary = Path(stream.name)
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def atomic_write_json(path: Path, value: object) -> None:
    """Write strict JSON; reject nonfinite numbers before replacing a document."""
    content = json.dumps(value, indent=2, ensure_ascii=True, allow_nan=False) + "\n"
    atomic_write_text(path, content)


import hashlib

import numpy as np


def file_hash(path):
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def array_digest(*arrays: np.ndarray) -> str:
    digest = hashlib.sha256()
    for values in arrays:
        digest.update(np.ascontiguousarray(values, dtype="<f8").tobytes())
    return digest.hexdigest()


def say(message: str) -> None:
    print(message, flush=True)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)
