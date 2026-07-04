"""extract_space_inventory — inventaire pièces + zones + nommage + fraîcheur.

Produit le JSON ``*_space_inventory.json`` consommé par
``audit_bim.audit.rules.preliminary._inventory_findings``.

Flags pièce : ``piece_trop_petite``, ``ecart_surface``,
``sans_surface_declaree``, ``sans_zone``, ``sans_etage``.
Flags zone  : ``typologie_incoherente``, ``zone_discontinue``,
``zone_sans_piece``, ``doublon_nom_zone``, ``duplex_possible``.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from datetime import date, datetime

from .. import ifc_utils
from .spaces import HABITABLE_TYPES, SpaceRecord, build_spaces

# Types non contigus tolérés (n'entraînent pas de discontinuité de zone).
_ANNEX_TYPES = {"cellier", "cave", "technique", "exterieur"}


def _round(v, n=2):
    return round(v, n) if isinstance(v, (int, float)) else v


def _space_entry(
    sp: SpaceRecord, min_area_threshold_m2: float, area_tol_pct: float
) -> dict:
    flags: list[str] = []
    if sp.storey is None:
        flags.append("sans_etage")
    if not sp.zones:
        flags.append("sans_zone")
    if sp.area_declared_m2 is None:
        flags.append("sans_surface_declaree")

    delta = sp.area_delta_pct
    if delta is not None and abs(delta) > area_tol_pct:
        flags.append("ecart_surface")

    # Seuil de surface minimale : seulement pour les pièces habitables.
    area = sp.best_area
    if (
        sp.room_type in HABITABLE_TYPES
        and area is not None
        and area < min_area_threshold_m2
    ):
        flags.append("piece_trop_petite")

    return {
        "guid": sp.guid,
        "name": sp.name,
        "long_name": sp.long_name,
        "storey": sp.storey,
        "zones": sp.zones,
        "room_type": sp.room_type,
        "flags": flags,
        "min_area_threshold_m2": min_area_threshold_m2,
        "area_declared_m2": _round(sp.area_declared_m2),
        "area_recalc_m2": _round(sp.area_recalc_m2),
        "area_delta_pct": _round(delta, 1),
    }


def _declared_typologie(zone_ifc) -> str | None:
    """Typologie déclarée (T1..T5) trouvée dans le nom ou les Psets de la zone."""
    name = getattr(zone_ifc, "Name", "") or ""
    m = re.search(r"\bT\s?([0-9])\b", name, re.IGNORECASE)
    if m:
        return f"T{m.group(1)}"
    try:
        import ifcopenshell.util.element as ue

        for _, props in (ue.get_psets(zone_ifc) or {}).items():
            for k, v in props.items():
                if "typolog" in k.lower() and isinstance(v, str):
                    mm = re.search(r"T\s?([0-9])", v, re.IGNORECASE)
                    if mm:
                        return f"T{mm.group(1)}"
    except Exception:
        pass
    return None


def _zone_entries(model, spaces: list[SpaceRecord]) -> list[dict]:
    by_guid = {s.guid: s for s in spaces}
    _, zone_to_spaces = ifc_utils.zone_map(model)

    zones = list(model.by_type("IfcZone"))
    name_counts = Counter((z.Name or "").strip().lower() for z in zones if z.Name)

    entries: list[dict] = []
    for z in zones:
        members = [
            by_guid[g] for g in zone_to_spaces.get(z.GlobalId, []) if g in by_guid
        ]
        flags: list[str] = []

        if not members:
            flags.append("zone_sans_piece")

        # Typologie calculée = nb de chambres + 1 (T(n+1)).
        n_chambres = sum(1 for m in members if m.room_type == "chambre")
        typo_calc = f"T{n_chambres + 1}" if members else None
        typo_decl = _declared_typologie(z)
        if typo_decl and typo_calc and typo_decl != typo_calc:
            flags.append("typologie_incoherente")

        # Continuité : étages distincts couverts par les pièces principales
        # (hors annexes cellier/cave/technique/extérieur).
        storeys = {
            m.storey for m in members if m.storey and m.room_type not in _ANNEX_TYPES
        }
        if len(storeys) >= 3:
            flags.append("zone_discontinue")
        elif len(storeys) == 2:
            flags.append("duplex_possible")

        # Doublon de nom (légitime seulement en duplex).
        if z.Name and name_counts[z.Name.strip().lower()] > 1:
            flags.append("doublon_nom_zone")

        entries.append(
            {
                "guid": z.GlobalId,
                "name": z.Name,
                "flags": flags,
                "n_pieces": len(members),
                "n_chambres": n_chambres,
                "typologie_declaree": typo_decl,
                "typologie_calculee": typo_calc,
            }
        )
    return entries


def _naming_issues(spaces: list[SpaceRecord]) -> list[dict]:
    """Un type de pièce porté par plusieurs libellés distincts → incohérence."""
    variants: dict[str, set[str]] = defaultdict(set)
    for s in spaces:
        if s.raw_label and s.room_type not in {"inconnu", "autre"}:
            variants[s.room_type].add(s.raw_label.strip())
    issues = []
    for room_type, labels in sorted(variants.items()):
        if len(labels) > 1:
            issues.append({"room_type": room_type, "variants": sorted(labels)})
    return issues


def _export_date(model) -> str | None:
    """Date d'export : en-tête FILE_NAME (timestamp) ou OwnerHistory."""
    # 1) En-tête STEP FILE_NAME[1] = timestamp ISO.
    try:
        ts = model.wrapped_data.header.file_name.time_stamp
        if ts:
            return ts[:10]
    except Exception:
        pass
    # 2) OwnerHistory.CreationDate (epoch) du projet.
    try:
        for oh in model.by_type("IfcOwnerHistory"):
            if oh.CreationDate:
                return datetime.utcfromtimestamp(oh.CreationDate).date().isoformat()
    except Exception:
        pass
    return None


def _dates(model, stale_after_days: int, analysis_date: date) -> dict:
    export_iso = _export_date(model)
    out: dict = {
        "export_date": export_iso,
        "analysis_date": analysis_date.isoformat(),
        "stale_after_days": stale_after_days,
        "age_days": None,
        "stale": False,
    }
    if export_iso:
        try:
            exp = datetime.fromisoformat(export_iso).date()
            age = (analysis_date - exp).days
            out["age_days"] = age
            out["stale"] = age > stale_after_days
        except Exception:
            pass
    return out


def run(
    model,
    file_name: str,
    *,
    min_area_threshold_m2: float = 9.0,
    area_tol_pct: float = 5.0,
    stale_after_days: int = 90,
    analysis_date: date | None = None,
) -> dict:
    spaces = build_spaces(model)
    analysis_date = analysis_date or date.today()
    return {
        "file": file_name,
        "params": {
            "min_area_threshold_m2": min_area_threshold_m2,
            "area_tol_pct": area_tol_pct,
            "stale_after_days": stale_after_days,
        },
        "counts": {
            "n_spaces": len(spaces),
            "n_zones": len(model.by_type("IfcZone")),
        },
        "spaces": [
            _space_entry(s, min_area_threshold_m2, area_tol_pct) for s in spaces
        ],
        "zones": _zone_entries(model, spaces),
        "naming_issues": _naming_issues(spaces),
        "dates": _dates(model, stale_after_days, analysis_date),
    }
