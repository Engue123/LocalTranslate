"""
Model presence + download management for the selectable translation models.

- Q4 ships natively; Q6/Q8 are downloaded once from Hugging Face into models/.
- Downloads are streamed (in-process), resumable (.part + HTTP Range), and
  verified by exact byte size before being promoted to the final filename.
"""
import os
import urllib.request
from pathlib import Path
from typing import Optional, Callable

from core.model_registry import ModelSpec, get, all_models

_PROJECT_ROOT = Path(__file__).resolve().parent.parent


class DownloadCancelled(Exception):
    pass


def default_models_dir() -> Path:
    d = _PROJECT_ROOT / "models"
    d.mkdir(parents=True, exist_ok=True)
    return d


def local_path(spec: ModelSpec, models_dir: Optional[Path] = None) -> Optional[Path]:
    """Return the path to a present, complete model file, or None."""
    models_dir = Path(models_dir) if models_dir else default_models_dir()
    for name in spec.filenames:
        p = models_dir / name
        if p.exists() and p.stat().st_size == spec.size:
            return p
    return None


def is_ready(spec: ModelSpec, models_dir: Optional[Path] = None) -> bool:
    return local_path(spec, models_dir) is not None


def resolve_path(model_id: Optional[str], models_dir: Optional[Path] = None) -> Optional[Path]:
    """Path for an explicit selection, else the best ready model (Q8>Q6>Q4).

    The automatic fallback only ever considers the universal (pure-MT) tier:
    the Quality tier (instruct, heavy) must be an explicit user choice.
    """
    if model_id:
        spec = get(model_id)
        if spec:
            return local_path(spec, models_dir)
    for spec in sorted(all_models(), key=lambda m: m.size, reverse=True):
        if spec.instruct:
            continue  # opt-in only — never the silent default
        p = local_path(spec, models_dir)
        if p:
            return p
    return None


def download(
    spec: ModelSpec,
    models_dir: Optional[Path] = None,
    progress: Optional[Callable[[float, int, int], None]] = None,
    cancel: Optional["object"] = None,
    chunk_size: int = 1 << 20,
    _opener: Callable = urllib.request.urlopen,
) -> Path:
    """
    Download `spec` into models_dir, resuming a partial `.part` if present.
    `progress(fraction, downloaded, total)` is called as bytes arrive.
    Raises DownloadCancelled if `cancel.is_set()`; ValueError on size mismatch.
    """
    if not spec.url:
        raise ValueError(f"Model {spec.id!r} has no download URL")

    models_dir = Path(models_dir) if models_dir else default_models_dir()
    models_dir.mkdir(parents=True, exist_ok=True)
    dest = models_dir / spec.filename
    part = dest.with_name(dest.name + ".part")

    if dest.exists() and dest.stat().st_size == spec.size:
        return dest

    resume_from = part.stat().st_size if part.exists() else 0
    req = urllib.request.Request(spec.url, headers={"User-Agent": "LocalTranslate"})
    if resume_from:
        req.add_header("Range", f"bytes={resume_from}-")

    response = _opener(req, timeout=60)
    status = getattr(response, "status", 200)
    # If the server ignored our Range (200 instead of 206), restart cleanly.
    if resume_from and status != 206:
        resume_from = 0

    total = spec.size
    downloaded = resume_from
    mode = "ab" if resume_from else "wb"
    try:
        with open(part, mode) as f:
            while True:
                if cancel is not None and getattr(cancel, "is_set", lambda: False)():
                    raise DownloadCancelled()
                buf = response.read(chunk_size)
                if not buf:
                    break
                f.write(buf)
                downloaded += len(buf)
                if progress:
                    progress(downloaded / total if total else 0.0, downloaded, total)
    finally:
        close = getattr(response, "close", None)
        if close:
            close()

    actual = part.stat().st_size
    if total and actual != total:
        raise ValueError(f"Download size mismatch for {spec.id}: {actual} != {total}")

    os.replace(part, dest)
    return dest
