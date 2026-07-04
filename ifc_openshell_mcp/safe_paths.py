"""Sandbox d'entrée/sortie — miroir léger de ``audit_bim.safe_paths``.

- Les fichiers IFC lus sont contraints sous ``AUDIT_INPUT_DIR`` (si défini),
  sans traversal ``..`` et avec extension autorisée.
- Les JSON écrits sont contraints sous ``AUDIT_OUTPUT_DIR`` (défaut ``./out``),
  sans écrasement silencieux sauf ``overwrite=True``.

Objectif : que les JSON produits atterrissent au même endroit que celui lu
par ``audit-bim-i3f`` (aligner ``AUDIT_OUTPUT_DIR`` entre les deux serveurs).
"""

from __future__ import annotations

import os
from pathlib import Path


def _input_root() -> Path | None:
    raw = os.environ.get("AUDIT_INPUT_DIR")
    return Path(raw).expanduser().resolve() if raw else None


def _output_root() -> Path:
    raw = os.environ.get("AUDIT_OUTPUT_DIR", "./out")
    root = Path(raw).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def safe_input_path(
    path: str | os.PathLike,
    *,
    allowed_extensions: set[str] | None = None,
) -> Path:
    """Résout un chemin de lecture en le contraignant sous ``AUDIT_INPUT_DIR``.

    Si ``AUDIT_INPUT_DIR`` n'est pas défini, accepte un chemin absolu existant
    (mode local/dev).
    """
    p = Path(path).expanduser()
    root = _input_root()
    if root is not None:
        candidate = (root / p).resolve() if not p.is_absolute() else p.resolve()
        try:
            candidate.relative_to(root)
        except ValueError as exc:
            raise ValueError(
                f"Chemin hors AUDIT_INPUT_DIR ({root}) : {candidate}"
            ) from exc
    else:
        candidate = p.resolve()

    if not candidate.exists():
        raise FileNotFoundError(f"Fichier introuvable : {candidate}")
    if allowed_extensions and candidate.suffix.lower() not in allowed_extensions:
        raise ValueError(
            f"Extension non autorisée ({candidate.suffix}). "
            f"Attendu : {sorted(allowed_extensions)}"
        )
    return candidate


def safe_output_path(name: str, *, overwrite: bool = False) -> Path:
    """Résout un nom de fichier de sortie sous ``AUDIT_OUTPUT_DIR``."""
    root = _output_root()
    # On ne garde que le nom de fichier : pas de sous-dossier, pas de traversal.
    target = (root / Path(name).name).resolve()
    try:
        target.relative_to(root)
    except ValueError as exc:  # pragma: no cover - défensif
        raise ValueError(f"Chemin de sortie hors sandbox : {target}") from exc
    if target.exists() and not overwrite:
        raise FileExistsError(
            f"{target} existe déjà (passer overwrite=True pour remplacer)."
        )
    return target
