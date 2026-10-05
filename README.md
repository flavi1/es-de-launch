# es-de-launch

Lance un jeu exactement comme le menu d'[ES-DE](https://es-de.org), à partir d'un simple nom de fichier : même système, même émulateur (y compris les émulateurs alternatifs choisis dans ES-DE), même commande, mêmes scripts `game-start` / `game-end`.

```bash
es-de-launch "~/ROMs/n64/Mario Kart 64.z64"
```

Il sait aussi lancer une ROM rangée hors de la bibliothèque, en détectant son système, et peut la ranger au bon endroit.

> Statut : version 0.1, en cours de validation. Les tests automatiques passent ; la comparaison avec les commandes réellement lancées par ES-DE reste à faire (voir [Limites connues](#limites-connues)).

## Sommaire

- [Prérequis](#prérequis)
- [Installation](#installation)
- [Utilisation](#utilisation)
- [Fonctionnement](#fonctionnement)
- [Configuration : es-de-launch.conf](#configuration--es-de-launchconf)
- [Types d'installation d'ES-DE](#types-dinstallation-des-de)
- [Surcharges d'ES-DE prises en charge](#surcharges-des-de-prises-en-charge)
- [Scripts d'événements](#scripts-dévénements)
- [Fichiers et cache](#fichiers-et-cache)
- [Codes de sortie](#codes-de-sortie)
- [Limites connues](#limites-connues)
- [Dépannage](#dépannage)
- [Tests](#tests)

## Prérequis

- Linux.
- Python 3.8 ou plus récent. Aucune bibliothèque externe n'est nécessaire.
- Optionnel : `python-magic` ou la commande `file`, utilisés pour la détection par type MIME.
- ES-DE 3.x installé et configuré au moins une fois (dossier de données `~/ES-DE`).

## Installation

```bash
install -m 755 es-de-launch ~/.local/bin/es-de-launch
es-de-launch --detect-install     # vérifier ce qui est trouvé
es-de-launch --init               # écrire ~/.config/es-de-launch.conf
```

`--init` est aussi exécuté automatiquement au premier lancement si la conf est absente.

## Utilisation

```
es-de-launch [OPTIONS] FICHIER        lance le jeu
es-de-launch --output-cmd FICHIER     affiche la commande, sans rien exécuter
es-de-launch --info FICHIER           informations sur la ROM
es-de-launch --copy FICHIER           range la ROM dans la bibliothèque
es-de-launch --init [--force]         détecte l'installation et écrit la conf
es-de-launch --detect-install         liste les installations ES-DE trouvées
es-de-launch --show-conf              affiche la conf effective
```

`~` est accepté même entre guillemets : `es-de-launch "~/ROMs/psx/Jeu.chd"`.

### Exemples

```bash
# Voir ce qui serait lancé, et depuis quel dossier
es-de-launch --output-cmd ~/ROMs/gc/"Super Mario Sunshine.rvz"

# Forcer un autre émulateur du système (label de es_systems.xml)
es-de-launch --emulator "Snes9x - Current" ~/ROMs/snes/jeu.sfc

# ROM téléchargée : quel système, quel émulateur, où la ranger ?
es-de-launch --info ~/Téléchargements/jeu.iso
es-de-launch --info --json ~/Téléchargements/jeu.iso

# La ranger (copie ; --move pour déplacer, --dry-run pour simuler)
es-de-launch --copy --fix-ext ~/Téléchargements/"Mario Kart.64"
```

### Options

| Option | Rôle |
| --- | --- |
| `--conf FICHIER` | Autre fichier de conf que `~/.config/es-de-launch.conf` |
| `--config-dir DIR` | Dossier de données ES-DE (défaut : conf, sinon `~/ES-DE`) |
| `--resources-dir DIR` | Dossier contenant `es_systems.xml` et `es_find_rules.xml` |
| `--system NOM` | Force le système (`n64`, `psx`…) |
| `--emulator LABEL` | Force une commande du système |
| `--scripts` / `--no-scripts` | Force ou désactive les scripts d'événements |
| `--json` | Sortie JSON pour `--info`, `--output-cmd`, `--detect-install` |
| `--move`, `--dry-run`, `--overwrite`, `--rename`, `--fix-ext` | Options de `--copy` |
| `--force` | Avec `--init` : remplace la conf (l'ancienne est gardée en `.bak`) |
| `--refresh-cache` | Réextrait les XML de l'AppImage |
| `-y`, `--yes` | Aucune question : un choix ambigu devient une erreur |
| `-v`, `--verbose` | Trace la résolution ; la sortie de l'émulateur va au terminal |

## Fonctionnement

1. **Configuration.** Lecture de la conf du lanceur, puis des fichiers d'ES-DE : réglages, systèmes, règles de recherche des émulateurs, gamelists.
2. **Système.** Si le fichier est dans le dossier d'un système et que son extension y est déclarée, c'est ce système, comme dans le menu. Sinon, la détection s'appuie, dans l'ordre, sur les dossiers déclarés dans la conf, les signatures d'en-tête, le type MIME, puis l'extension seule.
3. **Émulateur.** L'ordre de priorité est le même que dans ES-DE : `--emulator`, puis l'émulateur alternatif du jeu, puis celui du système (choisis dans ES-DE, lus dans le gamelist), puis la première commande du système.
4. **Commande.** Remplacement des variables d'ES-DE (`%EMULATOR_X%`, `%CORE_X%`, `%ROM%`, `%STARTDIR%`, `%INJECT%`…) et recherche des binaires avec les règles d'ES-DE.
5. **Lancement.** Scripts `game-start`, puis l'émulateur via `/bin/sh -c`, puis scripts `game-end`. Ces derniers sont exécutés même si l'émulateur échoue ou est interrompu.

### Détection hors bibliothèque

| Niveau | Sources |
| --- | --- |
| Certain | `--system` ; chemin dans la bibliothèque avec extension déclarée ; signature décisive : `SYSTEM.CNF` (PS1/PS2), `PS3_GAME`, disque GameCube ou Wii, Saturn, Dreamcast, Mega-CD |
| Probable | Section `[folders]` ; en-têtes N64, NES, FDS, Game Boy, GBA, DS, 3DS, Switch, Mega Drive, 32X, Master System ; type MIME ; contenu d'un zip |
| Faible | Extension seule |

Seuls les candidats du meilleur niveau sont gardés. Les priorités de la conf départagent ensuite les égalités, sans jamais contredire un résultat certain. Les fichiers `.cue` sont analysés à travers le premier fichier qu'ils référencent.

## Configuration : es-de-launch.conf

Format INI, dans `~/.config/es-de-launch.conf` (ou `$XDG_CONFIG_HOME`). `~` et `$VAR` sont développés.

```ini
[install]
# appimage | system | local | portable | flatpak | snap | manual | auto
type = appimage
executable = /home/flav/.local/bin/es-de
config_dir = /home/flav/ES-DE
# auto = extrait de l'AppImage ou trouvé automatiquement
resources_dir = auto
# vide = ~/.cache/es-de-launch
cache_dir =

[launch]
# always | auto (suit le réglage CustomEventScripts d'ES-DE) | never
scripts = always
# ask | first | fail : que faire s'il reste une égalité
ambiguity = ask

[systems]
# ordre global de préférence ; --init le génère d'après le nombre de jeux
priority = n64, snes, megadrive, gba, nds, psx, ps2, gc, wii
# systèmes à ne jamais proposer
exclude = n64dd

[extensions]
# extension = systèmes candidats, du plus au moins prioritaire
iso = ps2, gc, wii, psx
zip = n64, snes, megadrive, gba

[folders]
# dossier hors bibliothèque = système
~/Téléchargements/ps2 = ps2

[mime]
# surcharge de la table interne type MIME -> système
application/x-n64-rom = n64
```

## Types d'installation d'ES-DE

`es-de-launch --detect-install` liste les installations trouvées et indique celle qui sera utilisée.

| Type | Détecté par | XML lus dans |
| --- | --- | --- |
| `appimage` | Signature AppImage, ou exécutable contenant une image squashfs/DwarFS, ou nom en `.AppImage` | L'image, extraits dans le cache |
| `system` | `/usr/bin/es-de` (paquet .deb, .rpm, AUR) | `/usr/share/es-de/resources/systems/linux` |
| `local` | `/usr/local/bin/es-de`, `~/.local/bin/es-de` (compilation) | `<préfixe>/share/es-de/resources/systems/linux` |
| `portable` | Binaire avec un dossier `resources/` à côté | `<dossier du binaire>/resources/systems/linux` |
| `flatpak` | Ressources sous `/var/lib/flatpak/app/*` ou `~/.local/share/flatpak/app/*` | L'installation Flatpak |
| `snap` | Ressources sous `/snap/*/current` | Le snap |
| `manual` | `--resources-dir`, ou `resources_dir` dans la conf | Le dossier indiqué |

Ordre de recherche : options de la ligne de commande, conf, puis journal d'ES-DE (`logs/es_log.txt`, qui indique le fichier réellement chargé), puis recherche dans le `PATH` et les emplacements usuels. Si une installation n'a pas de ressources lisibles, la suivante est essayée.

Pour Flatpak et Snap, les commandes d'ES-DE sont prévues pour son bac à sable ; leur exécution depuis l'hôte peut différer, et un avertissement le rappelle.

## Surcharges d'ES-DE prises en charge

| Surcharge | Prise en charge |
| --- | --- |
| `custom_systems/es_systems.xml` | Oui. Un système de même nom remplace entièrement le système embarqué ; un nouveau système est ajouté. |
| `custom_systems/es_find_rules.xml` | Oui. Un émulateur ou un cœur de même nom remplace la définition embarquée ; les autres restent. |
| `ROMDirectory` (réglages d'ES-DE) | Oui, `~` compris ; défaut `~/ROMs`. |
| Émulateur alternatif du système | Oui (`<alternativeEmulator>` du gamelist). |
| Émulateur alternatif du jeu | Oui (`<altemulator>` du jeu dans le gamelist). |
| Fichiers `%INJECT%` (`.esprefix`, `.commands`…) | Oui, lus dans le dossier du jeu (4 Kio maximum). |
| `CustomEventScripts` | Oui si `scripts = auto` dans la conf. |
| Dossier de données déplacé | Par `--config-dir`, la conf, ou la variable `ESDE_APPDATA_DIR`. |
| Gamelists rangés dans les dossiers de ROMs | Non, pas encore. |

Un XML personnalisé mal formé produit une erreur claire (code 3) qui nomme le fichier.

## Scripts d'événements

Les exécutables de `~/ES-DE/scripts/game-start/` puis `game-end/` sont lancés dans l'ordre alphabétique, un par un, avec les mêmes arguments qu'ES-DE :

```
<chemin de la ROM> <nom du jeu> <nom du système> <nom complet du système>
```

Le nom du jeu vient du gamelist ; à défaut, c'est le nom du fichier sans extension. Les fichiers non exécutables sont ignorés (`common.conf`, par exemple). Variables d'environnement ajoutées : `ESDE_LAUNCHER=es-de-launch`, `ESDE_SYSTEM`, `ESDE_EMULATOR_LABEL` et `ESDE_DETECTION_METHOD`. Un script peut ainsi savoir qu'il n'est pas appelé par ES-DE.

## Fichiers et cache

| Emplacement | Contenu |
| --- | --- |
| `~/.config/es-de-launch.conf` | Conf du lanceur |
| `~/.cache/es-de-launch/resources/` | XML extraits de l'AppImage et `manifest.json` |
| `~/.cache/es-de-launch/last_run.log` | Commande et sortie du dernier émulateur (hors `-v`) |
| `~/.cache/es-de-launch/lock` | Verrou contre deux lancements simultanés |

Le cache AppImage est refait quand l'exécutable (cible finale des liens symboliques) change de taille ou de date, ou est plus récent que le cache. L'extraction monte l'AppImage (`--appimage-mount`), copie les deux XML, puis arrête son montage, sans toucher à un ES-DE en cours d'exécution. Si le montage échoue, elle se rabat sur `--appimage-extract`.

## Codes de sortie

| Code | Signification |
| --- | --- |
| 0 | Succès |
| 1 | Erreur générique |
| 2 | Usage incorrect, fichier introuvable |
| 3 | Configuration ES-DE introuvable ou invalide |
| 4 | Système indéterminé ou ambigu |
| 5 | Émulateur, cœur ou commande inutilisable |
| 6 | `--copy` : la destination existe avec un contenu différent |

Une fois l'émulateur lancé, son propre code de sortie est renvoyé.

## Limites connues

- **Ce qu'ES-DE fait en plus n'est pas reproduit** : écran de lancement, compteur de parties et date de dernière partie dans le gamelist.
- **L'échappement de `%ROM%` est reconstitué**, pas repris du code d'ES-DE. Il reste à le comparer aux commandes réelles.
- **Sens supposé de certaines variables** : `%GAMEENTRYDIR%` et l'extension `.` (xbox360) sont interprétés au mieux.
- **Sans signature décisive**, les formats compressés (`.chd`, `.rvz`, `.7z`) sont détectés par leur extension seulement.
- **Les emplacements Flatpak et Snap** n'ont pas été vérifiés sur de vraies installations.

## Dépannage

| Symptôme | Piste |
| --- | --- |
| « ressources ES-DE introuvables » | `es-de-launch --detect-install`, puis `--resources-dir` ou `[install]` dans la conf |
| Mauvais système détecté | `es-de-launch --info FICHIER` montre la méthode et les autres candidats ; ajuster `[extensions]`, `[systems]` ou `[folders]` |
| Émulateur introuvable | Le message liste les règles testées ; ajouter une règle dans `~/ES-DE/custom_systems/es_find_rules.xml` |
| L'émulateur se ferme aussitôt | Lire `~/.cache/es-de-launch/last_run.log`, ou relancer avec `-v` |
| AppImage mise à jour non prise en compte | `--refresh-cache` |

## Tests

```bash
python3 -m unittest discover -s tests -v
```

Les tests utilisent les `es_systems.xml` et `es_find_rules.xml` réels (`tests/fixtures/`), un dossier personnel temporaire, des émulateurs et une AppImage factices, et une arborescence système simulée pour les types d'installation. Ils ne touchent ni à `~/ES-DE` ni à `~/ROMs`.
