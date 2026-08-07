"""extract_spatial_evidence — produit ``spatial_evidence/v1``.

Balaye une maquette et mesure, pour chaque élément retenu : boîte englobante,
empreinte, centroïde ; pour chaque ``IfcSpace`` en plus : surfaces, deux
approximations de largeur, hauteur libre, objets contenus et encombrement.

Aucun seuil, aucun verdict. Le contrat est décrit dans
:mod:`bim_core.contracts.spatial_evidence` — ce module se contente de le
remplir, et de compter ce qu'il n'a pas réussi à mesurer.

Sélection par **liste d'exclusion**, jamais par liste blanche : une classe IFC
oubliée dans une liste blanche disparaît sans bruit du document, et le
consommateur conclut à une absence. Ici tout ``IfcElement`` est mesuré sauf les
classes explicitement écartées, et ces classes sont nommées dans ``selection``.
"""

from __future__ import annotations

from collections import Counter

from shapely.geometry import Point, Polygon
from shapely.ops import unary_union
from shapely.strtree import STRtree

from bim_core.contracts import SCHEMA_SPATIAL_EVIDENCE_V1

from .. import ifc_utils
from ..contracts import contract_source, utc_now_iso
from .spaces import SpaceRecord, build_spaces, normalize_room_type

#: Classes écartées par défaut, et pourquoi.
#:
#: - ``IfcOpeningElement`` : un vide, pas un objet. Sa boîte englobante décrirait
#:   le trou d'une porte comme s'il s'agissait d'un meuble.
#: - ``IfcBuildingElementPart`` : les couches d'un composant (60 % des produits
#:   de la maquette de référence). Les mesurer compterait sept fois un mur.
#: - ``IfcVirtualElement`` : séparateur d'espaces sans matière.
#: - ``IfcAnnotation`` / ``IfcGrid`` : repères de dessin.
DEFAULT_EXCLUDED_CLASSES = (
    "IfcOpeningElement",
    "IfcBuildingElementPart",
    "IfcVirtualElement",
    "IfcAnnotation",
    "IfcGrid",
)

#: Classes traitées comme menuiseries — seules à porter ``opening_width_m``.
OPENING_CLASSES = ("IfcDoor", "IfcWindow")

#: Part de l'empreinte d'un objet qui doit tomber dans un espace pour qu'on
#: retienne un rattachement par recouvrement, à défaut de centroïde.
MIN_OVERLAP_RATIO = 0.50

#: Recouvrement vertical minimal (m) entre un objet et un espace. Sans lui, un
#: luminaire du 3e serait rattaché au séjour du rez-de-chaussée : en projection
#: XY les deux se superposent parfaitement.
MIN_Z_OVERLAP_M = 0.05


def _bbox_dict(bbox: tuple[float, ...] | None) -> dict[str, float] | None:
    if bbox is None:
        return None
    keys = ("x_min", "x_max", "y_min", "y_max", "z_min", "z_max")
    return {k: round(v, 4) for k, v in zip(keys, bbox, strict=True)}


def _min_rect_width(polygon: Polygon | None) -> float | None:
    """Petit côté du rectangle englobant **orienté**.

    Exact sur une pièce convexe. Sur une pièce en L, le rectangle englobe les
    deux branches et la valeur ne correspond à aucun passage.

    L'orientation n'est pas un détail : un rectangle aligné sur les axes du
    projet rendrait la diagonale d'un couloir à 45°, et un passage de 1,20 m
    passerait pour large de 8 m.
    """
    if polygon is None or polygon.is_empty or polygon.area <= 0:
        return None
    try:
        rect = polygon.minimum_rotated_rectangle
        coords = list(rect.exterior.coords)[:4]
    except (AttributeError, ValueError):
        return None
    if len(coords) < 4:
        return None
    sides = [Point(coords[i]).distance(Point(coords[(i + 1) % 4])) for i in range(4)]
    return round(min(sides), 3)


def _inscribed_diameter(
    polygon: Polygon | None, *, tolerance: float = 0.01
) -> float | None:
    """Diamètre du plus grand cercle inscrit, par dichotomie sur l'érosion.

    Ce que la mesure dit : la pièce atteint ce diamètre **quelque part**. Ce
    qu'elle ne dit pas : qu'elle l'atteint partout. Sur un L à branches de
    2,00 m elle rend 2,34, parce que le plus grand cercle se loge dans l'angle
    et déborde en diagonale dans les deux branches (test dédié).

    Elle vaut donc la largeur uniquement sur une pièce convexe. Trancher un
    contrôle « largeur de circulation ≥ X » sur une forme quelconque demande un
    axe médian, hors de ce lot.
    """
    if polygon is None or polygon.is_empty or polygon.area <= 0:
        return None
    x_min, y_min, x_max, y_max = polygon.bounds
    lo, hi = 0.0, min(x_max - x_min, y_max - y_min) / 2.0
    if hi <= tolerance:
        return 0.0
    while hi - lo > tolerance / 2.0:
        mid = (lo + hi) / 2.0
        try:
            eroded = polygon.buffer(-mid)
        except Exception:  # géométrie pathologique : on rend ce qui est acquis
            break
        if eroded.is_empty:
            hi = mid
        else:
            lo = mid
    return round(lo * 2.0, 3)


def _declared_space_container(element) -> str | None:
    """GUID de l'``IfcSpace`` déclaré conteneur par le fichier, s'il y en a un.

    Rare en pratique : la plupart des exports rattachent les éléments à
    l'``IfcBuildingStorey``. Quand elle existe, cette information vient du
    fichier et prime sur toute déduction géométrique.
    """
    try:
        container = ifc_utils.ue.get_container(element)
    except Exception:
        return None
    if container is not None and container.is_a("IfcSpace"):
        return container.GlobalId
    return None


def _z_overlap(a: tuple[float, ...] | None, b: SpaceRecord) -> float:
    if a is None or b.z_min is None or b.z_max is None:
        return 0.0
    return max(0.0, min(a[5], b.z_max) - max(a[4], b.z_min))


def _resolve_container(
    measures, declared: str | None, spaces: list[SpaceRecord], tree: STRtree
) -> dict | None:
    """Rattache un objet à un espace, en nommant la méthode utilisée."""
    if declared:
        return {"space_global_id": declared, "method": "ifc_declared"}
    if measures.centroid is None:
        return None
    centroid = Point(measures.centroid[0], measures.centroid[1])

    # Une menuiserie n'a pas d'empreinte (plaque verticale) mais a un centroïde.
    # Exiger l'empreinte priverait de tout rattachement la classe qui compte le
    # plus pour les contrôles de largeur de passage.
    probe = measures.footprint
    if probe is None or probe.is_empty:
        for index in tree.query(centroid):
            space = spaces[index]
            if _z_overlap(measures.bbox, space) < MIN_Z_OVERLAP_M:
                continue
            if space.footprint is not None and space.footprint.contains(centroid):
                return {
                    "space_global_id": space.guid,
                    "method": "centroid_in_footprint",
                    "overlap_ratio": None,
                }
        return None

    best: tuple[float, SpaceRecord] | None = None
    for index in tree.query(measures.footprint):
        space = spaces[index]
        if (
            space.footprint is None
            or _z_overlap(measures.bbox, space) < MIN_Z_OVERLAP_M
        ):
            continue
        if centroid is not None and space.footprint.contains(centroid):
            inter = measures.footprint.intersection(space.footprint)
            ratio = (
                inter.area / measures.footprint.area
                if measures.footprint.area
                else None
            )
            return {
                "space_global_id": space.guid,
                "method": "centroid_in_footprint",
                "overlap_ratio": round(ratio, 3) if ratio is not None else None,
            }
        inter = measures.footprint.intersection(space.footprint)
        if inter.is_empty or not measures.footprint.area:
            continue
        ratio = inter.area / measures.footprint.area
        if ratio >= MIN_OVERLAP_RATIO and (best is None or ratio > best[0]):
            best = (ratio, space)

    if best is None:
        return None
    return {
        "space_global_id": best[1].guid,
        "method": "footprint_overlap",
        "overlap_ratio": round(best[0], 3),
    }


def _space_entry(space: SpaceRecord) -> dict:
    footprint = space.footprint
    bbox = None
    if footprint is not None and not footprint.is_empty:
        x_min, y_min, x_max, y_max = footprint.bounds
        bbox = (x_min, x_max, y_min, y_max, space.z_min or 0.0, space.z_max or 0.0)
    clear = (
        round(space.z_max - space.z_min, 3)
        if space.z_min is not None and space.z_max is not None
        else None
    )
    return {
        "global_id": space.guid,
        "ifc_class": "IfcSpace",
        "name": space.name,
        "long_name": space.long_name,
        "storey": space.storey,
        "room_type": space.room_type or normalize_room_type(space.raw_label),
        "zones": list(space.zones),
        "geometry_status": "ok" if footprint is not None else "degenerate",
        "bbox": _bbox_dict(bbox),
        "centroid": (
            (
                round(footprint.centroid.x, 4),
                round(footprint.centroid.y, 4),
                round((space.z_min or 0.0), 4),
            )
            if footprint is not None and not footprint.is_empty
            else None
        ),
        "footprint_area_m2": (
            round(space.area_recalc_m2, 3) if space.area_recalc_m2 is not None else None
        ),
        "area_declared_m2": (
            round(space.area_declared_m2, 3)
            if space.area_declared_m2 is not None
            else None
        ),
        "area_recalc_m2": (
            round(space.area_recalc_m2, 3) if space.area_recalc_m2 is not None else None
        ),
        "min_rect_width_m": _min_rect_width(footprint),
        "inscribed_diameter_m": _inscribed_diameter(footprint),
        "clear_height_m": clear,
        "contained_global_ids": [],
    }


def run(
    model,
    *,
    file_name: str = "",
    excluded_classes: tuple[str, ...] = DEFAULT_EXCLUDED_CLASSES,
    spaces: list[SpaceRecord] | None = None,
) -> dict:
    """Construit le document ``spatial_evidence/v1`` complet, provenance comprise."""
    spaces = spaces if spaces is not None else build_spaces(model)
    space_entries = {s.guid: _space_entry(s) for s in spaces}

    geo_spaces = [
        s for s in spaces if s.footprint is not None and s.footprint.area > 1e-6
    ]
    tree = STRtree([s.footprint for s in geo_spaces])

    excluded = set(excluded_classes)
    try:
        products = model.by_type("IfcElement")
    except Exception:
        products = model.by_type("IfcProduct")
    selected = [p for p in products if not any(p.is_a(cls) for cls in excluded)]

    objects: list[dict] = []
    seen_classes: set[str] = set()
    occupants: dict[str, list[Polygon]] = {}
    by_status: Counter[str] = Counter()
    by_method: Counter[str] = Counter()
    n_with_bbox = n_without = n_contained = 0

    for element in selected:
        ifc_class = element.is_a()
        seen_classes.add(ifc_class)
        measures = ifc_utils.element_measures(element)
        is_opening = ifc_class in OPENING_CLASSES
        by_status[measures.status] += 1

        if measures.bbox is None:
            n_without += 1
        else:
            n_with_bbox += 1

        container = _resolve_container(
            measures, _declared_space_container(element), geo_spaces, tree
        )
        if container:
            n_contained += 1
            by_method[container["method"]] += 1
            entry = space_entries.get(container["space_global_id"])
            if entry is not None:
                entry["contained_global_ids"].append(element.GlobalId)
                if measures.footprint is not None:
                    occupants.setdefault(container["space_global_id"], []).append(
                        measures.footprint
                    )

        objects.append(
            {
                "global_id": element.GlobalId,
                "ifc_class": ifc_class,
                "name": element.Name,
                "type_name": _type_name(element),
                "storey": ifc_utils.storey_name(element),
                "geometry_status": measures.status,
                "bbox": _bbox_dict(measures.bbox),
                "centroid": (
                    tuple(round(v, 4) for v in measures.centroid)
                    if measures.centroid
                    else None
                ),
                "footprint_area_m2": (
                    round(measures.recalc_area_m2, 3)
                    if measures.recalc_area_m2 is not None
                    else None
                ),
                "opening_width_m": (
                    round(measures.opening_width_m, 3)
                    if is_opening and measures.opening_width_m is not None
                    else None
                ),
                "opening_height_m": (
                    round(measures.opening_height_m, 3)
                    if is_opening and measures.opening_height_m is not None
                    else None
                ),
                "is_external": ifc_utils.is_external(element),
                "container": container,
            }
        )

    # Encombrement : UNION des empreintes, jamais leur somme — deux objets
    # superposés (un radiateur devant un mur) compteraient deux fois leur place.
    for space in geo_spaces:
        polygons = occupants.get(space.guid)
        if not polygons:
            continue
        try:
            covered = unary_union(polygons).intersection(space.footprint)
        except Exception:
            continue
        space_entries[space.guid]["occupancy_area_m2"] = round(covered.area, 3)

    return {
        "schema": SCHEMA_SPATIAL_EVIDENCE_V1,
        "source": contract_source("extract_spatial_evidence", file_name),
        "created_at": utc_now_iso(),
        "selection": {
            "classes": sorted(seen_classes),
            "excluded_classes": sorted(excluded),
            "n_products_total": len(model.by_type("IfcProduct")),
            "n_selected": len(selected),
        },
        "coverage": {
            "n_objects": len(objects),
            "n_with_bbox": n_with_bbox,
            "n_without_bbox": n_without,
            "n_no_representation": by_status["no_representation"],
            "n_shape_failed": by_status["shape_failed"],
            "n_degenerate": by_status["degenerate"],
            "n_spaces": len(spaces),
            "n_spaces_with_footprint": len(geo_spaces),
            "n_contained": n_contained,
            "n_uncontained": len(objects) - n_contained,
            "n_contained_by_method": dict(by_method),
        },
        "objects": objects,
        "spaces": list(space_entries.values()),
    }


def _type_name(element) -> str | None:
    try:
        etype = ifc_utils.ue.get_type(element)
    except Exception:
        return None
    return getattr(etype, "Name", None) if etype is not None else None
