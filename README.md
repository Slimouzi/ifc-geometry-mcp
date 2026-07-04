# ifc-geometry-mcp

Serveur **MCP `ifc-geometry` d'audit géométrique IFC** basé sur [IfcOpenShell](https://ifcopenshell.org).
Il fournit le moteur « préliminaire » consommé par
[`audit-bim-i3f`](../audit-bim-i3f) : il analyse une maquette IFC et écrit
5 fichiers JSON que `audit-bim-i3f` fusionne dans son audit via
`import_preliminary_findings` (rapport Word, annexe XLSX, topics BCF, Smart Views).

## Ce qu'il détecte

| Outil MCP | Fichier produit | Contrôles |
|-----------|-----------------|-----------|
| `run_space_clash_audit` | `<stem>_space_clash_findings.json` | doublons de pièces, chevauchements, placards double-modélisés, chevauchement vertical entre étages |
| `extract_space_inventory` | `<stem>_space_inventory.json` | pièces trop petites, écart surface déclarée/recalculée, pièces sans zone / sans étage / sans surface, typologies de zones (T1..T5), continuité & duplex, cohérence de nommage, fraîcheur de l'export |
| `compute_surface_loss` | `<stem>_surface_loss.json` | m² perdus par pièce (empiètement murs / poteaux) |
| `check_space_boundaries` | `<stem>_boundaries.json` | pièces sans `IfcRelSpaceBoundary`, limites manquantes entre pièces adjacentes |
| `check_opening_correspondence` | `<stem>_openings_check.json` | réservations structure sans ouverture archi correspondante (mode bi-maquette) |

Le vocabulaire de sortie (flags, classifications, sévérités) est **le contrat**
défini par `audit_bim/audit/rules/preliminary.py` côté `audit-bim-i3f`. Ne pas le
modifier sans mettre à jour les deux côtés.

## Installation

```bash
git clone https://github.com/Slimouzi/ifc-geometry-mcp.git
cd ifc-geometry-mcp
python -m venv .venv && source .venv/bin/activate
pip install -e .
```

Dépendances clés : `ifcopenshell>=0.8`, `shapely>=2.0`, `numpy`, `fastmcp>=3.0`.

## Configuration MCP (Claude Desktop / Cowork)

Ajouter dans le bloc `mcpServers`, **en alignant `AUDIT_OUTPUT_DIR` sur celui
d'`audit-bim-i3f`** pour que les JSON atterrissent là où l'import les lit :

```json
"ifc-geometry": {
  "command": "/Users/stani/code/MCP/ifc-openshell/.venv/bin/ifc-geometry-mcp",
  "args": ["--transport", "stdio"],
  "env": {
    "AUDIT_OUTPUT_DIR": "/Users/stani/code/MCP/audit_out",
    "AUDIT_INPUT_DIR": "/Users/stani/code/MCP/audit_in"
  }
}
```

- `AUDIT_INPUT_DIR` : dossier autorisé pour les IFC lus (sandbox). Si absent,
  des chemins absolus existants sont acceptés (mode local/dev).
- `AUDIT_OUTPUT_DIR` : dossier d'écriture des JSON (défaut `./out`).

## Pipeline type (premier run)

```
extract_space_inventory(ifc_path=".../250613_MN_BAT.ifc")
run_space_clash_audit(ifc_path=...)
compute_surface_loss(ifc_path=...)
check_space_boundaries(ifc_path=...)
check_opening_correspondence(ifc_path=..., structure_ifc_path=...)   # optionnel
          │  (5 JSON dans AUDIT_OUTPUT_DIR)
          ▼
audit-bim-i3f : import_preliminary_findings(space_clash_json=..., inventory_json=...,
                surface_loss_json=..., boundaries_json=..., openings_json=...)
          ▼
generate_avp_i3f_pack / generate_word_report   →  rapport d'audit préliminaire
```

Les 5 outils sont **indépendants** et peuvent tourner en parallèle. Voir
`Runbook_Cablage_ifc-geometry_Premier_Run.md` pour la procédure détaillée.

## Seuils (à confirmer avec I3F)

| Paramètre | Défaut | Outil |
|-----------|--------|-------|
| surface mini pièce habitable | `9.0` m² | `extract_space_inventory.min_area_threshold_m2` |
| tolérance écart surface | `5.0` % | `extract_space_inventory.area_tol_pct` |
| fraîcheur export | `90` j | `extract_space_inventory.stale_after_days` |
| recouvrement pièces | `0.10` | `run_space_clash_audit.overlap_min_ratio` |
| seuil doublon | `0.90` | `run_space_clash_audit.duplicate_ratio` |
| adjacence limites | `0.35` m | `check_space_boundaries.adjacency_tol_m` |
| appariement ouvertures | `0.10` m | `check_opening_correspondence.tolerance_m` |

## Détails géométriques

- Empreinte 2D d'un élément = union (`shapely.unary_union`) des facettes du
  maillage IfcOpenShell (coordonnées monde) projetées sur le plan XY ; les faces
  verticales, projetées en segments, sont éliminées.
- Surface recalculée = aire de cette empreinte ; comparée à la surface déclarée
  (`Qto_SpaceBaseQuantities.NetFloorArea`).
- Perte de surface = intersection de l'empreinte d'une pièce avec celles des
  murs/poteaux dont la plage d'altitude recoupe la pièce (indexation `STRtree`).

## Tests

```bash
pytest -q          # contrat de schéma + smoke géométrique (mini-maquette IFC)
```

`tests/test_contract.py` verrouille la forme des payloads ; `tests/test_geometry_smoke.py`
construit une petite maquette (2 pièces qui se chevauchent + placard inclus +
mur empiétant) et vérifie que le clash, l'inventaire et les pertes de surface
fonctionnent réellement via IfcOpenShell.

## Licence

Apache-2.0 — © Stanislas Limouzi / BIMData.
