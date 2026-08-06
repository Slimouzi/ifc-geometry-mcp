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
# bim-core (contrats JSON versionnés + sandbox de chemins) n'est PAS publié
# sur PyPI : on l'installe d'abord depuis son tag Git, sinon la résolution de
# la dépendance ``bim-core>=0.4.0,<0.5`` échoue.
pip install "git+https://github.com/Slimouzi/bim-core.git@bim-core-v0.4.0"
pip install -e .
```

Dépendances clés : `ifcopenshell>=0.8`, `shapely>=2.0`, `numpy`, `fastmcp>=3.0`
(sur PyPI) et `bim-core` (tag Git — préinstallation ci-dessus).

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

Trois sorties sont des **contrats versionnés** définis dans
[`bim-core`](https://github.com/Slimouzi/bim-core) (`bim_core.contracts`) :

| Outil | Fichier | Schéma |
|---|---|---|
| `extract_envelope_surfaces` | `<stem>_envelope.json` | `envelope_quantities/v1` |
| `export_computed_base_quantities` | `<stem>_computed_quantities.json` | `computed_base_quantities/v1` |
| `extract_spatial_evidence` | `<stem>_spatial_evidence.json` | `spatial_evidence/v1` |

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

### `spatial_evidence/v1` — preuves géométriques neutres

Socle destiné aux profils AMO qui doivent trancher des contrôles de dimension,
de contenance et d'encombrement plutôt que des propriétés IFC. Le document ne
porte **aucun seuil et aucun verdict** : un seuil appartient au maître d'ouvrage
qui l'écrit, une mesure appartient à la maquette.

Ce que le contrat impose de dire honnêtement :

- **Il n'existe pas de champ « largeur ».** Deux approximations nommées par leur
  méthode, `min_rect_width_m` (petit côté du rectangle englobant orienté) et
  `inscribed_diameter_m` (plus grand cercle inscrit). Elles coïncident et valent
  la largeur sur une pièce **convexe**. Sur un L à branches de 2,00 m elles
  rendent 6,00 et 2,34 : **aucune des deux n'est la largeur du passage le plus
  étroit**. Trancher un contrôle de largeur de circulation sur forme quelconque
  demande un axe médian, absent de ce lot.
- **Le rattachement objet → espace porte sa méthode** — `ifc_declared` (lu dans
  le fichier), `centroid_in_footprint` ou `footprint_overlap` (déduits). Un
  consommateur exigeant peut refuser les deux derniers.
- **Ce qui n'a pas été mesuré est compté, pas supprimé.** `geometry_status`
  distingue `no_representation` (lacune de maquette) de `shape_failed` (forme
  déléguée à des sous-éléments écartés par la sélection — conséquence du
  périmètre, pas défaut du fichier) et de `degenerate` (boîte englobante
  disponible, empreinte non : le cas de toutes les menuiseries).

Sélection par **exclusion** (`selection.excluded_classes`), jamais par liste
blanche : une classe oubliée dans une liste blanche disparaîtrait sans bruit et
le consommateur conclurait à une absence.

Relevé sur la maquette de référence (10 524 produits, 3 362 retenus, ~18 s) :
2 452 boîtes englobantes, 757 `shape_failed` (murs composites Archicad), 409
`degenerate` (menuiseries), 868 rattachements dont 749 déclarés par l'IFC.

Les cinq JSON de findings préliminaires (`*_space_inventory.json`,
`*_space_clash_findings.json`, `*_surface_loss.json`, `*_boundaries.json`,
`*_openings_check.json`) ne sont **pas encore** contractualisés : leur forme
reste verrouillée par `tests/test_contract.py`.

## Sélection de l'enveloppe : trois modes

`extract_envelope_surfaces` propose trois modes de sélection des murs. Le mode
est déduit des motifs fournis, ou imposé par `filter_mode`.

| Mode | Déclenchement | Sélection | Total façade |
|---|---|---|---|
| `layer_type_filter` (ArchiCAD) | `layer_pattern` fourni | murs du calque, filtrés par `type_pattern` | `NetSideArea` des types retenus, menuiseries **exclues** |
| `geometric_type_filter` (Revit) | `type_pattern` **seul** | murs extérieurs géométriques, filtrés par `type_pattern` | `NetSideArea` des types retenus, menuiseries **exclues** |
| `geometric` (défaut) | aucun motif | murs marqués extérieurs (limites d'espace ou `IsExternal`) | murs extérieurs, menuiseries **incluses** |

Sur une maquette **ArchiCAD**, le mode géométrique ne reproduit pas la
décomposition MOA : le calque est ce qui délimite réellement l'enveloppe, et il
faut encore écarter les habillages (zinc, alu, bois, couvertines) qu'il
contient.

Sur une maquette **Revit**, le problème est inverse : il n'y a aucun calque, et
chaque façade est modélisée en **murs superposés** — structure porteuse,
doublage isolant, peau extérieure. Sommer les murs extérieurs compte alors la
même façade trois ou quatre fois : sur un cas réel, 9 030 m² de façade pour
2 392 m² de SHAB, soit un ratio de 3,77 physiquement absurde. `type_pattern`
désigne la couche qui représente la façade et ramène le total à 2 206 m²
(ratio 0,92).

Les motifs sont **explicites** — aucune valeur n'est codée en dur pour un
projet donné :

```python
# ArchiCAD : le calque délimite, le type écarte les habillages.
extract_envelope_surfaces(
    "modele.ifc",
    seuil_3f=0.9,
    layer_pattern=r"221|ext[ée]rieurs?\s+p[ée]riph[ée]riques",
    type_pattern=r"^ME[ _]",
)

# Revit : pas de calque, le type désigne la peau extérieure.
extract_envelope_surfaces(
    "modele.ifc",
    type_pattern=r"MUR ENDUIT|BARDAGE BOIS|ZINC|VERRE REGLIT",
    filter_mode="geometric_type_filter",  # facultatif : déduit du motif
)
```

`filter_mode` **impose** le mode au lieu de le déduire. Un mode demandé dont le
motif manque est une erreur, jamais une dégradation silencieuse : se rabattre
sur une autre sélection changerait la nature du total sans que rien ne le dise.

Le filtre appliqué est tracé dans `diagnostics.filters` du JSON produit — mode,
motifs, **types retenus et types rejetés**. Le résultat est donc rejouable à
partir des seuls paramètres, sans retouche du contrat après génération.

**Menuiseries en mode `geometric_type_filter`.** Elles sont comptées sur les
murs extérieurs **avant** le filtre de type, et non sur les seuls types retenus.
Ce n'est pas un oubli : dans une façade Revit multicouche, la baie est portée par
le mur **porteur** (béton, ossature), pas par la peau extérieure, qui est une
couche non porteuse. Mesuré sur une maquette réelle : 108 menuiseries / 375,89 m²
sur les 404 murs extérieurs, et **zéro** sur les 128 murs des types retenus.
Conséquence assumée : la ventilation par type (`menuiseries_m2`) peut être nulle
sur toutes les lignes alors que le total ne l'est pas — le contrat expose alors
`diagnostics.menuiseries_m2_sur_types_rejetes` pour que l'écart s'explique.

**Ratio FAC/SHAB.** Définition **unique** dans les trois modes :
`superficie_facades_nette_m2 / shab_m2`, menuiseries exclues — celle que le
livrable Excel calcule. Deux définitions concurrentes du même indicateur ont
circulé (0,92 dans le classeur, 1,05 dans le contrat) ; il n'en reste qu'une.

**Nom de type métier.** Résolu dans cet ordre : **type IFC**
(`IfcWallType.Name` via `IfcRelDefinesByType`, atteint par
`ifcopenshell.util.element.get_type` — et non `IsTypedBy`, qui n'existe qu'en
IFC4 alors que les maquettes I3F sont en IFC2X3), puis `ObjectType`, puis
`PredefinedType`. Ce dernier n'est qu'un recours ultime : il vaut
`ELEMENTEDWALL` pour tous les murs ArchiCAD et écraserait la décomposition
métier en un type unique. Le `Name` d'**instance** n'intervient qu'en tout
dernier : sur Revit il porte l'identifiant de l'élément
(`Mur de base:MUR ENDUIT 20 mm:3566323`) et ferait un type distinct par mur.

**SHAB.** Une seule définition dans les **trois** modes : seules les pièces
**rattachées à une zone** comptent — donc à un logement — hors annexes non
habitables (cellier, cave, balcon, garage, escalier, local). Les exclusions
appliquées sont listées dans `diagnostics.shab_types_exclus`.

`summary.methode_shab` déclare la méthode employée, au même titre que
`methode_facade`. Le ratio a une formule unique, mais comparer deux ratios
suppose des dénominateurs de même nature : le mode géométrique comptait
auparavant **toutes** les pièces, y compris hors logement, et son ratio n'était
donc pas comparable à celui des modes filtrés.

Sur une maquette **sans aucune `IfcZone`**, le zonage n'est pas une donnée
manquante mais une convention absente : le calcul se replie sur toutes les
pièces hors annexes et le déclare
(`methode_shab: "toutes_pieces_hors_annexes_sans_zonage"`). En revanche, si des
zones existent mais qu'aucune pièce n'y est rattachée, la SHAB vaut **0** et le
ratio devient nul — c'est un défaut de modélisation réel, qu'un repli
masquerait sous un chiffre d'apparence normale.

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
