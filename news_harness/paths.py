"""Path and artifact-writing safety helpers."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .config import find_raw_secret_material
from .fixtures import ROOT


SYSTEM_PATH_PREFIXES = (Path("/etc"), Path("/tmp"), Path("/var"), Path("/private"), Path("/System"))


class UnsafeOutputPathError(ValueError):
    """Raised when a requested output path leaves the project output surface."""


class RawSecretMaterialError(ValueError):
    """Raised before an artifact write would persist raw secret material."""


def safe_output_path(base_dir: Path, user_path: Path) -> Path:
    """Resolve a user output path under a project-rooted output base."""

    base = base_dir.resolve()
    if not _is_relative_to(base, ROOT.resolve()):
        raise UnsafeOutputPathError(f"output base must be inside project root: {base_dir}")
    if user_path.is_absolute():
        raise UnsafeOutputPathError(f"absolute output paths are not allowed: {user_path}")
    if ".." in user_path.parts:
        raise UnsafeOutputPathError(f"parent traversal is not allowed: {user_path}")

    if user_path.parts and user_path.parts[0] == base.name:
        candidate = (ROOT / user_path).resolve()
    else:
        candidate = (base / user_path).resolve()

    if not _is_relative_to(candidate, base):
        raise UnsafeOutputPathError(f"output path escapes {base}: {user_path}")
    if any(candidate == prefix or _is_relative_to(candidate, prefix) for prefix in SYSTEM_PATH_PREFIXES):
        raise UnsafeOutputPathError(f"system output paths are not allowed: {user_path}")
    return candidate


def write_json_artifact(path: Path, data: Any) -> None:
    """Write JSON only after a deep raw-secret scan of the actual payload."""

    findings = find_raw_secret_material(data)
    if findings:
        raise RawSecretMaterialError(f"raw secret material present before writing {path}: {findings}")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2, sort_keys=True)
        handle.write("\n")


def write_text_artifact(path: Path, text: str) -> None:
    findings = find_raw_secret_material(text)
    if findings:
        raise RawSecretMaterialError(f"raw secret material present before writing {path}: {findings}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _is_relative_to(path: Path, base: Path) -> bool:
    try:
        path.relative_to(base)
    except ValueError:
        return False
    return True
