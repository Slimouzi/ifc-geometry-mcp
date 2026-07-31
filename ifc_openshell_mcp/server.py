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
from pathlib import Path
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
from .enrichers import base_quantities
from .safe_paths import safe_input_path, safe_output_path

load_dotenv()
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("ifc_openshell_mcp")

mcp = FastMCP("ifc-geometry")

_IFC_EXT = {".ifc", ".ifczip", ".ifcxml"}
# Extensions acceptées en **écriture** : ``ifcopenshell.file.write`` ne sait PAS
# produire du ``.ifcxml`` (NotImplementedError). Distinct de ``_IFC_EXT`` (lecture).
_OUTPUT_IFC_EXT = {".ifc", ".ifczip"}


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


# --------------------------------------------------------------------------- #
#  7. Complétion des BaseQuantities manquantes (écrit une COPIE, jamais in-place)
# --------------------------------------------------------------------------- #
_DEFAULT_BQ_SUFFIX = ".with_base_quantities.ifc"


@mcp.tool()
def complete_ifc_base_quantities(
    ifc_path: str,
    output_ifc_path: str | None = None,
    classes: list[str] | None = None,
    overwrite_existing: bool = False,
    dry_run: bool = True,
    confirm: bool = False,
    precision: int = 3,
) -> dict[str, Any]:
    """Complète les ``Qto_*BaseQuantities`` manquantes dans une **copie** de l'IFC.

    Calcule via IFC OpenShell les quantités déjà exploitées, de façon fiable, par
    le MCP audit-bim-i3f, et les ajoute/met à jour dans un **nouveau** fichier IFC
    sous ``AUDIT_OUTPUT_DIR``. **Le fichier source n'est jamais modifié.**

    Quantités : ``IfcSpace``/``NetFloorArea``, ``IfcSlab``/``NetArea``,
    ``IfcWall``/``NetSideArea``, ``IfcWindow``/``IfcDoor``/``Width``+``Height``.
    Les ``Gross*`` et l'aire des menuiseries ne sont **pas** inventées (marquées
    *skipped*, cf. ``warnings``). Une géométrie illisible → quantité *skipped*.

    Sécurité :

    - ``dry_run=True`` (défaut) : **aucun** fichier écrit, on renvoie le plan.
    - ``dry_run=False`` exige ``confirm=True`` (sinon ``status="failed"``).
    - Sortie sandboxée sous ``AUDIT_OUTPUT_DIR`` ; jamais d'écrasement de la source.

    Args:
        ifc_path: IFC source (sandbox ``AUDIT_INPUT_DIR``, lecture seule).
        output_ifc_path: nom du fichier de sortie (sandbox ``AUDIT_OUTPUT_DIR``,
            aplati au basename). Défaut : ``<source>.with_base_quantities.ifc``.
        classes: sous-ensemble de classes à traiter (défaut : toutes les
            supportées — ``IfcSpace``/``IfcSlab``/``IfcWall``/``IfcWindow``/``IfcDoor``).
        overwrite_existing: si ``True``, écrase une BaseQuantity déjà présente ;
            sinon elle est conservée (*skipped* ``exists_not_overwritten``).
        dry_run: si ``True`` (défaut), n'écrit rien.
        confirm: obligatoire (``True``) pour écrire quand ``dry_run=False``.
        precision: nombre de décimales des valeurs écrites (défaut 3).

    Returns:
        ``{status, source_ifc, output_ifc, summary, changes, warnings}`` — ``status``
        ∈ {``dry_run``, ``written``, ``failed``} ; ``changes`` audite chaque valeur.
    """
    model, safe = _load(ifc_path)

    # Nom de sortie : fourni (aplati au basename) ou défaut. Extension exigée pour
    # l'**écriture** (``_OUTPUT_IFC_EXT`` : .ifcxml refusé — non écrit par ifcopenshell).
    out_name = (
        output_ifc_path.strip() if output_ifc_path and output_ifc_path.strip() else None
    )
    if out_name is None:
        out_name = f"{safe.stem}{_DEFAULT_BQ_SUFFIX}"
    base_out = Path(out_name).name

    plan = base_quantities.plan_completion(
        model,
        classes=classes,
        overwrite_existing=overwrite_existing,
        precision=precision,
    )

    def _failed(error: str) -> dict[str, Any]:
        return {
            "status": "failed",
            "source_ifc": str(safe),
            "output_ifc": None,
            "summary": plan["summary"],
            "changes": plan["changes"],
            "warnings": plan["warnings"],
            "error": error,
        }

    # P2 — extension de sortie supportée par l'écriture IFC (jamais .ifcxml).
    if Path(base_out).suffix.lower() not in _OUTPUT_IFC_EXT:
        return _failed(
            f"Extension de sortie non supportée par l'écriture IFC "
            f"{sorted(_OUTPUT_IFC_EXT)} : {base_out!r}"
        )

    # P1 — résolution défensive du chemin de sortie : ``safe_output_path`` peut
    # lever (FileExistsError / UnsafePathError⊂ValueError). On résout avec
    # ``overwrite=True`` (résolution seule, sans lever sur existence) et on
    # retourne un payload structuré en cas d'erreur, jamais d'exception brute.
    try:
        target = safe_output_path(base_out, overwrite=True)
    except (FileExistsError, ValueError) as exc:
        return _failed(str(exc))

    # P1 — collision avec la source, atteinte de façon **fiable** même quand
    # ``AUDIT_INPUT_DIR == AUDIT_OUTPUT_DIR`` (avant toute écriture).
    if target.resolve() == safe.resolve():
        return _failed(
            "Le chemin de sortie coïncide avec la source — écriture refusée."
        )

    # ── Mode simulation : rien n'est écrit ──────────────────────────────── #
    if dry_run:
        return {
            "status": "dry_run",
            "source_ifc": str(safe),
            "output_ifc": str(target),  # cible envisagée, NON écrite
            "summary": plan["summary"],
            "changes": plan["changes"],
            "warnings": plan["warnings"],
        }

    # ── Écriture réelle : garde-fous ────────────────────────────────────── #
    if not confirm:
        return _failed("Écriture demandée (dry_run=False) sans confirm=True — refusée.")
    if (
        target.exists()
    ):  # ne jamais écraser une sortie existante (≠ source, déjà exclue)
        return _failed(
            f"Le fichier de sortie existe déjà : {target.name} — ne pas écraser."
        )

    base_quantities.apply_completion(model, plan["changes"])
    model.write(str(target))
    return {
        "status": "written",
        "source_ifc": str(safe),
        "output_ifc": str(target),
        "summary": plan["summary"],
        "changes": plan["changes"],
        "warnings": plan["warnings"],
    }
