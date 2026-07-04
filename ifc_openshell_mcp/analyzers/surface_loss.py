"""compute_surface_loss — m² perdus par pièce (empiètement murs/poteaux).

Produit ``*_surface_loss.json`` consommé par
``preliminary._surface_loss_findings`` (1 entrée par pièce touchée).

Principe : quand le contour d'une pièce (IfcSpace) recouvre un élément
vertical porteur (mur, poteau), la surface d'intersection n'est pas réellement
exploitable → elle est comptée comme perte. On distingue murs et poteaux et on
compte le nombre d'éléments intrus.
"""

from __future__ import annotations

from dataclasses import dataclass

from shapely.strtree import STRtree

from .. import ifc_utils
from .spaces import SpaceRecord, build_spaces

_WALL_TYPES = ("IfcWall", "IfcWallStandardCase")
_COLUMN_TYPES = ("IfcColumn",)

# Seuils de sévérité métier (perte en % de la surface brute de la pièce).
_SEV_CRITIQUE = 5.0
_SEV_MAJEUR = 2.0
# Plancher de signalement : en deçà, on ignore (bruit géométrique).
_MIN_LOSS_M2 = 0.05
_MIN_LOSS_PCT = 1.0


@dataclass
class _Intruder:
    footprint: object
    z_min: float
    z_max: float


def _collect(model, ifc_types) -> list[_Intruder]:
    items: list[_Intruder] = []
    for t in ifc_types:
        for el in model.by_type(t):
            g = ifc_utils.element_geometry(el)
            if g.footprint is not None and g.footprint.area > 1e-6:
                items.append(_Intruder(g.footprint, g.z_min, g.z_max))
    return items


def _z_overlaps(a_min, a_max, b_min, b_max) -> bool:
    if None in (a_min, a_max, b_min, b_max):
        return True  # à défaut d'altitude, on ne filtre pas
    return a_min < b_max and b_min < a_max


def _severity(pct: float) -> str:
    if pct >= _SEV_CRITIQUE:
        return "Critique"
    if pct >= _SEV_MAJEUR:
        return "Majeur"
    return "Mineur"


def _sum_loss(space: SpaceRecord, intruders: list[_Intruder], tree: STRtree):
    total = 0.0
    n = 0
    for idx in tree.query(space.footprint):
        intr = intruders[idx]
        if not _z_overlaps(space.z_min, space.z_max, intr.z_min, intr.z_max):
            continue
        inter = space.footprint.intersection(intr.footprint)
        if inter.is_empty or inter.area < 1e-4:
            continue
        total += inter.area
        n += 1
    return total, n


def _declared_deduction(space: SpaceRecord) -> dict:
    """Déduction déclarée = GrossFloorArea - NetFloorArea si disponible."""
    try:
        import ifcopenshell.util.element as ue

        qtos = ue.get_psets(space.ifc, qtos_only=True) or {}
    except Exception:
        return {}
    gross = net = None
    for _, props in qtos.items():
        gross = gross if gross is not None else props.get("GrossFloorArea")
        net = net if net is not None else props.get("NetFloorArea")
    if isinstance(gross, (int, float)) and isinstance(net, (int, float)):
        return {"total_deduit_m2": round(float(gross) - float(net), 2)}
    return {}


def run(
    model,
    *,
    spaces: list[SpaceRecord] | None = None,
) -> dict:
    spaces = spaces if spaces is not None else build_spaces(model)
    walls = _collect(model, _WALL_TYPES)
    columns = _collect(model, _COLUMN_TYPES)
    wall_tree = STRtree([w.footprint for w in walls]) if walls else None
    col_tree = STRtree([c.footprint for c in columns]) if columns else None

    losses: list[dict] = []
    for sp in spaces:
        if sp.footprint is None or sp.footprint.area <= 1e-6:
            continue
        loss_walls = n_walls = 0.0
        loss_cols = n_cols = 0.0
        if wall_tree is not None:
            loss_walls, n_walls = _sum_loss(sp, walls, wall_tree)
        if col_tree is not None:
            loss_cols, n_cols = _sum_loss(sp, columns, col_tree)

        total = loss_walls + loss_cols
        base = sp.footprint.area  # surface brute (contour dessiné)
        pct = (total / base * 100.0) if base else 0.0
        if total < _MIN_LOSS_M2 or pct < _MIN_LOSS_PCT:
            continue

        losses.append(
            {
                "guid": sp.guid,
                "name": sp.name,
                "long_name": sp.long_name,
                "storey": sp.storey,
                "zones": sp.zones,
                "perte_totale_m2": round(total, 2),
                "perte_pct": round(pct, 1),
                "perte_murs_m2": round(loss_walls, 2),
                "perte_poteaux_m2": round(loss_cols, 2),
                "n_intrus": int(n_walls + n_cols),
                "severity": _severity(pct),
                "deduction_declaree": _declared_deduction(sp),
            }
        )

    losses.sort(key=lambda r: r["perte_totale_m2"], reverse=True)
    return {
        "params": {
            "seuil_critique_pct": _SEV_CRITIQUE,
            "seuil_majeur_pct": _SEV_MAJEUR,
            "plancher_m2": _MIN_LOSS_M2,
        },
        "counts": {
            "n_walls": len(walls),
            "n_columns": len(columns),
            "n_losses": len(losses),
        },
        "losses": losses,
    }
