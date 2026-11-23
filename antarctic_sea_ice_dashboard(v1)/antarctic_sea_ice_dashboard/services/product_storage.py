"""POSIX local publication storage; all traversal uses no-follow descriptors."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
from typing import BinaryIO

from antarctic_sea_ice_dashboard.services.product_contracts import ProductManifest


APPLICATION_ROOT = Path(__file__).resolve().parents[2]
PRODUCT_STORAGE_ROOT = Path(os.environ.get(
    "SEA_ICE_PRODUCT_STORAGE_ROOT", str(APPLICATION_ROOT / ".data" / "research_products"),
))
CHUNK_BYTES = 1024 * 1024
MAX_FILE_BYTES = 128 * 1024 * 1024
DIRECTORY_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW


class ProductError(ValueError):
    """A safe business/storage failure with a stable recovery identity."""

    def __init__(self, code: str, message: str, product_id: str | None = None):
        super().__init__(message)
        self.code = code
        self.product_id = product_id


def canonical_json(value: object) -> bytes:
    """Contract v1: ASCII JSON, sorted keys, compact separators, no NaN or LF."""
    return json.dumps(value, sort_keys=True, ensure_ascii=True, allow_nan=False, separators=(",", ":")).encode("ascii")


def digest_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def relative_parts(value: str) -> tuple[str, ...]:
    if (not isinstance(value, str) or not value or "\\" in value or "\x00" in value
        or PurePosixPath(value).is_absolute()
        or any(part in {"", ".", ".."} for part in value.split("/"))):
        raise ProductError("INVALID_RESOURCE", "Use a controlled relative resource identifier.")
    return tuple(value.split("/"))


def require_product_id(product_id: str) -> None:
    if not isinstance(product_id, str) or re.fullmatch(r"[0-9a-f]{32}", product_id) is None:
        raise ProductError("INVALID_INPUT", "Product ID must be a saved 32-character hexadecimal identity.")


@contextmanager
def directory(path: Path, *, create: bool = False) -> Iterator[int]:
    """Open each absolute ancestor without following a symbolic link."""
    if not path.is_absolute():
        raise ProductError("STORAGE_CONFIGURATION", "The operator resource root must be absolute.")
    descriptor = os.open("/", DIRECTORY_FLAGS)
    try:
        for part in path.parts[1:]:
            if part in {".", ".."}:
                raise ProductError("INVALID_RESOURCE", "Resource root traversal is prohibited.")
            if create:
                try:
                    os.mkdir(part, mode=0o700, dir_fd=descriptor)
                except FileExistsError:
                    pass
            child = os.open(part, DIRECTORY_FLAGS, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        yield descriptor
    finally:
        os.close(descriptor)


@contextmanager
def source_file(root: Path, resource: str) -> Iterator[BinaryIO]:
    """Read only a regular, singly linked file below the configured root."""
    parts = relative_parts(resource)
    with directory(root) as root_fd:
        descriptor = os.dup(root_fd)
        try:
            for part in parts[:-1]:
                child = os.open(part, DIRECTORY_FLAGS, dir_fd=descriptor)
                os.close(descriptor)
                descriptor = child
            file_fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=descriptor)
            with os.fdopen(file_fd, "rb") as stream:
                info = os.fstat(stream.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                    raise ProductError("INVALID_RESOURCE", "Linked or non-regular resource files are not accepted.")
                yield stream
        finally:
            os.close(descriptor)


def stream_digest(stream: BinaryIO, *, maximum: int = MAX_FILE_BYTES) -> tuple[int, str]:
    digest = hashlib.sha256()
    size = 0
    for chunk in iter(lambda: stream.read(CHUNK_BYTES), b""):
        size += len(chunk)
        if size > maximum:
            raise ProductError("RESOURCE_LIMIT", "The selected file exceeds the supported resource limit.")
        digest.update(chunk)
    return size, digest.hexdigest()


class ProductStorage:
    """Hide staging, file locks and final placement behind product identities.

    One shared local POSIX filesystem is required. Persistent flock files are
    never unlinked; locks release on process exit and acquisition never waits.
    Runtime files remain below this application's ignored data directory.
    """

    def __enter__(self) -> ProductStorage:
        self._root_context = directory(PRODUCT_STORAGE_ROOT, create=True)
        self.root_fd = self._root_context.__enter__()
        try:
            devices: set[int] = set()
            for name in ("staging", "products", "locks"):
                try:
                    os.mkdir(name, mode=0o700, dir_fd=self.root_fd)
                except FileExistsError:
                    pass
                descriptor = os.open(name, DIRECTORY_FLAGS, dir_fd=self.root_fd)
                devices.add(os.fstat(descriptor).st_dev)
                os.close(descriptor)
            if len(devices) != 1:
                raise ProductError("STORAGE_CONFIGURATION", "Staging, products and locks must share one local filesystem.")
            return self
        except BaseException:
            self._root_context.__exit__(None, None, None)
            raise

    def __exit__(self, *args: object) -> None:
        self._root_context.__exit__(*args)

    @contextmanager
    def lock(self, product_id: str) -> Iterator[None]:
        require_product_id(product_id)
        locks_fd = os.open("locks", DIRECTORY_FLAGS, dir_fd=self.root_fd)
        try:
            descriptor = os.open(product_id, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600, dir_fd=locks_fd)
            try:
                info = os.fstat(descriptor)
                if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                    raise ProductError("STORAGE_UNAVAILABLE", "The product lock resource is invalid.", product_id)
                try:
                    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    raise ProductError("OPERATION_BUSY", "This product has an active worker. Read its status before retrying.", product_id) from None
                yield
            finally:
                os.close(descriptor)
        finally:
            os.close(locks_fd)

    def _name(self, product_id: str, attempt_id: str) -> str:
        require_product_id(product_id)
        require_product_id(attempt_id)
        return f"{product_id}-{attempt_id}"

    @contextmanager
    def area(self, product_id: str, attempt_id: str | None = None) -> Iterator[int]:
        require_product_id(product_id)
        parent = os.open("staging" if attempt_id else "products", DIRECTORY_FLAGS, dir_fd=self.root_fd)
        try:
            name = self._name(product_id, attempt_id) if attempt_id else product_id
            descriptor = os.open(name, DIRECTORY_FLAGS, dir_fd=parent)
            try:
                yield descriptor
            finally:
                os.close(descriptor)
        finally:
            os.close(parent)

    def create_stage(self, product_id: str, attempt_id: str) -> None:
        parent = os.open("staging", DIRECTORY_FLAGS, dir_fd=self.root_fd)
        try:
            os.mkdir(self._name(product_id, attempt_id), mode=0o700, dir_fd=parent)
            os.fsync(parent)
        finally:
            os.close(parent)

    @contextmanager
    def write_file(self, area_fd: int, name: str) -> Iterator[BinaryIO]:
        if len(relative_parts(name)) != 1:
            raise ProductError("INVALID_RESOURCE", "Product files must have flat controlled names.")
        descriptor = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=area_fd)
        with os.fdopen(descriptor, "wb") as stream:
            yield stream
            stream.flush()
            os.fsync(stream.fileno())

    def copy_file(self, area_fd: int, name: str, stream: BinaryIO, expected: tuple[int, str]) -> None:
        digest = hashlib.sha256()
        size = 0
        with self.write_file(area_fd, name) as target:
            for chunk in iter(lambda: stream.read(CHUNK_BYTES), b""):
                size += len(chunk)
                if size > MAX_FILE_BYTES or size > expected[0]:
                    raise ProductError("SOURCE_CHANGED", "Source size changed during the controlled copy.")
                target.write(chunk)
                digest.update(chunk)
        if (size, digest.hexdigest()) != expected:
            raise ProductError("SOURCE_CHANGED", "Source bytes changed during the controlled copy.")

    def verify(self, product_id: str, manifest: ProductManifest, attempt_id: str | None = None) -> None:
        """Hash manifest and every listed file on every consumption/recovery."""
        with self.area(product_id, attempt_id) as area_fd:
            expected_names = {"manifest.json"}
            for item in manifest["files"]:
                name = item["relative_path"]
                if len(relative_parts(name)) != 1 or name in expected_names:
                    raise ProductError("INTEGRITY_UNAVAILABLE", "Product file inventory is invalid.", product_id)
                expected_names.add(name)
                with self.read_file(area_fd, name) as stream:
                    if stream_digest(stream) != (item["size_bytes"], item["sha256"]):
                        raise ProductError("INTEGRITY_UNAVAILABLE", "A product file is missing or has changed.", product_id)
            if set(os.listdir(area_fd)) != expected_names:
                raise ProductError("INTEGRITY_UNAVAILABLE", "The product directory inventory has changed.", product_id)
            with self.read_file(area_fd, "manifest.json") as stream:
                if stream.read(8 * 1024 * 1024 + 1) != canonical_json(manifest):
                    raise ProductError("INTEGRITY_UNAVAILABLE", "The product manifest has changed.", product_id)

    @contextmanager
    def read_file(self, area_fd: int, name: str) -> Iterator[BinaryIO]:
        if len(relative_parts(name)) != 1:
            raise ProductError("INVALID_RESOURCE", "Product files must have flat controlled names.")
        descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=area_fd)
        with os.fdopen(descriptor, "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise ProductError("INTEGRITY_UNAVAILABLE", "Product files must be independent regular files.")
            yield stream

    def final_exists(self, product_id: str) -> bool:
        require_product_id(product_id)
        parent = os.open("products", DIRECTORY_FLAGS, dir_fd=self.root_fd)
        try:
            try:
                os.stat(product_id, dir_fd=parent, follow_symlinks=False)
                return True
            except FileNotFoundError:
                return False
        finally:
            os.close(parent)

    def promote(self, product_id: str, attempt_id: str) -> None:
        """Caller holds the product lock; never replace any existing final path."""
        if self.final_exists(product_id):
            raise ProductError("FINAL_ALREADY_EXISTS", "Recover and verify the existing final product.", product_id)
        with self.area(product_id, attempt_id) as area_fd:
            for name in os.listdir(area_fd):
                with self.read_file(area_fd, name) as stream:
                    os.fchmod(stream.fileno(), 0o400)
            # macOS requires a writable source directory during rename.
            # Seal it only after promotion, before database visibility.
            os.fsync(area_fd)
        staging_fd = os.open("staging", DIRECTORY_FLAGS, dir_fd=self.root_fd)
        final_fd = os.open("products", DIRECTORY_FLAGS, dir_fd=self.root_fd)
        try:
            os.rename(self._name(product_id, attempt_id), product_id, src_dir_fd=staging_fd, dst_dir_fd=final_fd)
            os.fsync(final_fd)
            os.fsync(staging_fd)
        finally:
            os.close(staging_fd)
            os.close(final_fd)

    def seal(self, product_id: str) -> None:
        """Make final files read-only, also when recovering an interrupted rename."""
        with self.area(product_id) as area_fd:
            for name in os.listdir(area_fd):
                with self.read_file(area_fd, name) as stream:
                    os.fchmod(stream.fileno(), 0o400)
            os.fchmod(area_fd, 0o500)
            os.fsync(area_fd)

    def discard_stage(self, product_id: str, attempt_id: str) -> None:
        """Only under this product's lock and using its saved attempt identity."""
        try:
            with self.area(product_id, attempt_id) as area_fd:
                os.fchmod(area_fd, 0o700)
                for name in os.listdir(area_fd):
                    # Flat files only; never recurse or follow a directory/link.
                    with self.read_file(area_fd, name):
                        pass
                    os.unlink(name, dir_fd=area_fd)
        except FileNotFoundError:
            return
        parent = os.open("staging", DIRECTORY_FLAGS, dir_fd=self.root_fd)
        try:
            os.rmdir(self._name(product_id, attempt_id), dir_fd=parent)
        finally:
            os.close(parent)
