"""check_space_boundaries — limites d'espaces manquantes.

Produit ``*_boundaries.json`` consommé par
``preliminary._boundaries_findings``.

- ``spaces_without_boundaries`` : pièces sans aucune ``IfcRelSpaceBoundary``.
- ``missing_boundaries`` : deux pièces adjacentes (même étage, contours proches)
  qui ne partagent aucun élément de séparation commun → limite manquante.
"""

from __future__ import annotations

from collections import defaultdict

from shapely.strtree import STRtree

from .spaces import SpaceRecord, build_spaces


def _boundary_elements(model) -> dict[str, set[str]]:
    """space_guid → ensemble des GlobalId d'éléments de séparation déclarés."""
    out: dict[str, set[str]] = defaultdict(set)
    for rel in model.by_type("IfcRelSpaceBoundary"):
        sp = getattr(rel, "RelatingSpace", None)
        el = getattr(rel, "RelatedBuildingElement", None)
        if sp is not None:
            if el is not None:
                out[sp.GlobalId].add(el.GlobalId)
            else:
                out[sp.GlobalId]  # présence, même sans élément
    return out


def run(
    model,
    *,
    adjacency_tol_m: float = 0.35,
    spaces: list[SpaceRecord] | None = None,
) -> dict:
    spaces = spaces if spaces is not None else build_spaces(model)
    bmap = _boundary_elements(model)
    has_boundary = set(bmap.keys())

    spaces_without = [
        {"guid": s.guid, "name": s.raw_label or s.name, "storey": s.storey}
        for s in spaces
        if s.guid not in has_boundary
    ]

    # Pièces avec boundaries, géométrie exploitable, groupées par étage.
    geo = [
        s
        for s in spaces
        if s.footprint is not None
        and s.footprint.area > 1e-6
        and s.guid in has_boundary
    ]
    by_storey: dict[str, list[SpaceRecord]] = defaultdict(list)
    for s in geo:
        by_storey[s.storey or "?"].append(s)

    missing: list[dict] = []
    for storey, group in by_storey.items():
        if len(group) < 2:
            continue
        geoms = [s.footprint for s in group]
        tree = STRtree(geoms)
        seen: set[tuple[int, int]] = set()
        for i, s in enumerate(group):
            probe = s.footprint.buffer(adjacency_tol_m)
            for j in tree.query(probe):
                if i == j:
                    continue
                key = (min(i, j), max(i, j))
                if key in seen:
                    continue
                seen.add(key)
                other = group[j]
                dist = s.footprint.distance(other.footprint)
                if dist > adjacency_tol_m:
                    continue
                # Partagent-elles un élément de séparation ?
                if bmap.get(s.guid, set()) & bmap.get(other.guid, set()):
                    continue
                missing.append(
                    {
                        "a_guid": s.guid,
                        "a_name": s.raw_label or s.name,
                        "b_guid": other.guid,
                        "b_name": other.raw_label or other.name,
                        "storey": storey,
                        "zones": s.zones,
                        "distance_m": round(dist, 3),
                    }
                )

    return {
        "params": {"adjacency_tol_m": adjacency_tol_m},
        "counts": {
            "n_spaces_without_boundaries": len(spaces_without),
            "n_missing_boundaries": len(missing),
        },
        "missing_boundaries": missing,
        "spaces_without_boundaries": spaces_without,
    }
