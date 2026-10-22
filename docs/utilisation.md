# Utilisation

- [Ligne de commande](#ligne-de-commande)
- [Application web](#application-web)
- [Fichiers produits](#fichiers-produits)

## Ligne de commande

Toutes les commandes se lancent depuis le dossier de l'outil (`.venv/bin/python make_bulletin.py …`, ou
`python make_bulletin.py …` si l'environnement est activé avec `source .venv/bin/activate`).

```bash
python make_bulletin.py                                   # bulletin du jour (run du jour, sinon jusqu'à 3 jours plus ancien)
python make_bulletin.py --date 2026-10-05                 # date d'émission donnée
python make_bulletin.py --date 2026-10-05 --focus Goroul_Dolbe --focus Pendjari_Arly
python make_bulletin.py --date 2026-10-05 --auto-focus    # + points les plus menacés (voir focus_stations.yaml)
python make_bulletin.py --date 2026-10-05 --notes notes.yaml        # texte du prévisionniste
python make_bulletin.py --date 2026-10-05 --ffgs demo     # aperçu du fond FFGS avec des données fictives
python make_bulletin.py --date 2026-10-05 --ffgs ffgs_20261005.gpkg # vrai produit FFGS
python make_bulletin.py --list-sections                   # liste des 295 points de rapport (SEC_NAME)
python make_bulletin.py --atlas                           # carte statique du bassin de chaque point de rapport
python make_bulletin.py --atlas Mouhoun_Dapola            # … ou seulement de certains
python make_bulletin.py --prepare-geodata                 # retélécharger les fonds cartographiques
```

| Option | Rôle |
|--------|------|
| `--date AAAA-MM-JJ` | Date d'émission (= premier jour de prévision). Défaut : aujourd'hui. Un avertissement s'affiche si ce n'est pas un jour d'émission (lundi / jeudi). |
| `--focus SEC_NAME` | Ajoute une zone de focus (répétable), en plus de celles de `focus_stations.yaml`. |
| `--auto-focus` | Ajoute les points les plus menacés (paramètres `auto` de `focus_stations.yaml`). |
| `--notes FICHIER` | Texte du prévisionniste (copie de `notes_template.yaml`). |
| `--ffgs FICHIER\|demo` | Fond de crues soudaines de la carte nationale. |
| `--number N` | Numéro du bulletin (défaut : n-ième lundi/jeudi depuis `season_start`). |
| `--forecast-root`, `--forecast-pattern` | Remplacent les chemins des sorties FloodProofs de la configuration. |
| `--html-only` | Ne produit pas le PDF (plus rapide, pour tester la mise en page). |
| `--config FICHIER` | Autre fichier de configuration. |
| `-v` | Messages détaillés. |

Le nom d'un point de rapport (`SEC_NAME`) est de la forme `Riviere_Lieu`, p. ex. `Mouhoun_Dapola`
(« Mouhoun à Dapola »).

## Application web

Ouvrir l'adresse de l'application (p. ex. `https://<machine>.<réseau>.ts.net` ou
`http://127.0.0.1:8000`) et s'identifier.

### Déroulement type d'un bulletin

1. Choisir la **date d'émission** et cliquer **Ouvrir**. Si le bulletin n'existe pas, il est créé à partir
   du dernier run FloodProofs disponible (à la date d'émission ou jusqu'à 3 jours avant).
2. Vérifier dans **Paramètres** le run utilisé et le nombre de points par niveau.
3. Dans **Zones**, ajouter les zones de focus (pages détaillées) en cliquant sur les points de la carte.
4. Relire et compléter les textes, **directement dans l'aperçu** (cliquer sur un texte) ou dans **Textes**.
5. Ajuster la **Mise en page** et ajouter des **Images** si besoin.
6. **Générer le PDF**. Il s'ouvre dans un nouvel onglet ; il reste téléchargeable dans l'onglet **Export**.

Le brouillon est **enregistré automatiquement** après chaque modification : on peut fermer la page et
reprendre plus tard, ou travailler à plusieurs sur le même bulletin (la page récupère les modifications
des autres toutes les 8 secondes ; la barre du haut indique la révision et son auteur). On peut regénérer
le PDF autant de fois que nécessaire.

L'aperçu de droite est le bulletin tel qu'il sera imprimé, page par page. Seules les figures dont les
paramètres ont changé sont redessinées, l'aperçu se met donc à jour en une ou deux secondes.

### Onglet Paramètres

| Réglage | Effet |
|---------|-------|
| Résumé du run | Date du run FloodProofs, modèles disponibles, nombre de points de rapport par niveau maximal. |
| Numéro, heure d'émission | Affichés dans l'en-tête de chaque page. |
| Run FloodProofs | *Automatique* (le plus récent ≤ date d'émission) ou un run précis. |
| Modèles météorologiques | Modèles pris en compte dans le calcul des niveaux et les hydrogrammes. |
| Niveau retenu si atteint par au moins N modèle(s) | `1` = le modèle le plus pessimiste ; `2` = au moins deux modèles d'accord, etc. |
| Fond FFGS | Aucun, démonstration (données fictives, signalées comme telles) ou fichier importé (GeoPackage/GeoJSON avec un champ `level` 0-3, ou GeoTIFF de niveaux 0-3). |
| Cartes et graphiques | Nombre de villes nommées (carte nationale) et de lieux nommés (cartes de zone), marge autour du bassin, finesse du réseau de drainage, nombre de jours de simulation passée sur les hydrogrammes, villages. |
| Revenir aux valeurs de config.yaml | Annule les réglages de cet onglet pour ce bulletin. |
| Relire les données | Recharge les fichiers FloodProofs (après l'arrivée d'un nouveau run). |
| Recréer le bulletin automatique | Repart de zéro : textes, zones, images et réglages du bulletin sont perdus. |

Ces réglages ne valent que pour le bulletin ouvert ; `config.yaml` n'est jamais modifié.

### Onglet Zones

* La **carte** montre les points de rapport colorés selon leur niveau maximal sur 5 jours, les cours
  d'eau et le contour des bassins des zones déjà choisies.
* Cliquer sur un point affiche ses informations (niveau par jour, débit maximal, seuils, modèles) et les
  boutons **Ajouter comme zone** ou **Hydrogramme → zone A** (hydrogramme complémentaire, 2 par zone au
  maximum).
* On peut aussi chercher un point par son nom, ou cliquer sur une ligne du tableau de la page 2 de l'aperçu.
* **Proposer** ajoute automatiquement les points les plus menacés (un par bassin, de l'aval vers l'amont),
  selon le niveau minimal et le nombre maximal choisis.
* Chaque zone a un **titre**, un **commentaire du prévisionniste**, ses hydrogrammes complémentaires et
  la **marge de la carte**. Les flèches ↑ ↓ changent l'ordre (et donc les lettres A, B, C…), ✕ supprime
  la zone, ⌖ affiche sa page dans l'aperçu.

### Onglet Textes

Tous les textes du bulletin sont modifiables : sous-titre, message principal, lignes par niveau, texte
complémentaire, évaluation des crues fluviales et soudaines, notes, impacts de chaque niveau, méthode,
prochain bulletin, contact, titres et commentaires des zones.

* Un texte **non modifié** reste automatique : il est recalculé si la prévision change (autre run, autres
  modèles…).
* Un texte **modifié** est marqué « modifié » et n'est plus recalculé ; **↺** revient au texte automatique.
* Dans l'aperçu, cliquer sur un texte pour le modifier ; *Échap* annule, cliquer ailleurs enregistre.
  Les cadres vides en italique gris (texte complémentaire, crues soudaines, commentaire de zone)
  n'apparaissent pas dans le PDF s'ils restent vides.
* Vider une ligne « Points en vigilance » la retire du bulletin.

### Onglet Mise en page

| Réglage | Effet |
|---------|-------|
| Largeur de la carte nationale | 110 à 186 mm. |
| Liste des points par niveau | Lignes « Vigilance rouge / orange / jaune : … » sous le message principal. |
| Bandeau des 5 cartes journalières | Bas de la page 1. |
| Tableau des points en vigilance, nombre maximal de lignes | Page 2 (`0` = toutes ; les points omis sont signalés). |
| Liste des zones de focus | Page 2. |
| Ordre des éléments | Pages de zone : carte puis hydrogramme, ou l'inverse. |
| Hauteur de la carte / de l'hydrogramme | Pages de zone : petite, normale, grande, très grande. |
| Tableau des débits journaliers, hydrogrammes complémentaires | Pages de zone. |
| Guide de lecture | Dernière page. |

### Onglet Images

Ajouter une image (photo de terrain, image satellite, carte météo…) avec une légende, un emplacement
(page 1, page 2, page d'une zone ou page « Informations complémentaires ») et une largeur en % de la
page. Les images déjà ajoutées peuvent être déplacées, redimensionnées ou retirées.

### Onglet Export

* **Générer le PDF** : produit les fichiers du bulletin (voir ci-dessous) et indique s'ils sont à jour
  par rapport au brouillon.
* Liste des autres bulletins (y compris ceux produits en ligne de commande) avec leur PDF.

## Fichiers produits

Dans `output/bulletin_AAAAMMJJ/` :

| Fichier | Contenu |
|---------|---------|
| `bulletin_inondations_BFA_AAAAMMJJ.pdf` | Le bulletin. |
| `bulletin_inondations_BFA_AAAAMMJJ.html` | Même contenu en HTML (archive, lisible sur la machine seulement). |
| `niveaux_vigilance_AAAAMMJJ.csv` | Niveau de chaque point de rapport et de chaque jour, débit de pointe, seuils, modèles. |
| `summary.json` | Résumé par jour, utilisé pour la **tendance** du bulletin suivant. |
| `figures/` | Cartes et hydrogrammes (PNG). |
| `draft.json` | Brouillon de l'application web (réglages, zones, textes, mise en page, images). |
| `uploads/` | Images et fichiers FFGS importés dans l'application web. |

La tendance d'un bulletin est calculée par rapport au dernier bulletin **exporté** (PDF généré) dont la
date d'émission est antérieure.
