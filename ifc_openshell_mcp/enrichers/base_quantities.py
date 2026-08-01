"""Complète les ``Qto_*BaseQuantities`` manquantes à partir de la géométrie.

Ce module **ne touche jamais au fichier source** : il calcule un *plan* de
modifications (:func:`plan_completion`, pur, sans mutation) puis applique ce plan
sur un modèle **en mémoire** (:func:`apply_completion`) que l'appelant écrit dans
une **copie** sous ``AUDIT_OUTPUT_DIR``.

Quantités traitées (uniquement celles déjà exploitées, de façon fiable, par le
MCP audit-bim-i3f) :

======================  ==========================  =====================================
Classe IFC              Qto                         Quantité(s) calculée(s)
======================  ==========================  =====================================
``IfcSpace``            ``Qto_SpaceBaseQuantities``  ``NetFloorArea`` (empreinte géom.)
``IfcSlab``             ``Qto_SlabBaseQuantities``   ``NetArea`` (empreinte géom.)
``IfcWall``             ``Qto_WallBaseQuantities``   ``NetSideArea`` (aire d'un parement)
``IfcWindow``           ``Qto_WindowBaseQuantities`` ``Width`` / ``Height`` (bbox)
``IfcDoor``             ``Qto_DoorBaseQuantities``   ``Width`` / ``Height`` (bbox)
======================  ==========================  =====================================

**Décisions « ne jamais inventer »** :

- Les quantités ``Gross*`` (GrossFloorArea / GrossSideArea / GrossArea) **ne sont
  pas calculées** : elles ne se dérivent pas de façon stable d'une géométrie déjà
  *découpée* (ouvertures soustraites) — marquées *skipped* (note globale).
- Pour les menuiseries, on écrit ``Width`` / ``Height`` (noms standard des
  ``Qto_*BaseQuantities``, lus par le MCP qui accepte ``Width``/``OverallWidth``).
  L'**aire** n'est **pas** écrite : la bbox donne une aire brute non fiable, et le
  MCP dérive lui-même la surface via ``Width × Height``.
- Toute géométrie illisible → quantité *skipped* (jamais de valeur inventée).

Unités : la géométrie ``ifcopenshell.geom`` est en **mètres** (modèles I3F en SI
LENGTHUNIT) → aires en m², longueurs en m, écrites telles quelles dans les Qto.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from typing import Any

import ifcopenshell
import ifcopenshell.api.pset
import ifcopenshell.util.element as ue
from bim_core.contracts import SCHEMA_COMPUTED_BASE_QUANTITIES_V1, SOURCE_COMPUTED

from .. import ifc_utils
from ..contracts import contract_source, utc_now_iso

logger = logging.getLogger("ifc_openshell_mcp.enrichers.base_quantities")

# Classes supportées → nom du Qto BaseQuantities standard.
_QTO_NAME: dict[str, str] = {
    "IfcSpace": "Qto_SpaceBaseQuantities",
    "IfcSlab": "Qto_SlabBaseQuantities",
    "IfcWall": "Qto_WallBaseQuantities",
    "IfcWindow": "Qto_WindowBaseQuantities",
    "IfcDoor": "Qto_DoorBaseQuantities",
}
SUPPORTED_CLASSES: tuple[str, ...] = tuple(_QTO_NAME)

# Note globale sur les quantités volontairement non calculées (design).
_DESIGN_SKIP_NOTE = (
    "Quantités « Gross* » (GrossFloorArea/GrossSideArea/GrossArea) et « Area » "
    "menuiseries non calculées : non dérivables de façon fiable depuis la "
    "géométrie découpée (ne pas inventer). Le MCP dérive la surface menuiserie "
    "de Width × Height."
)


# --------------------------------------------------------------------------- #
#  Calcul des quantités (pur, best-effort, jamais d'invention)
# --------------------------------------------------------------------------- #
def _area_result(
    value: float | None, precision: int
) -> tuple[float | None, str | None, str | None]:
    if isinstance(value, (int, float)) and value > 0:
        return round(float(value), precision), "ifcopenshell_geometry", None
    return None, None, "geometry_unavailable"


def _len_result(
    value: float | None, precision: int
) -> tuple[float | None, str | None, str | None]:
    if isinstance(value, (int, float)) and value > 0:
        return round(float(value), precision), "ifcopenshell_bbox", None
    return None, None, "geometry_unavailable"


def _compute_quantities(
    element, ifc_class: str, precision: int
) -> dict[str, tuple[float | None, str | None, str | None]]:
    """Quantités **tentées** pour un élément : ``{qty: (value|None, method, reason)}``.

    Renvoie ``{}`` si la géométrie de l'élément est globalement illisible (l'appelant
    émet alors un warning et passe au suivant, sans faire échouer le run).
    """
    if ifc_class == "IfcSpace":
        geo = ifc_utils.element_geometry(element)
        return {"NetFloorArea": _area_result(geo.recalc_area_m2, precision)}
    if ifc_class == "IfcSlab":
        geo = ifc_utils.element_geometry(element)
        return {"NetArea": _area_result(geo.recalc_area_m2, precision)}
    if ifc_class == "IfcWall":
        return {
            "NetSideArea": _area_result(
                ifc_utils.vertical_face_area(element), precision
            )
        }
    if ifc_class in ("IfcWindow", "IfcDoor"):
        wh = ifc_utils.bbox_width_height(element)
        if wh is None:
            return {
                "Width": (None, None, "geometry_unavailable"),
                "Height": (None, None, "geometry_unavailable"),
            }
        width, height = wh
        return {
            "Width": _len_result(width, precision),
            "Height": _len_result(height, precision),
        }
    return {}


# --------------------------------------------------------------------------- #
#  Lecture de l'existant + traversée des Qto
# --------------------------------------------------------------------------- #
def _existing_value(element, qto_name: str, qty_name: str) -> float | None:
    """Valeur numérique existante d'une quantité (via get_psets, qtos_only)."""
    try:
        qtos = ue.get_psets(element, qtos_only=True) or {}
    except Exception:  # noqa: BLE001 — élément atypique : traité comme « absent »
        return None
    props = qtos.get(qto_name) or {}
    val = props.get(qty_name)
    if isinstance(val, (int, float)) and not isinstance(val, bool):
        return float(val)
    return None


def _find_qto(element, qto_name: str):
    """Retourne l'``IfcElementQuantity`` nommé ``qto_name`` déjà porté par
    l'élément, ou ``None`` (pour éditer sans dupliquer)."""
    for rel in getattr(element, "IsDefinedBy", None) or []:
        if not rel.is_a("IfcRelDefinesByProperties"):
            continue
        pdef = rel.RelatingPropertyDefinition
        if pdef and pdef.is_a("IfcElementQuantity") and pdef.Name == qto_name:
            return pdef
    return None


# --------------------------------------------------------------------------- #
#  Plan (pur) et application (mutation en mémoire)
# --------------------------------------------------------------------------- #
def _select_classes(classes: list[str] | None, warnings: list[str]) -> list[str]:
    if not classes:
        return list(SUPPORTED_CLASSES)
    for c in classes:
        if c not in _QTO_NAME:
            warnings.append(f"classe non supportée ignorée : {c}")
    return [c for c in SUPPORTED_CLASSES if c in classes]


def plan_completion(
    model,
    *,
    classes: list[str] | None = None,
    overwrite_existing: bool = False,
    precision: int = 3,
) -> dict[str, Any]:
    """Calcule le **plan** de complétion (aucune mutation, aucun fichier écrit).

    Renvoie ``{summary, changes, warnings}``. Chaque entrée de ``changes`` porte
    ``action`` ∈ {``created``, ``updated``, ``skipped``} + ``old_value`` /
    ``new_value`` / ``method`` (+ ``reason`` si *skipped*), pour un audit valeur
    par valeur.
    """
    warnings: list[str] = []
    selected = _select_classes(classes, warnings)
    warnings.append(_DESIGN_SKIP_NOTE)

    changes: list[dict[str, Any]] = []
    scanned: set[str] = set()
    created = updated = skipped = 0

    for ifc_class in selected:
        qto_name = _QTO_NAME[ifc_class]
        for element in model.by_type(ifc_class):
            scanned.add(element.GlobalId)
            try:
                results = _compute_quantities(element, ifc_class, precision)
            except Exception as exc:  # noqa: BLE001 — un élément ne fait pas échouer le run
                logger.warning(
                    "géométrie KO %s %s : %s", ifc_class, element.GlobalId, exc
                )
                warnings.append(f"géométrie illisible : {ifc_class} {element.GlobalId}")
                continue
            if not results:
                continue

            for qty_name, (value, method, skip_reason) in results.items():
                old = _existing_value(element, qto_name, qty_name)
                entry: dict[str, Any] = {
                    "global_id": element.GlobalId,
                    "ifc_class": ifc_class,
                    "name": getattr(element, "Name", None),
                    "qto": qto_name,
                    "quantity": qty_name,
                    "old_value": old,
                    "new_value": None,
                    "method": method,
                    "action": "skipped",
                }
                if value is None:
                    entry["reason"] = skip_reason or "not_computable"
                    skipped += 1
                elif old is not None and not overwrite_existing:
                    entry["reason"] = "exists_not_overwritten"
                    skipped += 1
                else:
                    entry["new_value"] = value
                    entry["action"] = "updated" if old is not None else "created"
                    if old is not None:
                        updated += 1
                    else:
                        created += 1
                changes.append(entry)

    summary = {
        "elements_scanned": len(scanned),
        "quantities_created": created,
        "quantities_updated": updated,
        "quantities_skipped": skipped,
    }
    return {"summary": summary, "changes": changes, "warnings": warnings}


def apply_completion(model, changes: list[dict[str, Any]]) -> None:
    """Applique un plan sur le modèle **en mémoire** (mutations ciblées).

    Ne modifie que les entrées ``created`` / ``updated`` ; regroupe par
    (élément, Qto) pour n'ouvrir/éditer chaque Qto qu'une fois. Le modèle est
    ensuite écrit par l'appelant dans une **copie** (jamais la source).
    """
    grouped: dict[tuple[str, str], dict[str, float]] = defaultdict(dict)
    for c in changes:
        if c["action"] in ("created", "updated") and c["new_value"] is not None:
            grouped[(c["global_id"], c["qto"])][c["quantity"]] = c["new_value"]

    for (guid, qto_name), props in grouped.items():
        element = model.by_guid(guid)
        if element is None:
            continue
        qto = _find_qto(element, qto_name)
        if qto is None:
            qto = ifcopenshell.api.pset.add_qto(model, product=element, name=qto_name)
        ifcopenshell.api.pset.edit_qto(model, qto=qto, properties=props)


# --------------------------------------------------------------------------- #
#  Export des quantités calculées (JSON keyé GlobalId) — flux « fusion »
# --------------------------------------------------------------------------- #
#
# Contrat consommé par audit-bim-i3f pour une **fusion gap-only** dans le
# snapshot BIMData (clé de jointure ``BimObject.uuid == global_id``). Ce flux
# **n'écrit rien dans l'IFC** : il ne fait qu'exporter les valeurs calculées.

# Schéma et provenance viennent du contrat partagé (bim-core) — jamais
# redéclarés localement, sous peine de divergence silencieuse.
EXPORT_SCHEMA = SCHEMA_COMPUTED_BASE_QUANTITIES_V1

# Scope minimal DIEPPE (cf. cahier des charges) : espaces, dalles, menuiseries.
# Les murs d'enveloppe (NetSideArea) restent en **phase 2** — non exportés par
# défaut car la sélection « enveloppe » demande une heuristique dédiée.
DIEPPE_EXPORT_CLASSES: tuple[str, ...] = ("IfcSpace", "IfcSlab", "IfcWindow", "IfcDoor")

# Unité par quantité (modèles I3F en SI mètre → aires m², longueurs m).
_QTY_UNIT: dict[str, str] = {
    "NetFloorArea": "m2",
    "NetArea": "m2",
    "NetSideArea": "m2",
    "Width": "m",
    "Height": "m",
}


def export_computed_quantities(
    model,
    *,
    classes: list[str] | None = None,
    precision: int = 3,
    ifc_file: str | None = None,
) -> dict[str, Any]:
    """Calcule les BaseQuantities géométriques et les renvoie **exportables** (JSON),
    **sans écrire dans l'IFC**.

    Chaque quantité porte : ``global_id``, ``ifc_class``, ``qto``, ``quantity``,
    ``value``, ``unit``, ``method``, ``status`` (``computed`` | ``skipped``),
    ``source`` (+ ``reason`` si *skipped*). Le *gap-only* (ne combler que les
    vides) est appliqué **en aval** par le consommateur (audit-bim), contre le
    snapshot BIMData faisant foi — ici on calcule pour **tous** les éléments.

    ``classes`` défaut = scope minimal DIEPPE (``DIEPPE_EXPORT_CLASSES``) : les
    murs d'enveloppe restent en phase 2.
    """
    warnings: list[str] = []
    if classes is None:
        selected: list[str] = list(DIEPPE_EXPORT_CLASSES)
    else:
        selected = _select_classes(classes, warnings)

    quantities: list[dict[str, Any]] = []
    scanned: set[str] = set()
    n_computed = n_failed = 0

    for ifc_class in selected:
        qto_name = _QTO_NAME[ifc_class]
        for element in model.by_type(ifc_class):
            gid = element.GlobalId
            scanned.add(gid)
            try:
                results = _compute_quantities(element, ifc_class, precision)
            except Exception as exc:  # noqa: BLE001 — un élément ne fait pas échouer l'export
                logger.warning("géométrie KO %s %s : %s", ifc_class, gid, exc)
                warnings.append(f"géométrie illisible : {ifc_class} {gid}")
                n_failed += 1
                continue
            for qty_name, (value, method, skip_reason) in results.items():
                computed = value is not None
                entry: dict[str, Any] = {
                    "global_id": gid,
                    "ifc_class": ifc_class,
                    "qto": qto_name,
                    "quantity": qty_name,
                    "value": value,
                    "unit": _QTY_UNIT.get(qty_name),
                    "method": method,
                    "status": "computed" if computed else "skipped",
                    "source": SOURCE_COMPUTED,
                }
                if not computed:
                    entry["reason"] = skip_reason or "not_computable"
                    n_failed += 1
                else:
                    n_computed += 1
                quantities.append(entry)

    return {
        "schema": EXPORT_SCHEMA,
        "source": contract_source("export_computed_base_quantities", ifc_file or ""),
        "created_at": utc_now_iso(),
        "quantities": quantities,
        "coverage": {
            "n_elements": len(scanned),
            "n_computed": n_computed,
            "n_failed": n_failed,
        },
        "warnings": warnings,
    }
