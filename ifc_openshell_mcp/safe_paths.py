"""Ré-export : sandbox de chemins I/O (package ``bim-sandbox``, profil **ifc**).

L'implémentation vit désormais dans ``bim-sandbox`` (partagée avec audit-bim-i3f,
deux profils historiques). Ce module ré-exporte le profil **ifc** derrière les
signatures historiques — aucun call-site à réécrire, comportement et erreurs
observables inchangés :

- ``safe_input_path`` : base ``AUDIT_INPUT_DIR``, existence + extension (si
  fournie). Mince wrapper fixant ``profile="ifc"``.
- ``safe_output_path`` : ré-export direct — aplatit vers ``Path(name).name`` et
  conserve ``FileExistsError`` sur fichier existant.
"""

from __future__ import annotations

import os
from pathlib import Path

from bim_sandbox import safe_output_path  # noqa: F401 — ré-export direct
from bim_sandbox import safe_input_path as _bs_safe_input_path

__all__ = ["safe_input_path", "safe_output_path"]


def safe_input_path(
    path: str | os.PathLike,
    *,
    allowed_extensions: set[str] | None = None,
) -> Path:
    """Résout un chemin de lecture (profil **ifc** de bim-sandbox).

    Base ``AUDIT_INPUT_DIR`` (relatifs résolus sous la racine), existence, puis
    extension **uniquement si** ``allowed_extensions`` est fourni. Signature
    publique historique préservée.
    """
    return _bs_safe_input_path(
        path, profile="ifc", allowed_extensions=allowed_extensions
    )
