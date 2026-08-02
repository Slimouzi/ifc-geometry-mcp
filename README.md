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
# bim-sandbox (sandbox de chemins partagée) n'est PAS publié sur PyPI : on
# l'installe d'abord depuis son tag Git, sinon la résolution de la dépendance
# ``bim-sandbox>=0.1.0,<0.2`` échoue.
pip install "git+https://github.com/Slimouzi/bim-sandbox.git@bim-sandbox-v0.1.0"
# bim-core (contrats JSON versionnés) — même raison, même mode d'installation.
pip install "git+https://github.com/Slimouzi/bim-core.git@bim-core-v0.2.0"
pip install -e .
```

Dépendances clés : `ifcopenshell>=0.8`, `shapely>=2.0`, `numpy`, `fastmcp>=3.0`
(sur PyPI), `bim-sandbox` et `bim-core` (tags Git — préinstallations ci-dessus).

## Configuration MCP (Claude Desktop / Cowork)

Ajouter dans le bloc `mcpServers`, **en alignant `AUDIT_OUTPUT_DIR` sur celui
d'`audit-bim-i3f`** pour que les JSON atterrissent là où l'import les lit :

```json
"ifc-geometry": {
  "command": "/Users/stani/code/MCP/ifc-geometry-mcp/.venv/bin/ifc-geometry-mcp",
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

## Contrats JSON émis

Deux sorties sont des **contrats versionnés** définis dans
[`bim-core`](https://github.com/Slimouzi/bim-core) (`bim_core.contracts`) :

| Outil | Fichier | Schéma |
|---|---|---|
| `extract_envelope_surfaces` | `<stem>_envelope.json` | `envelope_quantities/v1` |
| `export_computed_base_quantities` | `<stem>_computed_quantities.json` | `computed_base_quantities/v1` |

**Ce sont les sorties officielles du serveur.** Ce MCP *calcule* ; la mise en
forme client (XLSX, DOCX, PDF) relève d'`audit-bim-i3f`, qui consomme ces JSON.
`extract_envelope_surfaces` sait encore produire un classeur `.xlsx`, mais il
est **LEGACY** : plus écrit par défaut, disponible via `legacy_xlsx=True`, et
émettant un `DeprecationWarning`. Le remplacement est de passer
`<stem>_envelope.json` en `envelope_json` à `generate_avp_i3f_pack` — ou de
laisser ce dernier le résoudre seul. Dupliquer la charte MOA dans deux dépôts
créerait deux vérités possibles pour un même livrable.

Chaque document porte `schema`, `source` (producteur, outil, version, maquette),
`created_at`, et ses données métier — `summary` / `par_type` /
`hors_filtre_type` pour l'enveloppe, `quantities` / `coverage` pour les
quantités. Les payloads sont **validés avant écriture** : un document non
conforme fait échouer l'outil au lieu de produire un fichier douteux.

Ce sont des documents **V1 d'origine**, jamais des payloads historiques migrés :
ils sont acceptés par bim-core sans avertissement de compat et passent le mode
strict `BIM_CORE_JSON_STRICT_SCHEMA=true` (test `tests/test_emitted_contracts.py`).

Les cinq JSON de findings préliminaires (`*_space_inventory.json`,
`*_space_clash_findings.json`, `*_surface_loss.json`, `*_boundaries.json`,
`*_openings_check.json`) ne sont **pas encore** contractualisés : leur forme
reste verrouillée par `tests/test_contract.py`.

## Sélection de l'enveloppe : calque + type

`extract_envelope_surfaces` propose deux modes de sélection des murs.

| Mode | Déclenchement | Sélection | Total façade |
|---|---|---|---|
| **Calque + type** (I3F) | `layer_pattern` fourni | murs du calque, filtrés par `type_pattern` | `NetSideArea` des types retenus, menuiseries **exclues** |
| **Géométrique** (défaut) | aucun paramètre | murs marqués extérieurs (limites d'espace ou `IsExternal`) | murs extérieurs, menuiseries **incluses** |

Sur une maquette ArchiCAD, le mode géométrique ne reproduit pas la
décomposition MOA : le calque est ce qui délimite réellement l'enveloppe, et il
faut encore écarter les habillages (zinc, alu, bois, couvertines) qu'il
contient. D'où les deux motifs, **explicites** — aucune valeur n'est codée en
dur pour un projet :

```python
extract_envelope_surfaces(
    "modele.ifc",
    seuil_3f=0.9,
    layer_pattern=r"221|ext[ée]rieurs?\s+p[ée]riph[ée]riques",
    type_pattern=r"^ME[ _]",
)
```

Les motifs employés sont repris dans `diagnostics.filters` du JSON produit : la
sélection reste auditable et reproductible.

**Nom de type métier.** Il vient du **type IFC** (`IfcWallType.Name`), résolu
via `ifcopenshell.util.element.get_type` — et non de `IsTypedBy`, qui n'existe
qu'en IFC4 alors que les maquettes ArchiCAD I3F sont en IFC2X3. `ObjectType` et
`Name` servent de repli ; `PredefinedType` n'est qu'un dernier recours, car il
vaut `ELEMENTEDWALL` pour tous les murs ArchiCAD et écraserait la décomposition
métier en un type unique.

**SHAB.** En mode calque, seules les pièces **rattachées à une zone** comptent,
hors annexes non habitables (cellier, cave, balcon, garage, escalier, local) —
les exclusions appliquées sont listées dans `diagnostics.shab_types_exclus`.

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
