"""Ré-export : sandbox de chemins I/O (``bim_core.paths``, profil **ifc**).

L'implémentation vit dans ``bim-core`` (module :mod:`bim_core.paths`, partagé
avec audit-bim-i3f, deux profils historiques). Elle transitait auparavant par le
package ``bim-sandbox``, désormais décommissionné : l'import a été repointé vers
la source, sans passer par le shim. Aucun call-site à réécrire, comportement et
erreurs observables inchangés :

- ``safe_input_path`` : base ``AUDIT_INPUT_DIR``, existence + extension (si
  fournie). Mince wrapper fixant ``profile="ifc"``.
- ``safe_output_path`` : ré-export direct — aplatit vers ``Path(name).name`` et
  conserve ``FileExistsError`` sur fichier existant.
"""

from __future__ import annotations

import os
from pathlib import Path

from bim_core.paths import safe_output_path  # noqa: F401 — ré-export direct
from bim_core.paths import safe_input_path as _core_safe_input_path

__all__ = ["safe_input_path", "safe_output_path"]


def safe_input_path(
    path: str | os.PathLike,
    *,
    allowed_extensions: set[str] | None = None,
) -> Path:
    """Résout un chemin de lecture (profil **ifc** de ``bim_core.paths``).

    Base ``AUDIT_INPUT_DIR`` (relatifs résolus sous la racine), existence, puis
    extension **uniquement si** ``allowed_extensions`` est fourni. Signature
    publique historique préservée.
    """
    return _core_safe_input_path(
        path, profile="ifc", allowed_extensions=allowed_extensions
    )
