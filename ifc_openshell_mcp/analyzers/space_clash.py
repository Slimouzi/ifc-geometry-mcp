"""run_space_clash_audit — chevauchements entre pièces (IfcSpace).

Produit ``*_space_clash_findings.json`` consommé par
``preliminary._space_clash_findings``.

Classifications émises :
- ``doublon_piece``          : deux pièces quasi superposées au même étage.
- ``placard_double_modelise``: un placard (type rangement) inclus dans une
  autre pièce ET modélisé comme pièce dédiée.
- ``chevauchement_pieces``   : recouvrement partiel de deux pièces (même étage).
- ``chevauchement_vertical_entre_etages`` : recouvrement de pièces d'étages
  différents avec chevauchement d'altitude (pièce qui traverse la dalle).

Les pertes de surface (murs/poteaux) sont couvertes par ``compute_surface_loss``
et volontairement non émises ici.
"""

from __future__ import annotations

from collections import defaultdict
from itertools import combinations

from shapely.strtree import STRtree

from .spaces import CLOSET_TYPES, SpaceRecord, build_spaces


def _centroid_distance(a: SpaceRecord, b: SpaceRecord) -> float:
    ca, cb = a.footprint.centroid, b.footprint.centroid
    return round(ca.distance(cb), 2)


def _z_overlap(a: SpaceRecord, b: SpaceRecord) -> float:
    if None in (a.z_min, a.z_max, b.z_min, b.z_max):
        return 0.0
    return max(0.0, min(a.z_max, b.z_max) - max(a.z_min, b.z_min))


def run(
    model,
    *,
    overlap_min_ratio: float = 0.10,
    duplicate_ratio: float = 0.90,
    closet_inside_ratio: float = 0.80,
    vertical_min_overlap_m: float = 0.20,
    spaces: list[SpaceRecord] | None = None,
) -> dict:
    spaces = spaces if spaces is not None else build_spaces(model)
    geo = [s for s in spaces if s.footprint is not None and s.footprint.area > 1e-6]

    findings: list[dict] = []

    # --- Chevauchements même étage --------------------------------------- #
    by_storey: dict[str, list[SpaceRecord]] = defaultdict(list)
    for s in geo:
        by_storey[s.storey or "?"].append(s)

    for storey, group in by_storey.items():
        if len(group) < 2:
            continue
        geoms = [s.footprint for s in group]
        tree = STRtree(geoms)
        seen: set[tuple[int, int]] = set()
        for i, s in enumerate(group):
            for j in tree.query(s.footprint):
                if i == j:
                    continue
                key = (min(i, j), max(i, j))
                if key in seen:
                    continue
                seen.add(key)
                other = group[j]
                inter = s.footprint.intersection(other.footprint)
                if inter.is_empty or inter.area < 1e-6:
                    continue
                area_s, area_o = s.footprint.area, other.footprint.area
                small = min(area_s, area_o)
                ratio = inter.area / small if small else 0.0

                # doublon : recouvrement mutuel très fort
                mutual = inter.area / max(area_s, area_o) if max(area_s, area_o) else 0
                if mutual >= duplicate_ratio:
                    cls = "doublon_piece"
                elif ratio >= closet_inside_ratio and (
                    (area_s <= area_o and s.room_type in CLOSET_TYPES)
                    or (area_o < area_s and other.room_type in CLOSET_TYPES)
                ):
                    cls = "placard_double_modelise"
                elif ratio >= overlap_min_ratio:
                    cls = "chevauchement_pieces"
                else:
                    continue

                # La pièce "a" pointée = la plus petite (celle à corriger).
                a, b = (s, other) if area_s <= area_o else (other, s)
                findings.append(
                    {
                        "classification": cls,
                        "a": a.raw_label or a.name,
                        "a_guid": a.guid,
                        "b": b.raw_label or b.name,
                        "b_guid": b.guid,
                        "storey": storey,
                        "overlap_ratio": round(ratio, 2),
                        "overlap_area_m2": round(inter.area, 2),
                        "distance": _centroid_distance(a, b),
                    }
                )

    # --- Chevauchement vertical entre étages ----------------------------- #
    storeys = list(by_storey.keys())
    for si, sj in combinations(range(len(storeys)), 2):
        ga, gb = by_storey[storeys[si]], by_storey[storeys[sj]]
        if not ga or not gb:
            continue
        tree = STRtree([s.footprint for s in gb])
        for a in ga:
            for j in tree.query(a.footprint):
                b = gb[j]
                zov = _z_overlap(a, b)
                if zov < vertical_min_overlap_m:
                    continue
                inter = a.footprint.intersection(b.footprint)
                if inter.is_empty or inter.area < 0.5:  # >=0.5 m² d'emprise
                    continue
                findings.append(
                    {
                        "classification": "chevauchement_vertical_entre_etages",
                        "a": a.raw_label or a.name,
                        "a_guid": a.guid,
                        "b": b.raw_label or b.name,
                        "b_guid": b.guid,
                        "storeys": [storeys[si], storeys[sj]],
                        "z_overlap_m": round(zov, 2),
                        "overlap_area_m2": round(inter.area, 2),
                        "distance": round(zov, 2),
                    }
                )

    return {
        "params": {
            "overlap_min_ratio": overlap_min_ratio,
            "duplicate_ratio": duplicate_ratio,
            "closet_inside_ratio": closet_inside_ratio,
            "vertical_min_overlap_m": vertical_min_overlap_m,
        },
        "counts": {"n_spaces_geom": len(geo), "n_findings": len(findings)},
        "findings": findings,
    }
