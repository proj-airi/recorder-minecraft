from __future__ import annotations

import errno
import hashlib
import os
import stat
import tempfile
import zipfile
from pathlib import Path
from typing import Mapping

from minerec.processing.bundle.contract import (
    OPTIONAL_RENDER_ENTRY_NAMES,
    build_metadata,
    bundle_output_path,
    canonical_json_bytes,
    replay_entry_name,
    validate_limits,
    validate_metadata,
)
from minerec.processing.bundle.model import (
    DEFAULT_BUNDLE_LIMITS,
    ArtifactSource,
    BundleError,
    BundleLimits,
    BundleRequest,
    PathValidator,
    PublishedBundle,
    RenderValidator,
)
from minerec.processing.bundle.reader import open_bundle

_COPY_CHUNK_BYTES = 1024 * 1024
_ZIP_TIMESTAMP = (1980, 1, 1, 0, 0, 0)


def _ensure_directory(path: Path) -> None:
    try:
        path.mkdir(parents=True, exist_ok=True)
        status = path.lstat()
    except OSError as exc:
        raise BundleError(f"cannot create bundle publication directory: {path}") from exc
    if stat.S_ISLNK(status.st_mode) or not stat.S_ISDIR(status.st_mode):
        raise BundleError(f"bundle publication directory is not safe: {path}")
    _fsync_directory(path)
    _fsync_directory(path.parent)


def _safe_create_parents(root: Path, destination: Path) -> None:
    try:
        relative = destination.parent.relative_to(root)
    except ValueError as exc:
        raise BundleError("bundle destination escapes the artifacts root") from exc
    current = root
    for part in relative.parts:
        current = current / part
        created = False
        try:
            current.mkdir()
            created = True
        except FileExistsError:
            pass
        except OSError as exc:
            raise BundleError(f"cannot create bundle layout directory: {current}") from exc
        try:
            status = current.lstat()
        except OSError as exc:
            raise BundleError(f"cannot inspect bundle layout directory: {current}") from exc
        if stat.S_ISLNK(status.st_mode) or not stat.S_ISDIR(status.st_mode):
            raise BundleError(f"bundle layout directory is not safe: {current}")
        if created:
            _fsync_directory(current)
            _fsync_directory(current.parent)


def _copy_verified_source(source: ArtifactSource, destination: Path, description: str) -> None:
    try:
        before = source.path.lstat()
    except OSError as exc:
        raise BundleError(f"cannot inspect {description}: {source.path}") from exc
    if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
        raise BundleError(f"{description} must be a non-symlinked regular file")
    if before.st_size != source.size_bytes:
        raise BundleError(f"{description} size changed before bundle publication")
    destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    digest = hashlib.sha256()
    observed = 0
    try:
        with source.path.open("rb") as input_handle:
            opened = os.fstat(input_handle.fileno())
            if (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino):
                raise BundleError(f"{description} changed while it was being opened")
            descriptor = os.open(
                destination,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0),
                0o600,
            )
            with os.fdopen(descriptor, "wb") as output_handle:
                while chunk := input_handle.read(_COPY_CHUNK_BYTES):
                    observed += len(chunk)
                    if observed > source.size_bytes:
                        raise BundleError(f"{description} grew during bundle publication")
                    digest.update(chunk)
                    output_handle.write(chunk)
                output_handle.flush()
                os.fsync(output_handle.fileno())
            after_open = os.fstat(input_handle.fileno())
        after_path = source.path.lstat()
    except BundleError:
        raise
    except OSError as exc:
        raise BundleError(f"cannot copy {description} into private bundle staging") from exc
    identities = (
        (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns),
        (after_open.st_dev, after_open.st_ino, after_open.st_size, after_open.st_mtime_ns),
        (after_path.st_dev, after_path.st_ino, after_path.st_size, after_path.st_mtime_ns),
    )
    if identities[0] != identities[1] or identities[0] != identities[2]:
        raise BundleError(f"{description} changed during bundle publication")
    if observed != source.size_bytes or digest.hexdigest() != source.sha256:
        raise BundleError(f"{description} failed its verified SHA-256 or size check")


def _zip_info(name: str, compression: int) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(filename=name, date_time=_ZIP_TIMESTAMP)
    info.compress_type = compression
    info.create_system = 3
    info.external_attr = (stat.S_IFREG | 0o600) << 16
    info.flag_bits |= 0x800
    return info


def _write_bytes_entry(
    archive: zipfile.ZipFile,
    name: str,
    data: bytes,
    compression: int,
) -> None:
    info = _zip_info(name, compression)
    info.file_size = len(data)
    with archive.open(info, "w", force_zip64=True) as target:
        target.write(data)


def _copy_entry(
    archive: zipfile.ZipFile,
    name: str,
    source: Path,
    compression: int,
) -> None:
    info = _zip_info(name, compression)
    info.file_size = source.stat().st_size
    with (
        source.open("rb") as input_handle,
        archive.open(
            info,
            "w",
            force_zip64=True,
        ) as output_handle,
    ):
        while chunk := input_handle.read(_COPY_CHUNK_BYTES):
            output_handle.write(chunk)


def _write_bundle_zip(
    output: Path,
    metadata_bytes: bytes,
    staged: Mapping[str, Path],
    replay_names: tuple[str, ...],
    has_render: bool,
) -> None:
    try:
        with zipfile.ZipFile(
            output,
            "x",
            compression=zipfile.ZIP_DEFLATED,
            compresslevel=9,
            allowZip64=True,
            strict_timestamps=True,
        ) as archive:
            _write_bytes_entry(
                archive,
                "metadata.json",
                metadata_bytes,
                zipfile.ZIP_DEFLATED,
            )
            _copy_entry(
                archive,
                "actions.jsonl",
                staged["actions.jsonl"],
                zipfile.ZIP_DEFLATED,
            )
            _copy_entry(
                archive,
                "scene.sqlite3",
                staged["scene.sqlite3"],
                zipfile.ZIP_STORED,
            )
            for replay_name in replay_names:
                _copy_entry(
                    archive,
                    replay_name,
                    staged[replay_name],
                    zipfile.ZIP_STORED,
                )
            if has_render:
                _copy_entry(
                    archive,
                    OPTIONAL_RENDER_ENTRY_NAMES[0],
                    staged[OPTIONAL_RENDER_ENTRY_NAMES[0]],
                    zipfile.ZIP_STORED,
                )
                _copy_entry(
                    archive,
                    OPTIONAL_RENDER_ENTRY_NAMES[1],
                    staged[OPTIONAL_RENDER_ENTRY_NAMES[1]],
                    zipfile.ZIP_DEFLATED,
                )
        with output.open("rb") as handle:
            os.fsync(handle.fileno())
    except BundleError:
        raise
    except (OSError, RuntimeError, ValueError, zipfile.LargeZipFile) as exc:
        raise BundleError("cannot build the play bundle ZIP in private staging") from exc


def _fsync_directory(path: Path) -> None:
    try:
        descriptor = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    except OSError:
        pass
    finally:
        os.close(descriptor)


def _validate_existing(
    destination: Path,
    expected_metadata: bytes,
    *,
    scene_validator: PathValidator | None,
    actions_validator: PathValidator | None,
    render_validator: RenderValidator | None,
    limits: BundleLimits,
    temp_root: Path,
) -> PublishedBundle:
    try:
        status = destination.lstat()
    except OSError as exc:
        raise BundleError(f"cannot inspect existing bundle destination: {destination}") from exc
    if stat.S_ISLNK(status.st_mode) or not stat.S_ISREG(status.st_mode):
        raise BundleError("existing bundle destination is not a regular immutable bundle")
    try:
        with open_bundle(
            destination,
            scene_validator=scene_validator,
            actions_validator=actions_validator,
            render_validator=render_validator,
            limits=limits,
            temp_root=temp_root,
        ) as opened:
            if opened._metadata_json != expected_metadata:
                raise BundleError("existing bundle destination does not contain identical inputs")
            return PublishedBundle(
                path=destination,
                bundle_id=opened.bundle_id,
                reused=True,
            )
    except BundleError as exc:
        raise BundleError("existing immutable bundle destination failed validation") from exc


def publish_bundle(
    request: BundleRequest,
    artifacts_root: Path,
    *,
    scene_validator: PathValidator | None = None,
    actions_validator: PathValidator | None = None,
    render_validator: RenderValidator | None = None,
    limits: BundleLimits = DEFAULT_BUNDLE_LIMITS,
) -> PublishedBundle:
    """Build, reopen, fsync, and atomically publish one connection bundle."""

    validate_limits(limits)
    metadata = build_metadata(request)
    metadata_bytes = canonical_json_bytes(metadata)
    # Parsing our own output here keeps publisher and reader schema decisions
    # on exactly the same boundary before any source bytes are copied.
    validate_metadata(metadata_bytes)
    destination = bundle_output_path(artifacts_root, request.identity, metadata["bundle_id"])
    _ensure_directory(artifacts_root)

    try:
        with tempfile.TemporaryDirectory(
            prefix=".minerec-play-bundle-stage-",
            dir=artifacts_root,
        ) as temporary_name:
            staging_root = Path(temporary_name)
            staging_root.chmod(0o700)
            sources_root = staging_root / "sources"
            sources_root.mkdir(mode=0o700)
            staged: dict[str, Path] = {}

            def stage(name: str, source: ArtifactSource, description: str) -> None:
                target = sources_root.joinpath(*name.split("/"))
                _copy_verified_source(source, target, description)
                staged[name] = target

            stage("actions.jsonl", request.actions, "reconstructed actions")
            stage("scene.sqlite3", request.scene, "Scene Store V2")
            replay_names: list[str] = []
            for ordinal, segment in enumerate(request.replay_segments):
                name = replay_entry_name(ordinal, segment.segment_id)
                replay_names.append(name)
                stage(name, segment.source, f"replay segment {segment.segment_id}")
            if request.render is not None:
                stage(
                    OPTIONAL_RENDER_ENTRY_NAMES[0],
                    request.render.video,
                    "FPV render",
                )
                stage(
                    OPTIONAL_RENDER_ENTRY_NAMES[1],
                    request.render.timeline,
                    "FPV render timeline",
                )

            staged_zip = staging_root / f"{metadata['bundle_id']}.staging.mcplay.zip"
            _write_bundle_zip(
                staged_zip,
                metadata_bytes,
                staged,
                tuple(replay_names),
                request.render is not None,
            )
            with open_bundle(
                staged_zip,
                scene_validator=scene_validator,
                actions_validator=actions_validator,
                render_validator=render_validator,
                limits=limits,
                temp_root=staging_root,
            ) as reopened:
                if reopened.bundle_id != metadata["bundle_id"] or reopened._metadata_json != metadata_bytes:
                    raise BundleError("staged play bundle changed during reader revalidation")

            _fsync_directory(staging_root)
            _safe_create_parents(artifacts_root, destination)
            if destination.exists() or destination.is_symlink():
                return _validate_existing(
                    destination,
                    metadata_bytes,
                    scene_validator=scene_validator,
                    actions_validator=actions_validator,
                    render_validator=render_validator,
                    limits=limits,
                    temp_root=staging_root,
                )
            try:
                os.link(staged_zip, destination)
            except FileExistsError:
                return _validate_existing(
                    destination,
                    metadata_bytes,
                    scene_validator=scene_validator,
                    actions_validator=actions_validator,
                    render_validator=render_validator,
                    limits=limits,
                    temp_root=staging_root,
                )
            except OSError as exc:
                if exc.errno == errno.EEXIST:
                    return _validate_existing(
                        destination,
                        metadata_bytes,
                        scene_validator=scene_validator,
                        actions_validator=actions_validator,
                        render_validator=render_validator,
                        limits=limits,
                        temp_root=staging_root,
                    )
                raise BundleError("cannot atomically publish the validated play bundle") from exc
            _fsync_directory(destination.parent)
            return PublishedBundle(
                path=destination,
                bundle_id=metadata["bundle_id"],
                reused=False,
            )
    except BundleError:
        raise
    except OSError as exc:
        raise BundleError("play bundle publication failed while staging local files") from exc
