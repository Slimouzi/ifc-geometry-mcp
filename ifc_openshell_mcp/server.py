"""Serveur FastMCP ``ifc-geometry`` — 5 outils d'audit géométrique.

Chaque outil ouvre une maquette IFC, exécute son analyse et écrit un JSON sous
``AUDIT_OUTPUT_DIR`` (aligné sur celui d'``audit-bim-i3f`` pour que
``import_preliminary_findings`` retrouve les fichiers). Le retour de chaque
outil est compact : compteurs + chemin du JSON produit.
"""

from __future__ import annotations

import json
import logging
from datetime import date
from typing import Any

from dotenv import load_dotenv
from fastmcp import FastMCP

from . import ifc_utils
from .analyzers import (
    boundaries,
    envelope,
    inventory,
    openings,
    space_clash,
    surface_loss,
)
from .safe_paths import safe_input_path, safe_output_path

load_dotenv()
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("ifc_openshell_mcp")

mcp = FastMCP("ifc-geometry")

_IFC_EXT = {".ifc", ".ifczip", ".ifcxml"}


def _load(ifc_path: str):
    """Résout + ouvre une maquette IFC (sandbox lecture)."""
    safe = safe_input_path(ifc_path, allowed_extensions=_IFC_EXT)
    return ifc_utils.open_model(str(safe)), safe


def _write(stem: str, suffix: str, payload: dict, overwrite: bool) -> str:
    target = safe_output_path(f"{stem}{suffix}", overwrite=overwrite)
    target.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return str(target)


# --------------------------------------------------------------------------- #
#  1. Inventaire pièces / zones
# --------------------------------------------------------------------------- #
@mcp.tool()
def extract_space_inventory(
    ifc_path: str,
    min_area_threshold_m2: float = 9.0,
    area_tol_pct: float = 5.0,
    stale_after_days: int = 90,
    analysis_date: str | None = None,
    overwrite: bool = False,
) -> dict[str, Any]:
    """Inventaire pièce par pièce + zones + nommage + fraîcheur du modèle.

    Écrit ``<stem>_space_inventory.json`` (schéma ``extract_space_inventory``).

    Args:
        ifc_path: Chemin de la maquette IFC (sandbox ``AUDIT_INPUT_DIR``).
        min_area_threshold_m2: Seuil de surface mini des pièces habitables
            (défaut 9 m² — à confirmer avec I3F).
        area_tol_pct: Tolérance d'écart surface déclarée/recalculée (défaut 5 %).
        stale_after_days: Âge max de l'export avant alerte fraîcheur (défaut 90 j).
        analysis_date: Date d'analyse ISO (défaut aujourd'hui) pour l'écart avec
            la date d'export.
        overwrite: Écrase le JSON existant.
    """
    model, safe = _load(ifc_path)
    ad = date.fromisoformat(analysis_date) if analysis_date else date.today()
    payload = inventory.run(
        model,
        file_name=safe.name,
        min_area_threshold_m2=min_area_threshold_m2,
        area_tol_pct=area_tol_pct,
        stale_after_days=stale_after_days,
        analysis_date=ad,
    )
    path = _write(safe.stem, "_space_inventory.json", payload, overwrite)
    return {"json_path": path, "counts": payload["counts"], "dates": payload["dates"]}


# --------------------------------------------------------------------------- #
#  2. Clash espaces
# --------------------------------------------------------------------------- #
@mcp.tool()
def run_space_clash_audit(
    ifc_path: str,
    overlap_min_ratio: float = 0.10,
    duplicate_ratio: float = 0.90,
    closet_inside_ratio: float = 0.80,
    vertical_min_overlap_m: float = 0.20,
    overwrite: bool = False,
) -> dict[str, Any]:
    """Détecte doublons, chevauchements et placards double-modélisés.

    Écrit ``<stem>_space_clash_findings.json`` (schéma ``run_space_clash_audit``).

    Args:
        ifc_path: Chemin de la maquette IFC.
        overlap_min_ratio: Recouvrement mini (sur la plus petite pièce) pour un
            chevauchement (défaut 0.10).
        duplicate_ratio: Recouvrement mutuel au-delà duquel deux pièces sont
            considérées en doublon (défaut 0.90).
        closet_inside_ratio: Inclusion mini d'un placard dans une pièce pour un
            double-modélisé (défaut 0.80).
        vertical_min_overlap_m: Chevauchement vertical mini (m) entre pièces
            d'étages différents (défaut 0.20).
        overwrite: Écrase le JSON existant.
    """
    model, safe = _load(ifc_path)
    payload = space_clash.run(
        model,
        overlap_min_ratio=overlap_min_ratio,
        duplicate_ratio=duplicate_ratio,
        closet_inside_ratio=closet_inside_ratio,
        vertical_min_overlap_m=vertical_min_overlap_m,
    )
    path = _write(safe.stem, "_space_clash_findings.json", payload, overwrite)
    return {"json_path": path, "counts": payload["counts"]}


# --------------------------------------------------------------------------- #
#  3. Pertes de surface
# --------------------------------------------------------------------------- #
@mcp.tool()
def compute_surface_loss(
    ifc_path: str,
    overwrite: bool = False,
) -> dict[str, Any]:
    """Calcule les m² perdus par pièce (empiètement murs/poteaux).

    Écrit ``<stem>_surface_loss.json`` (schéma ``compute_surface_loss``).

    Args:
        ifc_path: Chemin de la maquette IFC.
        overwrite: Écrase le JSON existant.
    """
    model, safe = _load(ifc_path)
    payload = surface_loss.run(model)
    path = _write(safe.stem, "_surface_loss.json", payload, overwrite)
    return {"json_path": path, "counts": payload["counts"]}


# --------------------------------------------------------------------------- #
#  4. Limites d'espaces
# --------------------------------------------------------------------------- #
@mcp.tool()
def check_space_boundaries(
    ifc_path: str,
    adjacency_tol_m: float = 0.35,
    overwrite: bool = False,
) -> dict[str, Any]:
    """Détecte les limites manquantes entre pièces adjacentes.

    Écrit ``<stem>_boundaries.json`` (schéma ``check_space_boundaries``).

    Args:
        ifc_path: Chemin de la maquette IFC.
        adjacency_tol_m: Distance max (m) entre contours pour considérer deux
            pièces adjacentes (défaut 0.35, ~épaisseur d'un mus).
        overwrite: Écrase le JSON existant.
    """
    model, safe = _load(ifc_path)
    payload = boundaries.run(model, adjacency_tol_m=adjacency_tol_m)
    path = _write(safe.stem, "_boundaries.json", payload, overwrite)
    return {"json_path": path, "counts": payload["counts"]}


# --------------------------------------------------------------------------- #
#  5. Correspondance d'ouvertures Archi ↔ Structure
# --------------------------------------------------------------------------- #
@mcp.tool()
def check_opening_correspondence(
    ifc_path: str,
    structure_ifc_path: str | None = None,
    tolerance_m: float = 0.10,
    overwrite: bool = False,
) -> dict[str, Any]:
    """Vérifie que chaque réservation structure a une ouverture archi.

    Écrit ``<stem>_openings_check.json`` (schéma ``check_opening_correspondence``).

    Args:
        ifc_path: Maquette archi (ou combinée si ``structure_ifc_path`` absent).
        structure_ifc_path: Maquette structure (mode bi-maquette recommandé).
        tolerance_m: Tolérance d'appariement des centroïdes d'ouvertures
            (défaut 0.10 m).
        overwrite: Écrase le JSON existant.
    """
    model, safe = _load(ifc_path)
    struct_model = struct_safe = None
    if structure_ifc_path:
        struct_model, struct_safe = _load(structure_ifc_path)
    payload = openings.run(
        model,
        file_name=safe.name,
        structure_model=struct_model,
        structure_file_name=struct_safe.name if struct_safe else None,
        tolerance_m=tolerance_m,
    )
    path = _write(safe.stem, "_openings_check.json", payload, overwrite)
    return {
        "json_path": path,
        "counts": payload["counts"],
        "structure_sans_reservations": payload["structure_sans_reservations"],
        "misaligned_models": payload["misaligned_models"],
    }


# --------------------------------------------------------------------------- #
#  6. Surfaces d'enveloppe (façades / menuiseries / SHAB / ratio)
# --------------------------------------------------------------------------- #
@mcp.tool()
def extract_envelope_surfaces(
    ifc_path: str,
    seuil_3f: float | None = None,
    overwrite: bool = False,
) -> dict[str, Any]:
    """Calcule les surfaces d'enveloppe (façades, menuiseries, SHAB, ratio).

    Écrit ``<stem>_envelope.json`` ET ``<stem>_enveloppe.xlsx`` (ce dernier au
    format attendu par le pack I3F — à passer en ``enveloppe_xlsx`` à
    ``generate_avp_i3f_pack``).

    Args:
        ifc_path: Chemin de la maquette IFC.
        seuil_3f: Seuil réglementaire 3F du ratio FAC/SHAB (optionnel, politique
            externe — laissé vide si non fourni).
        overwrite: Écrase les fichiers existants.
    """
    model, safe = _load(ifc_path)
    payload = envelope.run(model, file_name=safe.name, seuil_3f=seuil_3f)
    json_path = _write(safe.stem, "_envelope.json", payload, overwrite)
    xlsx_path = safe_output_path(f"{safe.stem}_enveloppe.xlsx", overwrite=overwrite)
    envelope.write_xlsx(payload, str(xlsx_path))
    return {
        "json_path": json_path,
        "xlsx_path": str(xlsx_path),
        "superficie_facades_m2": payload["superficie_facades_m2"],
        "superficie_menuiseries_m2": payload["superficie_menuiseries_m2"],
        "shab_m2": payload["shab_m2"],
        "ratio_fac_shab": payload["ratio_fac_shab"],
        "counts": payload["counts"],
    }
