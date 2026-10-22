# Installation sur un serveur

Ce guide installe l'outil sur une machine qui reste allumée (serveur Linux, PC ou Mac mini), récupère
automatiquement les prévisions FloodProofs et publie l'application web pour les prévisionnistes.

- [1. Prérequis](#1-prérequis)
- [2. Bibliothèques système](#2-bibliothèques-système)
- [3. Code et environnement Python](#3-code-et-environnement-python)
- [4. Données](#4-données)
- [5. Configuration de l'installation](#5-configuration-de-linstallation)
- [6. Vérification](#6-vérification)
- [7. Application web en service](#7-application-web-en-service)
- [8. Accès à distance](#8-accès-à-distance)
- [9. Tâches planifiées](#9-tâches-planifiées)
- [10. Sauvegarde et mise à jour](#10-sauvegarde-et-mise-à-jour)
- [11. Dépannage](#11-dépannage)

## 1. Prérequis

| | Minimum | Remarque |
|---|---|---|
| Système | Linux (Ubuntu 22.04 / 24.04, Debian 12) ou macOS 13+ | Windows possible mais non documenté (WeasyPrint y est plus délicat). |
| Python | 3.10 à 3.12 | 3.12 recommandé. |
| Mémoire | 2 Go | Le calcul des cartes utilise ~500 Mo. |
| Disque | 1 Go + sorties FloodProofs | Un run FloodProofs ≈ 2,5 Mo (format plat) ; un bulletin ≈ 3 Mo. |
| Réseau | accès SFTP au serveur CIMA | Pour récupérer les prévisions. Internet n'est nécessaire ni pour produire le bulletin, ni pour l'application web (sauf la carte de fond OpenStreetMap de l'onglet *Zones*). |

## 2. Bibliothèques système

Le PDF est produit par **WeasyPrint**, qui a besoin de **Pango** (mise en page du texte). Les autres
dépendances (GDAL via `pyogrio`, PROJ, GEOS) sont fournies par les paquets Python (*wheels*).

**Ubuntu / Debian**

```bash
sudo apt update
sudo apt install -y git python3 python3-venv python3-pip \
                    libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz-subset0 \
                    lftp
```

**macOS** (avec [Homebrew](https://brew.sh))

```bash
brew install git python@3.12 pango lftp
```

> Sur macOS, Python ne trouve pas tout seul les bibliothèques de Homebrew : `run_webapp.sh` définit
> `DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib`. Pour la ligne de commande, faire de même :
> `export DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib` (à ajouter dans `~/.zshrc`).

## 3. Code et environnement Python

```bash
sudo mkdir -p /opt/bfa-flood-bulletin && sudo chown $USER /opt/bfa-flood-bulletin   # ou tout autre dossier
git clone https://github.com/malcobak/bfa-flood-bulletin.git /opt/bfa-flood-bulletin
cd /opt/bfa-flood-bulletin
python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r requirements.txt
```

Paquets Python installés (`requirements.txt`) :

| Paquet | Rôle |
|--------|------|
| numpy, pandas | lecture des séries, calcul des niveaux |
| geopandas, shapely, rasterio | couches géographiques, délimitation des bassins versants |
| matplotlib | cartes et hydrogrammes |
| jinja2 | gabarit HTML du bulletin |
| weasyprint | conversion HTML → PDF |
| pyyaml | fichiers de configuration |
| fastapi, uvicorn, python-multipart | application web |

## 4. Données

| Dossier | Contenu | Dans le dépôt | À faire |
|---------|---------|:---:|---------|
| `data/static/` | Couches FloodProofs/Continuum du domaine : `bfa_sections_final.shp` (295 points de rapport et leurs seuils), `river_FP_clip.shp` (tronçons de rivière, un par point de rapport), `burkinafaso.pnt.txt` (directions de drainage), `burkinafaso.area.txt` (aires drainées) | oui | Remplacer ces fichiers si le domaine du modèle ou les points de rapport changent (le cache des bassins est alors recalculé automatiquement). |
| `data/geo/` | Fonds cartographiques : pays, régions, provinces, lacs, villes et villages | oui | Rien. Pour les régénérer (internet nécessaire) : `.venv/bin/python make_bulletin.py --prepare-geodata`. |
| `data/example_run/` | Deux runs FloodProofs d'exemple | oui | Rien (sert aux essais). |
| sorties FloodProofs opérationnelles | Un fichier JSON par point de rapport et par run | **non** | Les récupérer depuis le serveur CIMA (ci-dessous). |
| `cache/` | Polygones des bassins versants (`basins.gpkg`) | non | Calculé automatiquement au premier lancement (quelques secondes). |
| `output/` | Bulletins produits, brouillons de l'application web | non | À sauvegarder (§10). |

### Données FloodProofs (prévisions)

Les prévisions sont produites par la Fondation CIMA et déposées sur son serveur SFTP. L'identifiant,
l'adresse du serveur et la clé SSH sont fournis par la Fondation CIMA (ne jamais les écrire dans le dépôt).

1. Installer la clé SSH fournie dans `~/.ssh/` (droits `600`) et tester : `sftp <utilisateur>@<serveur>`.
   Pour une copie automatique, la clé ne doit pas demander de phrase secrète, ou être chargée dans un
   `ssh-agent`.
2. Copier le dossier `/data` du serveur :
   ```bash
   lftp -u <utilisateur>, sftp://<serveur> \
        -e "mirror --only-newer --parallel=4 /data /srv/floodproofs/data; bye"
   ```
3. Planifier cette copie (§9) et indiquer le dossier dans `config.local.yaml` (§5).

Format attendu : un JSON par point de rapport et par run, contenant `section_name`,
`section_discharge_thr_alert/_alarm/_emergency`, `time_run`, `time_period` (pas horaire) et une série
`time_series_discharge_simulated-<modèle>` par modèle météorologique (voir
[architecture.md](architecture.md#format-des-sorties-floodproofs)). Deux organisations des fichiers sont
prises en charge par `forecast_pattern` :

* arborescence du serveur : `MM/DD/HH/outcome/collections/deterministic/hydro*_<YYYYmmddHHMM>.json` ;
* dossier plat : `hydrogram_<section>_<YYYYmmddHHMM>.json` (comme `data/example_run/`).

## 5. Configuration de l'installation

`config.yaml` contient la configuration commune (versionnée). Les réglages propres à une machine vont
dans **`config.local.yaml`** (non versionné), qui ne contient que les clés à modifier :

```bash
cp config.local.example.yaml config.local.yaml
nano config.local.yaml
```

```yaml
paths:
  forecast_root: /srv/floodproofs/data
  forecast_pattern: "{run:%m}/{run:%d}/{run:%H}/outcome/collections/deterministic/hydro*_{run:%Y%m%d%H%M}.json"
bulletin:
  contact: "DGRE - Service de l'Hydrologie - Tél. : +226 .. .. .. .. - email : ...@..."
  season_start: 2026-06-01
```

Toutes les clés sont décrites dans [configuration.md](configuration.md).

## 6. Vérification

```bash
cd /opt/bfa-flood-bulletin
.venv/bin/python make_bulletin.py --list-sections | head        # couches statiques lisibles
.venv/bin/python make_bulletin.py --date 2026-10-05 \
     --forecast-root data/example_run --focus Goroul_Dolbe       # bulletin d'exemple
ls output/bulletin_20261005/                                     # PDF, HTML, CSV, figures
.venv/bin/python make_bulletin.py                                # bulletin du jour avec les vraies données
```

Le premier lancement calcule les bassins versants des 295 points de rapport ; les suivants
prennent quelques secondes.

## 7. Application web en service

### Comptes utilisateurs

L'application utilise une authentification HTTP simple (le navigateur demande identifiant et mot de passe).
**Sans aucun compte, l'application est ouverte à tous** : créer les comptes avant de la rendre accessible.

```bash
.venv/bin/python -m webapp.users add ana        # crée le compte ou change le mot de passe (saisi au clavier)
.venv/bin/python -m webapp.users list
.venv/bin/python -m webapp.users remove ana
```

Les mots de passe sont stockés hachés (PBKDF2) dans `webapp/users.txt`. Les modifications sont prises en
compte immédiatement, sans redémarrage.

### Lancement manuel

```bash
./run_webapp.sh                          # http://127.0.0.1:8000 (accessible depuis la machine seulement)
BULLETIN_PORT=8080 ./run_webapp.sh
BULLETIN_HOST=0.0.0.0 ./run_webapp.sh    # accessible depuis le réseau local
```

### Service Linux (systemd)

```bash
sudo useradd --system --home /opt/bfa-flood-bulletin bulletin    # utilisateur dédié (optionnel)
sudo chown -R bulletin /opt/bfa-flood-bulletin
sudo cp webapp/deploy/bulletin-web.service /etc/systemd/system/  # adapter User et les chemins
sudo systemctl daemon-reload
sudo systemctl enable --now bulletin-web
journalctl -u bulletin-web -f                                    # journaux
```

### Service macOS (launchd)

Adapter les chemins dans `webapp/deploy/org.dgre.bulletin.plist`, puis :

```bash
cp webapp/deploy/org.dgre.bulletin.plist ~/Library/LaunchAgents/
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/org.dgre.bulletin.plist
launchctl kickstart -k gui/$(id -u)/org.dgre.bulletin             # redémarrer (après une mise à jour)
```

Le service démarre à l'ouverture de session : activer l'ouverture de session automatique, et dans
*Réglages → Énergie* : « Empêcher la suspension automatique » et « Démarrer automatiquement après une
panne de courant » (ou `sudo pmset -a sleep 0 autorestart 1`).

## 8. Accès à distance

L'application écoute sur `127.0.0.1` : elle n'est pas exposée directement. Pour la rendre accessible :

| Solution | Coût | Adresse | Les utilisateurs installent… | Remarques |
|----------|------|---------|-------------------------------|-----------|
| **Tailscale Funnel** (recommandé) | gratuit | `https://<machine>.<réseau>.ts.net` | rien | HTTPS automatique, pas de nom de domaine ni de routeur à configurer. |
| Tailscale (réseau privé) | gratuit | idem | Tailscale | L'application n'est pas visible sur internet. |
| Cloudflare Tunnel | gratuit + nom de domaine (~10 €/an) | `https://bulletin.mondomaine.org` | rien | Cloudflare Access peut ajouter une connexion par e-mail. |
| Proxy nginx + certificat Let's Encrypt | gratuit | nom de domaine du serveur | rien | Serveur avec IP publique et ports 80/443 ouverts. |

**Tailscale Funnel**

```bash
# Installer Tailscale (https://tailscale.com/download), puis :
sudo tailscale up                     # se connecter (lien à ouvrir dans un navigateur)
sudo tailscale funnel --bg 8000       # publie http://127.0.0.1:8000 en HTTPS
tailscale funnel status               # affiche l'adresse publique
sudo tailscale funnel --https=443 off # retirer la publication
```

À la première utilisation, Tailscale demande d'activer HTTPS et Funnel pour le réseau (lien affiché).
La configuration est conservée après un redémarrage.

**nginx** (serveur avec nom de domaine)

```nginx
server {
    server_name bulletin.mondomaine.org;
    client_max_body_size 50m;                 # import d'images / fichiers FFGS
    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 300s;              # génération du PDF
    }
}
```

puis `sudo certbot --nginx -d bulletin.mondomaine.org` pour le HTTPS. Ne jamais publier l'application
en HTTP simple : les mots de passe circuleraient en clair.

## 9. Tâches planifiées

Exemple dans `webapp/deploy/crontab.example` (`crontab -e`) :

```cron
# copie des sorties FloodProofs toutes les heures
15 * * * *  lftp -u USER, sftp://SERVEUR -e "mirror --only-newer --parallel=4 /data /srv/floodproofs/data; bye" >> /var/log/floodproofs_mirror.log 2>&1
# bulletin automatique lundi et jeudi à 9 h 30 (facultatif si l'application web est utilisée)
30 9 * * 1,4  cd /opt/bfa-flood-bulletin && .venv/bin/python make_bulletin.py >> output/cron.log 2>&1
```

Après l'arrivée d'un nouveau run, le bouton **Relire les données** de l'application web (onglet
*Paramètres*) recharge les prévisions gardées en mémoire.

## 10. Sauvegarde et mise à jour

**À sauvegarder** : `output/` (bulletins, brouillons `draft.json`, images importées, `summary.json` qui
sert à la tendance du bulletin suivant), `config.local.yaml`, `webapp/users.txt`.

**Mise à jour du code**

```bash
cd /opt/bfa-flood-bulletin
git pull
.venv/bin/pip install -r requirements.txt
sudo systemctl restart bulletin-web       # macOS : launchctl kickstart -k gui/$(id -u)/org.dgre.bulletin
```

## 11. Dépannage

| Symptôme | Cause / solution |
|----------|------------------|
| `OSError: cannot load library 'libgobject-2.0-0'` (ou `pango`) | Pango absent (§2). Sur macOS : `export DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib`. |
| `No FloodProofs output found for <date>` | Aucun run trouvé jusqu'à `max_run_lookback_days` jours avant la date : vérifier `forecast_root`, `forecast_pattern` et la copie depuis le serveur CIMA. |
| Aperçu « Aperçu impossible » dans l'application | Le message d'erreur s'affiche dans l'aperçu ; détails dans les journaux du service. |
| Le service macOS ne démarre pas, `Operation not permitted` dans `output/webapp.log` | Protection des dossiers *Documents* / *Bureau* de macOS : installer l'outil ailleurs (p. ex. `~/bfa-flood-bulletin`) ou donner « Accès complet au disque » à `.venv/bin/python`. |
| `address already in use` | Le port est occupé (une autre instance tourne déjà) : changer `BULLETIN_PORT` ou arrêter l'autre instance. |
| Les cartes ne changent pas après une modification du code de dessin | Les figures sont en cache (nom = empreinte des paramètres) : incrémenter `FIG_VERSION` dans `bfa_bulletin/report.py`. |
| La carte de l'onglet *Zones* n'a pas de fond | Le navigateur de l'utilisateur n'accède pas à OpenStreetMap ; les points restent utilisables. |
