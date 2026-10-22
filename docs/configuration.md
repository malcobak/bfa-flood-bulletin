# Configuration

- [Fichiers](#fichiers)
- [config.yaml](#configyaml)
- [focus_stations.yaml](#focus_stationsyaml)
- [notes_template.yaml](#notes_templateyaml)
- [Calcul des niveaux de vigilance](#calcul-des-niveaux-de-vigilance)

## Fichiers

| Fichier | Versionné | Rôle |
|---------|:---:|------|
| `config.yaml` | oui | Configuration commune : chemins par défaut, textes fixes, modèles, niveaux, cartes. |
| `config.local.yaml` | non | Réglages d'une installation. Même structure que `config.yaml`, ne contient que les clés à changer ; fusionné par-dessus `config.yaml` au chargement. Modèle : `config.local.example.yaml`. |
| `focus_stations.yaml` | oui | Zones de focus par défaut et sélection automatique. |
| `notes_template.yaml` | oui | Modèle de texte du prévisionniste pour la ligne de commande (`--notes`). |

Les chemins relatifs sont résolus par rapport au dossier de `config.yaml`. Un autre fichier de
configuration peut être donné avec `--config` (ligne de commande) ou la variable d'environnement
`BULLETIN_CONFIG` (application web) ; son `config.local.yaml` voisin est alors utilisé.

Dans l'application web, l'onglet *Paramètres* remplace certaines valeurs **pour un bulletin seulement**
(stockées dans son brouillon, voir [architecture.md](architecture.md#brouillon-draftjson)).

## config.yaml

### `paths`

| Clé | Défaut | Description |
|-----|--------|-------------|
| `forecast_root` | `data/example_run` | Dossier des sorties FloodProofs (copie du dossier `/data` du serveur CIMA). |
| `forecast_pattern` | `hydro*_{run:%Y%m%d%H%M}.json` | Motif (glob) des fichiers d'un run, relatif à `forecast_root`. `{run:<format strftime>}` est remplacé par la date du run. Arborescence du serveur : `{run:%m}/{run:%d}/{run:%H}/outcome/collections/deterministic/hydro*_{run:%Y%m%d%H%M}.json`. |
| `static_data` | `data/static` | Dossier des couches statiques du modèle. |
| `sections` | `bfa_sections_final.shp` | Points de rapport (champs utilisés : `SEC_NAME`, `BASIN`, `TYPE`, `Q_THR1-3`, `ADMIN_B_L1-3`, `AREA`, `HMC_X` = ligne, `HMC_Y` = colonne dans la grille, à partir de 1). |
| `rivers` | `river_FP_clip.shp` | Tronçons de rivière ; champ `section` = point de rapport dont le tronçon prend la couleur, `log_ups` = taille (épaisseur du trait). |
| `pnt` | `burkinafaso.pnt.txt` | Directions de drainage Continuum (grille ASCII, codage du pavé numérique, 5 = exutoire). |
| `area` | `burkinafaso.area.txt` | Aire drainée en nombre de cellules (grille ASCII). |
| `geodata_dir` | `data/geo` | Fonds cartographiques. |
| `cache_dir` | `cache` | Polygones des bassins versants. |
| `output_dir` | `output` | Un sous-dossier par bulletin. |
| `logos_dir` | `assets/logos` | `dgre_header.png`, `dgre_round.png`, `aics.png`, `cima.png`. |

### `bulletin`

| Clé | Défaut | Description |
|-----|--------|-------------|
| `title` | « Bulletin de surveillance des inondations au Burkina Faso » | Titre de chaque page. |
| `organisation` | DGRE | Guide de lecture (contact). |
| `issue_time` | `10:00` | Heure d'émission imprimée. |
| `issue_weekdays` | `[0, 3]` | Jours d'émission (0 = lundi … 6 = dimanche) : numérotation et « prochain bulletin ». |
| `n_days` | `5` | Nombre de jours de prévision, à partir de la date d'émission. |
| `run_hour` | `0` | Heure (UTC) du run FloodProofs utilisé. |
| `max_run_lookback_days` | `3` | Si le run du jour manque, nombre de jours en arrière où chercher (le bulletin le signale). |
| `season_start` | `2026-01-01` | Le numéro est « AAAA-NN », NN = rang du jour d'émission depuis cette date. |
| `contact`, `footer_note` | | Textes du guide de lecture. |

### `models`

| Clé | Description |
|-----|-------------|
| `names` | Correspondance clé du JSON (`time_series_discharge_simulated-<clé>`) → nom affiché. L'ordre fixe l'ordre des légendes et des couleurs. Un modèle absent de la liste est quand même lu et affiché sous sa clé. |
| `min_models` | Un niveau est retenu pour un point et un jour s'il est atteint par au moins N modèles (`1` = le plus pessimiste). Limité au nombre de modèles disponibles. |
| `enabled` | (facultatif) liste des clés de modèles à utiliser ; défaut : tous. |

### `levels`

| Clé | Description |
|-----|-------------|
| `colors` | Couleurs des niveaux 0 à 3 (vert, jaune, orange, rouge). |
| `nodata_color` | Couleur « pas de données ». |
| `labels`, `short` | Libellés longs (« Vigilance modérée ») et courts (« ORANGE »). |
| `impacts` | Impacts possibles de chaque niveau (guide de lecture). |

### `ffgs`

Fond « crues soudaines » de la carte nationale (Flash Flood Guidance System). Pas encore opérationnel.

| Clé | Description |
|-----|-------------|
| `enabled` | `true` pour l'afficher par défaut. |
| `file` | Fichier vecteur (shp, gpkg, geojson) avec un champ entier de niveau 0-3, raster (GeoTIFF) de niveaux 0-3, ou `demo` (données fictives, signalées sur la carte). |
| `level_field` | Nom du champ de niveau (`level`). |
| `colors`, `labels` | Couleurs et libellés de la légende FFGS. |

### `maps`

| Clé | Défaut | Description |
|-----|--------|-------------|
| `dpi` | 200 | Résolution des figures. |
| `land_color` | `#b9bcb6` | Couleur du territoire. |
| `label_cities_national` | 12 | Villes nommées sur la carte nationale. |
| `label_places_zoom` | 25 | Lieux nommés sur une carte de zone. |
| `show_villages_zoom` | `true` | Points gris des villages dans le bassin. |
| `zoom_margin_deg` | 0.25 | Marge (degrés) autour du bassin sur une carte de zone. |
| `network_min_cells` | 15 | Réseau de drainage d'arrière-plan des cartes de zone : cellules drainant au moins N cellules (1 cellule ≈ 3,4 km²). |

### `plots` (facultatif)

| Clé | Description |
|-----|-------------|
| `past_days` | Nombre de jours de simulation passée affichés à gauche des hydrogrammes (défaut : tout). |

### `focus_file`

Nom du fichier des zones de focus (`focus_stations.yaml`).

## focus_stations.yaml

```yaml
zones:                       # zones de focus par défaut (aucune par défaut : zones: [])
  - section: Mouhoun_Dapola  # SEC_NAME du point de rapport exutoire de la zone
    title: "Mouhoun supérieur jusqu'à Dapola"        # facultatif (défaut : « Mouhoun à Dapola »)
    extra_hydrographs: [Mouhoun_Boromo, Sourou_Yaran] # facultatif, 2 au maximum
    comment: "Montée lente attendue sur le Mouhoun."  # facultatif
    margin_deg: 0.4                                   # facultatif
auto:                        # sélection automatique des points les plus menacés
  enabled: false             # aussi activable avec --auto-focus
  min_level: 2               # 1 = jaune, 2 = orange, 3 = rouge
  max_zones: 4
```

La sélection automatique classe les points par niveau puis par aire drainée (de l'aval vers l'amont) et
ignore un point situé dans le bassin d'une zone déjà retenue.

## notes_template.yaml

Texte du prévisionniste pour la ligne de commande (`--notes notes.yaml`) ; dans l'application web, les
textes se modifient directement.

| Clé | Effet |
|-----|-------|
| `summary` | Remplace le message principal de la page 1 (et masque les lignes par niveau). |
| `additional` | Texte ajouté sous le message principal. |
| `flash_floods` | Texte sur les crues soudaines (page 2). |
| `zones` | Commentaire par zone : `{SEC_NAME: "texte"}`. |

## Calcul des niveaux de vigilance

Pour chaque point de rapport, chaque jour *d* (de 00 h à 24 h UTC, heure locale du Burkina Faso) et chaque
modèle *m* :

1. débit maximal horaire prévu le jour *d* (valeurs postérieures à `time_run`) ;
2. comparaison aux trois seuils du point : niveau 0 (vert) si < seuil d'alerte, 1 (jaune) si ≥ alerte,
   2 (orange) si ≥ alarme, 3 (rouge) si ≥ urgence ;
3. niveau du jour = niveau le plus élevé atteint par au moins `min_models` modèles ;
4. niveau de la carte nationale = maximum sur les `n_days` jours.

Un jour avec moins de 12 valeurs horaires (run trop ancien) est « pas de données ». Si les seuils manquent
dans le JSON, ceux du shapefile (`Q_THR1-3`) sont utilisés. Chaque tronçon de `river_FP_clip.shp` prend la
couleur de son point de rapport (champ `section`).
