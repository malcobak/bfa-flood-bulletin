# Architecture (développeurs)

- [Vue d'ensemble](#vue-densemble)
- [Modules et fonctions](#modules-et-fonctions)
- [Format des sorties FloodProofs](#format-des-sorties-floodproofs)
- [Brouillon (draft.json)](#brouillon-draftjson)
- [Cache des figures](#cache-des-figures)
- [Gabarit HTML et textes modifiables](#gabarit-html-et-textes-modifiables)
- [API de l'application web](#api-de-lapplication-web)
- [Évolutions courantes](#évolutions-courantes)

## Vue d'ensemble

```
                 config.yaml + config.local.yaml
                              │
     focus_stations.yaml ─► new_draft() ─► brouillon (dict / draft.json) ◄── application web (PATCH)
     notes.yaml / CLI                          │
                                               ▼
  sorties FloodProofs ──► load_forecast() ──► Engine.build() ──► figures (maps.py, plots.py, cache)
  data/static ─► Hydro (points, rivières,      │                 textes automatiques (text.py)
                 bassins versants PNT)         ▼
  data/geo ───► GeoLayers               contexte Jinja2 ──► Engine.render() ──► HTML
                                                                 │
                                       Engine.export() ──► PDF (WeasyPrint), CSV, summary.json
```

Un bulletin est entièrement décrit par son **brouillon** : la ligne de commande crée un brouillon par
défaut et l'exporte immédiatement ; l'application web le modifie pas à pas puis l'exporte. Les deux
utilisent le même moteur (`bfa_bulletin.report.Engine`), le même gabarit et produisent les mêmes fichiers.

## Modules et fonctions

### `make_bulletin.py` - ligne de commande

`main(argv)` analyse les options (voir [utilisation.md](utilisation.md#ligne-de-commande)) et appelle
`prepare_geodata`, `Hydro(...).sections` (`--list-sections`), `build_atlas` (`--atlas`) ou `build_bulletin`.

### `bfa_bulletin/config.py` - configuration et dates

| Élément | Description |
|---------|-------------|
| `Config(dict)` | Configuration avec `base_dir` (dossier de `config.yaml`). `path(key)` → chemin absolu de `paths.<key>` ; `static(key)` → fichier `paths.<key>` dans `static_data`. |
| `load_config(path=None)` | Lit `config.yaml` (ou `path`) et fusionne `config.local.yaml` s'il existe (`_merge`, récursif). |
| `load_yaml(path)` | Lit un YAML, `{}` si absent. |
| `fr_date(d, weekday, year)` | « lundi 5 octobre 2026 ». |
| `bulletin_number(issue, cfg)` | « 2026-80 » : rang du jour d'émission depuis `season_start`. |
| `next_issue(issue, cfg)` | Prochain jour d'émission. |
| `JOURS`, `MOIS` | Noms français des jours et mois. |

### `bfa_bulletin/forecast.py` - prévisions FloodProofs et niveaux

| Élément | Description |
|---------|-------------|
| `Forecast` (dataclass) | `run` (datetime du run), `issue`, `days` (jours du bulletin), `table` (une ligne par point de rapport, index `SEC_NAME`), `series` (`{SEC_NAME: DataFrame horaire, une colonne par modèle}`), `models`. Propriété `day_cols` = `["d0", …, "d4"]` ; `level_counts(col)`. |
| `read_json(path)` | Lit un fichier FloodProofs → `(métadonnées, DataFrame horaire)` ; -9999 → NaN, séries tronquées complétées. |
| `find_run_files(cfg, issue, root, pattern)` | Run le plus récent ≤ date d'émission ayant des fichiers (jusqu'à `max_run_lookback_days` jours avant), ou le run imposé par `bulletin.force_run`. |
| `load_forecast(cfg, issue, sections, root, pattern)` | Lit tous les fichiers du run et calcule la table : seuils `thr_alert/alarm/emergency`, niveaux par jour `d0…`, débits max par jour `q0…`, `level_max`, `first_day`, `peak_q`, `peak_time`, `peak_model`, `models_alert`, `n_models`, plus les attributs du shapefile (`BASIN`, `ADMIN_B_L1`…). Filtre les modèles selon `models.enabled`. |
| `available_runs(cfg, root, pattern)` | Liste des runs présents (du plus récent au plus ancien). |
| `_level(q, thr)`, `_aggregate(levels, min_models)` | Niveau d'un débit ; niveau atteint par au moins N modèles. |

Colonnes de `Forecast.table` utilisées ailleurs : `d0…d4` (niveau -1…3), `q0…q4`, `level_max`,
`first_day`, `thr_*`, `peak_*`, `models_alert`, `n_models`, `drained_area`, `AREA`, `ADMIN_B_L1/2/3`.

### `bfa_bulletin/hydro.py` - couches hydrographiques

`Hydro(cfg)` charge paresseusement (propriétés mises en cache) :

| Élément | Description |
|---------|-------------|
| `sections` | Points de rapport (GeoDataFrame, index `SEC_NAME`), replacés au centre de leur cellule de modèle. |
| `rivers` | Tronçons de rivière (`section`, `log_ups`). |
| `basins` | Bassin versant amont de chaque point (polygones), calculé depuis le PNT puis mis en cache dans `cache/basins.gpkg` (recalculé si le PNT ou les points changent). |
| `upstream_mask(row0, col0)` | Masque des cellules drainant vers une cellule (parcours de l'arbre de drainage, numpy). |
| `basin_polygon(section)` | Polygone et nombre de cellules du bassin d'un point. |
| `network_segments(min_cells, extent)` | Segments du réseau de drainage (cellules drainant ≥ `min_cells`) pour le fond des cartes de zone. |
| `sections_in(geom)` | Points de rapport situés dans un polygone. |

Codage PNT (pavé numérique) : `7 8 9 / 4 5 6 / 1 2 3` = NO N NE / O exutoire E / SO S SE. C'est la même
méthode que le notebook `Continuum_PNT_delimitation_bassins_FR.ipynb`, réécrite sans `pyflwdir`.

### `bfa_bulletin/geodata.py` - fonds cartographiques

| Élément | Description |
|---------|-------------|
| `prepare_geodata(cfg, force)` | Télécharge Natural Earth (pays, admin1, lacs), GeoNames (villes et villages de 7 pays) et geoBoundaries (provinces du BF), découpés au domaine, dans `data/geo/`. |
| `GeoLayers(cfg)` | Accès paresseux : `countries`, `burkina`, `admin1`, `regions` (admin1 regroupés par région), `provinces`, `lakes`, `places` (avec un rang : capitale, chefs-lieux…), `main_towns(n)`. Une couche absente est vide (non dessinée). |

### `bfa_bulletin/maps.py` - cartes (matplotlib)

| Fonction | Description |
|----------|-------------|
| `national_map(path, levels, hydro, geo, cfg, ffgs, focus, demo_ffgs_flag)` | Carte nationale : territoire, régions, rivières colorées par niveau, villes, contour et lettre des zones de focus, légende, fond FFGS éventuel. |
| `day_map(path, levels, hydro, geo, cfg)` | Petite carte d'un jour (rivières principales, points en vigilance). |
| `zoom_map(path, zone, levels, hydro, geo, cfg, fc_table, atlas)` | Carte d'une zone : bassin, réseau de drainage, rivières colorées, provinces, villes et villages, point principal (étoile), points des hydrogrammes complémentaires, carte de situation, légende. Hauteur : `maps.zoom_height`. |
| `atlas_map(path, section, hydro, geo, cfg)` | Carte statique d'un bassin (rivières en bleu, sans prévision). |
| `load_ffgs(cfg, geo, forecast, file)`, `demo_ffgs`, `draw_ffgs` | Lecture et dessin du fond FFGS (vecteur, raster ou démonstration). |
| `draw_background`, `draw_rivers`, `draw_network`, `draw_alert_points`, `river_legend_handles` | Éléments communs. |
| `level_color(cfg, lvl)`, `station_label(sec)`, `fit_extent(bounds, aspect, margin)` | Utilitaires (« Mouhoun à Dapola »…). |

### `bfa_bulletin/plots.py` - hydrogrammes

| Fonction | Description |
|----------|-------------|
| `hydrograph(path, df, row, run, days, cfg, title, figsize, compact)` | Simulation passée (trait noir) + prévision de chaque modèle, bandes et lignes des seuils, ligne du run. `plots.past_days` limite la période passée. |
| `daily_table(df, row, run, days, cfg)` | Débit max journalier par modèle et son niveau (tableau des pages de zone). |
| `model_style(cfg, model)` | Couleur et style de trait d'un modèle (palette sans vert/jaune/orange/rouge). |

### `bfa_bulletin/text.py` - textes automatiques et tendance

| Fonction | Description |
|----------|-------------|
| `summary_text(fc)` | Message principal + une phrase par niveau (« Vigilance rouge : 5 point(s) de rapport - Goroul (Tillabéry) dès lundi… »). |
| `overall_level(fc)` | Niveau maximal du bulletin. |
| `day_cards(fc, previous, cfg)` | Les 5 cartes journalières : niveau, nombre de points, tendance par rapport au bulletin précédent (niveau max et nombre de points en vigilance du même jour). |
| `save_summary(path, fc, number)`, `load_previous_summary(output_dir, issue)` | `summary.json` d'un bulletin ; résumé du dernier bulletin exporté avant une date. |
| `river_name(section)`, `place_name(section)` | Découpe de `SEC_NAME`. |

### `bfa_bulletin/report.py` - brouillon, moteur, export

| Élément | Description |
|---------|-------------|
| `new_draft(cfg, issue, focus, auto_focus, ffgs_file, notes_file, number, forecast_root, forecast_pattern)` | Brouillon par défaut (zones de `focus_stations.yaml` + CLI, textes de `--notes`). |
| `effective_config(cfg, draft)` | Copie de la configuration avec les `options` et la mise en page du brouillon (voir `OPTION_PATHS`). |
| `auto_zones(fc, hydro, min_level, max_zones, exclude)` | Points les plus menacés, un par bassin. |
| `resolve_zones(zones, fc, hydro)` | Complète les zones : lettre, nom de la station, titre par défaut, hydrogrammes complémentaires valides. |
| `Engine(cfg)` | Garde en mémoire `Hydro`, `GeoLayers` et les prévisions déjà lues (12 au plus) ; verrou pour matplotlib/WeasyPrint (non thread-safe). |
| `Engine.forecast(ecfg, issue)` | Prévision (avec cache). `clear_cache()` l'oublie. |
| `Engine.suggest_zones(draft)`, `materialize_auto(draft)` | Sélection automatique ; `materialize_auto` ajoute une seule fois les zones automatiques au brouillon (elles deviennent des zones ordinaires). |
| `Engine.build(draft)` | Dessine les figures manquantes et construit le contexte du gabarit (textes automatiques, tableaux, zones…). |
| `Engine.render(ctx, urls, edit)` | HTML du bulletin. `urls` = adresses des CSS, logos, figures et images (par défaut des chemins `file://`, pour le PDF) ; `edit=True` ajoute les attributs d'édition. |
| `Engine.preview(draft, urls)` | `build` + `render` en mode édition (application web). |
| `Engine.export(draft, html_only)` | HTML, PDF, CSV, `summary.json` ; supprime les figures qui ne servent plus. |
| `build_bulletin(...)` | Utilisée par la ligne de commande : `new_draft` → `materialize_auto` → `export`. |
| `build_atlas(cfg, sections)` | Cartes de bassin de tous les points + `bassins_points_de_rapport.gpkg`. |
| `DEFAULT_LAYOUT`, `OPTION_PATHS`, `IMAGE_PLACES`, `FIG_VERSION` | Valeurs par défaut de la mise en page, options modifiables par bulletin, emplacements d'images, version du dessin. |

### `bfa_bulletin/style.py`

Enregistre les polices Fira Sans (`assets/fonts`) dans matplotlib et définit la couleur d'encre.

### `bfa_bulletin/templates/`

`bulletin.html.j2` (gabarit Jinja2, une `div.page` par page) et `bulletin.css` (mise en page A4 pour
WeasyPrint : en-têtes, pied de page courant avec numérotation, tableaux).

### `webapp/` - application web

| Fichier | Description |
|---------|-------------|
| `app.py` | Application FastAPI : authentification, stockage des brouillons (`output/bulletin_AAAAMMJJ/draft.json`, écriture atomique, numéro de révision), API (ci-dessous), fichiers statiques. |
| `users.py` | Comptes (PBKDF2-SHA256 dans `webapp/users.txt`) et commande `python -m webapp.users add|remove|list`. |
| `static/index.html`, `app.css`, `app.js` | Interface (HTML/CSS/JavaScript sans compilation ; Leaflet pour la carte, chargé depuis unpkg). |
| `static/preview.css` | Ajouté à l'aperçu : pages A4 sur fond gris, blocs modifiables surlignés. |
| `deploy/` | Service systemd, agent launchd, exemple de crontab. |

## Format des sorties FloodProofs

Un JSON par point de rapport et par run (`hydrogram_<section>_<YYYYmmddHHMM>.json`) :

| Clé | Contenu |
|-----|---------|
| `section_name` | `SEC_NAME` du point de rapport. |
| `section_discharge_thr_alert`, `_alarm`, `_emergency` | Seuils de débit (m³/s) des niveaux jaune, orange, rouge. |
| `section_drained_area`, `section_domain` | Aire drainée, domaine. |
| `time_run` | Date du run, `YYYYmmddHHMM`. |
| `time_period` | Pas de temps horaires séparés par des virgules (`YYYY-mm-dd HH:MM`). |
| `time_series_discharge_simulated-<modèle>` | Débits horaires séparés par des virgules, -9999 = manquant. Avant `time_run` toutes les séries sont identiques (simulation forcée par les observations), après elles diffèrent selon le modèle météorologique. |

## Brouillon (draft.json)

```jsonc
{
  "issue": "2026-10-05",              // date d'émission
  "number": "2026-80",
  "options": {                        // remplacent config.yaml pour ce bulletin (OPTION_PATHS)
    "min_models": 2, "models": ["gfs", "ecmwf"], "force_run": "2026-10-04",
    "issue_time": "10:00", "label_cities_national": 12, "label_places_zoom": 25,
    "show_villages_zoom": true, "network_min_cells": 15, "zoom_margin_deg": 0.25, "past_days": 5
  },
  "ffgs": null,                       // null | "demo" | chemin d'un fichier
  "zones": [                          // dans l'ordre des lettres A, B, C…
    {"section": "Goroul_Dolbe", "title": "", "comment": "…", "extra_hydrographs": ["Goroul_Essakane"],
     "margin_deg": 0.3, "auto": true}
  ],
  "auto": {"enabled": false, "min_level": 2, "max_zones": 4, "done": false},
  "texts": {"headline": "…", "part.0": "…"},   // textes modifiés (absent = automatique)
  "layout": {"main_map_width": 168, "zone_layout": "map_first", "...": "…"},   // DEFAULT_LAYOUT
  "images": [{"id": "…", "file": "image_…png", "caption": "…", "place": "page2", "width": 60}],
  "rev": 12, "updated": "2026-10-05T09:12:03", "updated_by": "ana",
  "exported": {"at": "…", "by": "ana", "rev": 12, "files": ["bulletin_….pdf", "….html", "….csv"]}
}
```

## Cache des figures

`Engine._fig(figdir, kind, key, draw)` nomme chaque figure `<type>_<empreinte>.png`, où l'empreinte est le
SHA-1 de tout ce qui influence le dessin (niveaux, zones, options de carte, couleurs, `FIG_VERSION`…).
Une figure dont le fichier existe n'est pas redessinée : modifier un texte ne redessine rien, ajouter une
zone ne dessine que sa carte, son hydrogramme et la carte nationale. Ces noms changent quand le contenu
change, ce qui évite aussi les problèmes de cache du navigateur. **Après une modification du code de
dessin, incrémenter `FIG_VERSION`.** `Engine.export` supprime les figures non utilisées par la version
exportée.

## Gabarit HTML et textes modifiables

Dans `bulletin.html.j2`, la macro `ed(clé, balise, classe)` affiche `txt(clé)` : le texte du brouillon
(`texts[clé]`) s'il existe, sinon le texte automatique calculé dans `Engine.build` (dictionnaire
`defaults`). En mode édition, l'élément porte `data-edit="<clé>"` : l'interface le rend modifiable et
enregistre le texte dans `texts`. La macro `edz(zone, champ)` fait de même pour `zone.<SEC_NAME>.title` et
`.comment` (stockés dans la zone).

Clés : `subtitle`, `headline`, `part.<i>`, `additional`, `river_risk`, `ffgs_text`, `flash`,
`alert_note`, `no_alert`, `zones_note`, `impact.<0-3>`, `method`, `trend_note`, `flash_note`,
`next_issue`, `contact`.

Pour ajouter un texte modifiable : ajouter sa valeur par défaut dans `defaults` (`Engine.build`), l'afficher
avec `{{ ed("ma_cle", "p") }}` dans le gabarit, et un libellé dans `TEXT_LABELS` (`webapp/static/app.js`).

## API de l'application web

Toutes les routes exigent l'authentification si des comptes existent. `{issue}` = `AAAA-MM-JJ`.

| Méthode et route | Description |
|------------------|-------------|
| `GET /` | Interface. |
| `GET /api/meta` | Utilisateur, modèles, niveaux, valeurs par défaut, mise en page par défaut, runs disponibles, bulletins existants. |
| `POST /api/bulletins/{issue}[?reset=true]` | Ouvre le brouillon (le crée s'il n'existe pas ; `reset` le recrée). |
| `GET /api/bulletins/{issue}` | Brouillon. |
| `PATCH /api/bulletins/{issue}` | Mise à jour partielle : `options`, `layout`, `auto`, `texts` fusionnés (`null` = retour à la valeur par défaut) ; `zones`, `images`, `number`, `ffgs` remplacés ; `texts["zone.<SEC>.title|comment"]` modifie une zone. Renvoie le brouillon (révision incrémentée). |
| `GET /api/bulletins/{issue}/preview` | Aperçu HTML modifiable. |
| `GET /api/bulletins/{issue}/sections` | GeoJSON des points de rapport avec niveaux, débits, seuils + run, jours, modèles, comptes par niveau. |
| `POST /api/bulletins/{issue}/suggest` | `{min_level, max_zones}` → points proposés. |
| `POST /api/bulletins/{issue}/upload` | Formulaire : `file`, `kind` (`image`\|`ffgs`), `place`, `caption`, `width`. |
| `POST /api/bulletins/{issue}/export` | Génère PDF, HTML, CSV ; renvoie le brouillon avec `exported`. |
| `GET /api/bulletins/{issue}/fig/{nom}`, `/uploads/{nom}`, `/files/{nom}` | Figures, fichiers importés, fichiers produits (pdf, csv, html). |
| `GET /api/rivers`, `GET /api/basin/{section}` | Rivières et contour d'un bassin (GeoJSON simplifié) pour la carte. |
| `POST /api/reload` | Oublie les prévisions en mémoire (nouveau run). |

## Évolutions courantes

| Besoin | Où |
|--------|----|
| Ajouter un modèle météorologique | `models.names` dans `config.yaml` (clé = suffixe de `time_series_discharge_simulated-`). |
| Changer couleurs, libellés, impacts | `levels` dans `config.yaml`. |
| Changer la mise en page du PDF | `templates/bulletin.html.j2`, `templates/bulletin.css` ; vérifier avec `--html-only` puis le PDF. |
| Ajouter une option de mise en page | `DEFAULT_LAYOUT` (`report.py`), utilisation dans le gabarit (`L.<clé>`), contrôle dans `index.html` (`id="l-<clé>"`) et `LAYOUT_KEYS` (`app.js`). |
| Ajouter une option par bulletin | `OPTION_PATHS` (`report.py`), champ `id="o-<clé>"` dans `index.html` (et `OPTION_INPUTS` dans `app.js`), valeur par défaut dans `/api/meta`. |
| Nouveau domaine / nouveaux points | Remplacer `data/static/` ; le cache des bassins est recalculé. |
