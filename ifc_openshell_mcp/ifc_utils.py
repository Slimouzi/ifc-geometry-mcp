"""Primitives IfcOpenShell partagées par les analyseurs.

Ouverture de la maquette, hiérarchie spatiale (étage), rattachement aux zones
(IfcZone), et géométrie : empreinte 2D (footprint) projetée sur le plan XY,
plage d'altitude (z), surface recalculée, et surfaces déclarées (Qto).

La géométrie s'appuie sur ``ifcopenshell.geom`` (coordonnées monde) puis sur
``shapely`` pour l'algèbre 2D. Tout est tolérant aux erreurs : un élément sans
représentation géométrique renvoie ``None`` plutôt que de faire échouer l'audit.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np

try:  # imports lourds isolés pour des messages d'erreur clairs
    import ifcopenshell
    import ifcopenshell.geom
    import ifcopenshell.util.element as ue
    import ifcopenshell.util.unit as uu
except Exception as exc:  # pragma: no cover
    raise ImportError(
        f"ifcopenshell est requis (pip install ifcopenshell). Erreur d'import : {exc}"
    ) from exc

from shapely.geometry import Polygon
from shapely.ops import unary_union

logger = logging.getLogger("ifc_openshell_mcp.ifc_utils")

# Aire minimale (m²) d'une facette projetée pour être retenue dans un footprint
# (élimine les faces verticales qui se projettent en segments).
_MIN_FACE_AREA = 1e-4


# --------------------------------------------------------------------------- #
#  Ouverture + réglages géométrie
# --------------------------------------------------------------------------- #
def open_model(path: str):
    """Ouvre une maquette IFC."""
    return ifcopenshell.open(path)


def _geom_settings():
    settings = ifcopenshell.geom.settings()
    # Coordonnées monde : indispensable pour comparer des éléments entre eux.
    try:
        settings.set("use-world-coords", True)
    except Exception:  # API < 0.8
        settings.set(settings.USE_WORLD_COORDS, True)
    return settings


_SETTINGS = _geom_settings()


# --------------------------------------------------------------------------- #
#  Représentation géométrique d'un élément
# --------------------------------------------------------------------------- #
@dataclass
class ElementGeometry:
    """Empreinte 2D + plage d'altitude d'un élément."""

    guid: str
    footprint: Polygon | None  # union des facettes projetées sur XY
    z_min: float | None
    z_max: float | None
    recalc_area_m2: float | None  # aire du footprint (m²)

    @property
    def has_geometry(self) -> bool:
        return self.footprint is not None and not self.footprint.is_empty


def element_geometry(element) -> ElementGeometry:
    """Calcule footprint + z-range d'un élément IFC (best effort)."""
    guid = element.GlobalId
    try:
        shape = ifcopenshell.geom.create_shape(_SETTINGS, element)
    except Exception:
        return ElementGeometry(guid, None, None, None, None)

    try:
        verts = np.asarray(shape.geometry.verts, dtype=float).reshape(-1, 3)
        faces = np.asarray(shape.geometry.faces, dtype=np.int64).reshape(-1, 3)
    except Exception:
        return ElementGeometry(guid, None, None, None, None)

    if verts.size == 0 or faces.size == 0:
        return ElementGeometry(guid, None, None, None, None)

    z_min = float(verts[:, 2].min())
    z_max = float(verts[:, 2].max())

    tris: list[Polygon] = []
    for f in faces:
        tri = verts[f][:, :2]  # projection XY
        poly = Polygon(tri)
        if poly.is_valid and poly.area > _MIN_FACE_AREA:
            tris.append(poly)

    footprint: Polygon | None = None
    recalc: float | None = None
    if tris:
        try:
            merged = unary_union(tris).buffer(0)
            if not merged.is_empty:
                footprint = merged
                recalc = float(merged.area)
        except Exception:
            footprint = None
            recalc = None

    return ElementGeometry(guid, footprint, z_min, z_max, recalc)


# --------------------------------------------------------------------------- #
#  Hiérarchie spatiale + zones
# --------------------------------------------------------------------------- #
def storey_name(element) -> str | None:
    """Nom de l'étage (IfcBuildingStorey) contenant l'élément."""
    try:
        container = ue.get_container(element)
    except Exception:
        container = None
    while container is not None:
        if container.is_a("IfcBuildingStorey"):
            return container.Name
        container = getattr(container, "Decomposes", None)
        container = container[0].RelatingObject if container else None
    # Fallback : remonter la décomposition
    try:
        for rel in getattr(element, "Decomposes", []) or []:
            parent = rel.RelatingObject
            if parent.is_a("IfcBuildingStorey"):
                return parent.Name
    except Exception:
        pass
    return None


def zone_map(model) -> tuple[dict[str, list[str]], dict[str, list[str]]]:
    """Retourne (space_guid → [noms de zones], nom_zone → [space_guid])."""
    space_to_zones: dict[str, list[str]] = {}
    zone_to_spaces: dict[str, list[str]] = {}
    for zone in model.by_type("IfcZone"):
        zname = zone.Name or zone.GlobalId
        members: list[str] = []
        for rel in getattr(zone, "IsGroupedBy", []) or []:
            for obj in rel.RelatedObjects or []:
                if obj.is_a("IfcSpace"):
                    members.append(obj.GlobalId)
                    space_to_zones.setdefault(obj.GlobalId, []).append(zname)
        zone_to_spaces[zone.GlobalId] = members
    return space_to_zones, zone_to_spaces


# --------------------------------------------------------------------------- #
#  Surfaces déclarées (Qto)
# --------------------------------------------------------------------------- #
_AREA_KEYS = ("NetFloorArea", "GrossFloorArea", "NetArea", "GrossArea")


def declared_area(element) -> float | None:
    """Surface déclarée d'une pièce via ses BaseQuantities (m²)."""
    try:
        qtos = ue.get_psets(element, qtos_only=True)
    except Exception:
        return None
    for _, props in (qtos or {}).items():
        for key in _AREA_KEYS:
            val = props.get(key)
            if isinstance(val, (int, float)):
                return float(val)
    return None


def space_long_name(space) -> str | None:
    return getattr(space, "LongName", None)


def unit_scale(model) -> float:
    """Facteur d'échelle des longueurs vers le mètre (1.0 si déjà en m)."""
    try:
        return float(uu.calculate_unit_scale(model))
    except Exception:
        return 1.0


def quantity(element, *names: str) -> float | None:
    """Lit la 1re BaseQuantity numérique trouvée parmi ``names`` (Qto_*)."""
    try:
        qtos = ue.get_psets(element, qtos_only=True) or {}
    except Exception:
        return None
    for _, props in qtos.items():
        for n in names:
            v = props.get(n)
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                return float(v)
    return None


def element_centroid(element) -> tuple[float, float, float] | None:
    """Centroïde géométrique (moyenne des sommets, coords monde) ou ``None``."""
    try:
        shape = ifcopenshell.geom.create_shape(_SETTINGS, element)
        verts = np.asarray(shape.geometry.verts, dtype=float).reshape(-1, 3)
    except Exception:
        return None
    if verts.size == 0:
        return None
    c = verts.mean(axis=0)
    return (float(c[0]), float(c[1]), float(c[2]))


def _mesh(element):
    """Retourne (verts Nx3, faces Mx3) en coords monde, ou ``None``."""
    try:
        shape = ifcopenshell.geom.create_shape(_SETTINGS, element)
        verts = np.asarray(shape.geometry.verts, dtype=float).reshape(-1, 3)
        faces = np.asarray(shape.geometry.faces, dtype=np.int64).reshape(-1, 3)
    except Exception:
        return None
    if verts.size == 0 or faces.size == 0:
        return None
    return verts, faces


def vertical_face_area(element, *, normal_z_max: float = 0.35) -> float | None:
    """Aire des faces ~verticales d'un élément (m²), divisée par 2.

    On somme l'aire des triangles dont la normale est quasi horizontale
    (``|nz| < normal_z_max``) puis on divise par 2 : un mur ayant deux
    parements de surface comparable, cela approxime l'aire d'un seul
    parement (surface de façade nette, ouvertures déduites).
    """
    m = _mesh(element)
    if m is None:
        return None
    verts, faces = m
    total = 0.0
    for f in faces:
        p0, p1, p2 = verts[f[0]], verts[f[1]], verts[f[2]]
        n = np.cross(p1 - p0, p2 - p0)
        area = 0.5 * float(np.linalg.norm(n))
        if area <= 0:
            continue
        nz = abs(n[2]) / (2 * area) if area else 1.0  # composante z normalisée
        if nz < normal_z_max:
            total += area
    return total / 2.0


def bbox_width_height(element) -> tuple[float, float] | None:
    """(largeur, hauteur) d'un élément : diagonale horizontale de l'emprise
    × extension verticale. Adapté aux menuiseries (plan vertical)."""
    m = _mesh(element)
    if m is None:
        return None
    verts, _ = m
    dx = float(verts[:, 0].max() - verts[:, 0].min())
    dy = float(verts[:, 1].max() - verts[:, 1].min())
    dz = float(verts[:, 2].max() - verts[:, 2].min())
    width = (dx**2 + dy**2) ** 0.5
    return width, dz


@dataclass
class ElementMeasures:
    """Toutes les mesures d'un élément, pour **un seul** calcul de forme.

    ``element_geometry``, ``element_centroid`` et ``bbox_width_height`` appellent
    chacune ``create_shape``. Les enchaîner sur le même élément triple le coût du
    balayage complet d'une maquette (mesuré : ~8 ms par élément et par appel).
    Cette fonction fait le calcul une fois et distribue.
    """

    guid: str
    status: str  # "ok" | "no_representation" | "shape_failed" | "degenerate"
    footprint: Polygon | None = None
    bbox: tuple[float, float, float, float, float, float] | None = None
    centroid: tuple[float, float, float] | None = None
    recalc_area_m2: float | None = None
    opening_width_m: float | None = None
    opening_height_m: float | None = None

    @property
    def z_min(self) -> float | None:
        return self.bbox[4] if self.bbox else None

    @property
    def z_max(self) -> float | None:
        return self.bbox[5] if self.bbox else None


def element_measures(element) -> ElementMeasures:
    """Empreinte, boîte englobante, centroïde et dimensions de menuiserie.

    ``status`` distingue trois échecs que la valeur ``None`` seule confondrait,
    et qui n'appellent pas la même conclusion :

    - ``no_representation`` : aucune forme déclarée dans le fichier. Lacune de
      maquette, à signaler au maître d'ouvrage.
    - ``shape_failed`` : une forme est déclarée, mais aucun maillage n'en sort.
      Mesuré sur la maquette de référence : 757 murs sur 1372 sont dans ce cas,
      parce que leur matière vit dans leurs ``IfcBuildingElementPart``. C'est une
      conséquence du périmètre demandé, pas un défaut de la maquette — les
      confondre avec le cas précédent transformerait un choix de sélection en
      alerte qualité.
    - ``degenerate`` : maillage obtenu, boîte englobante disponible, mais aucune
      facette ne se projette en surface. Toutes les portes de la maquette de
      référence sont dans ce cas : une plaque verticale n'a pas d'empreinte, ses
      dimensions restent mesurables.
    """
    guid = element.GlobalId
    mesh = _mesh(element)
    if mesh is None:
        declared = getattr(element, "Representation", None) is not None
        return ElementMeasures(
            guid, "shape_failed" if declared else "no_representation"
        )
    verts, faces = mesh

    bbox = (
        float(verts[:, 0].min()),
        float(verts[:, 0].max()),
        float(verts[:, 1].min()),
        float(verts[:, 1].max()),
        float(verts[:, 2].min()),
        float(verts[:, 2].max()),
    )
    centre = verts.mean(axis=0)
    centroid = (float(centre[0]), float(centre[1]), float(centre[2]))
    # Largeur d'une menuiserie : diagonale horizontale de l'emprise, car une
    # porte n'est pas alignée sur les axes du projet. Voir bbox_width_height.
    width = ((bbox[1] - bbox[0]) ** 2 + (bbox[3] - bbox[2]) ** 2) ** 0.5

    tris = [
        poly
        for f in faces
        if (poly := Polygon(verts[f][:, :2])).is_valid and poly.area > _MIN_FACE_AREA
    ]
    footprint: Polygon | None = None
    recalc: float | None = None
    if tris:
        try:
            merged = unary_union(tris).buffer(0)
            if not merged.is_empty:
                footprint, recalc = merged, float(merged.area)
        except Exception:
            logger.debug("union des facettes impossible pour %s", guid, exc_info=True)

    return ElementMeasures(
        guid=guid,
        status="ok" if footprint is not None else "degenerate",
        footprint=footprint,
        bbox=bbox,
        centroid=centroid,
        recalc_area_m2=recalc,
        opening_width_m=width,
        opening_height_m=bbox[5] - bbox[4],
    )


def is_external(element) -> bool | None:
    """Lit ``IsExternal`` dans les Psets (True/False) ou ``None`` si absent."""
    try:
        psets = ue.get_psets(element) or {}
    except Exception:
        return None
    for _, props in psets.items():
        if "IsExternal" in props:
            v = props["IsExternal"]
            if isinstance(v, bool):
                return v
    return None


def model_name(model, fallback: str) -> str:
    """Nom lisible de la maquette (IfcProject.Name sinon nom de fichier)."""
    try:
        projects = model.by_type("IfcProject")
        if projects and projects[0].Name:
            return projects[0].Name
    except Exception:
        pass
    return fallback
