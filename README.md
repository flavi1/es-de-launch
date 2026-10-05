# es-de-launch

Lance un jeu exactement comme le menu d'[ES-DE](https://es-de.org), à partir d'un simple nom de fichier : même système, même émulateur (y compris les émulateurs alternatifs choisis dans ES-DE), même commande, mêmes scripts `game-start` / `game-end`.

```bash
es-de-launch "~/ROMs/n64/Mario Kart 64.z64"
```

Il sait aussi lancer une ROM rangée hors de la bibliothèque, en détectant son système, et peut la ranger au bon endroit.

> Statut : version 0.2. Le code de construction des commandes est transcrit de celui d'ES-DE 3.5.0 (`FileData::launchGame()`, `findEmulator()`, `Scripting::fireEvent()`) et couvert par 121 tests. Il reste à le valider contre les commandes réellement lancées par ES-DE sur une vraie installation (voir [Valider sur votre installation](#valider-sur-votre-installation)).

## Sommaire

- [Prérequis](#prérequis)
- [Installation](#installation)
- [Utilisation](#utilisation)
- [Fonctionnement](#fonctionnement)
- [Configuration : es-de-launch.conf](#configuration--es-de-launchconf)
- [Types d'installation d'ES-DE](#types-dinstallation-des-de)
- [Surcharges d'ES-DE prises en charge](#surcharges-des-de-prises-en-charge)
- [Conformité à ES-DE](#conformité-à-es-de)
- [Valider sur votre installation](#valider-sur-votre-installation)
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
3. **Émulateur.** Même logique que dans ES-DE : `--emulator`, sinon l'émulateur alternatif du jeu (si le réglage `AlternativeEmulatorPerGame` est actif, ce qui est le cas par défaut), sinon celui du système, sinon la première commande. Une étiquette invalide renvoie à la commande par défaut, avec un avertissement.
4. **Commande.** Variables et règles de recherche traitées dans le même ordre et de la même façon qu'ES-DE (voir [Conformité à ES-DE](#conformité-à-es-de)).
5. **Lancement.** Scripts `game-start`, puis `cd <%STARTDIR%> && <commande>` via `/bin/sh -c`, puis scripts `game-end`. Ces derniers sont exécutés même si l'émulateur échoue ou est interrompu.

`--output-cmd` affiche exactement ce qui est exécuté. Sans `%STARTDIR%`, c'est la ligne « Expanded emulator launch command » du journal d'ES-DE (`~/ES-DE/logs/es_log.txt`). Avec `%STARTDIR%`, elle est précédée de `cd <dossier> &&`, comme dans ES-DE.

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
# vide = $HOME (ou le dossier indiqué par portable.txt à côté de l'exécutable)
home =

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

La version Flatpak d'ES-DE exécute ses commandes sur l'hôte (`flatpak-spawn --host`), comme le lanceur : son comportement est donc reproduit. Aucune version Snap n'est prévue par le code d'ES-DE ; un avertissement le rappelle.

## Surcharges d'ES-DE prises en charge

Toutes les surcharges prévues par ES-DE 3.5 sont lues, avec les mêmes règles de priorité.

| Surcharge | Comportement (identique à ES-DE) |
| --- | --- |
| `~/ES-DE/custom_systems/es_systems.xml` | Lu en premier ; un système de même nom remplace entièrement le système embarqué ; un nouveau système est ajouté. `<loadExclusive/>` désactive le fichier embarqué. |
| `~/ES-DE/custom_systems/es_find_rules.xml` | Lu en premier ; un émulateur ou un cœur de même nom remplace entièrement la définition embarquée. Un fichier mal formé est ignoré avec un avertissement. |
| `~/ES-DE/resources/systems/linux/es_systems.xml` ou `es_find_rules.xml` | Remplace le fichier embarqué correspondant, fichier par fichier (`linuxarm` sur ARM 64 bits). |
| `ROMDirectory` (réglages d'ES-DE) | `~` et `%ESPATH%` développés ; défaut `<home>/ROMs/`. |
| `AlternativeEmulatorPerGame`, `RunInBackground`, `CustomEventScripts`, `LegacyGamelistFileLocation` | Lus dans `es_settings.xml`, avec les valeurs par défaut d'ES-DE. |
| Émulateur alternatif du système / du jeu | `<alternativeEmulator>` (à l'intérieur ou à l'extérieur de `<gameList>`) et `<altemulator>`. |
| Gamelist dans le dossier de ROMs | Utilisé si `LegacyGamelistFileLocation` est actif, comme dans ES-DE. |
| Dossier de données | `$ESDE_APPDATA_DIR`, sinon `<home>/ES-DE` ; `--config-dir` ou la conf le remplacent. |
| `portable.txt` à côté de l'exécutable | Change le dossier personnel vu par ES-DE (et donc `~`) ; aussi réglable par `home` dans `[install]`. |

Un `es_systems.xml` personnalisé mal formé arrête le chargement, comme dans ES-DE (code 3).

## Scripts d'événements

Comme ES-DE, le lanceur exécute toutes les entrées de `~/ES-DE/scripts/game-start/` puis `game-end/`, triées par nom en respectant la casse. Chaque script est lancé par le shell avec la ligne :

```
"<script>" "<chemin de la ROM échappé>" "<nom du jeu>" "<nom du système>" "<nom complet du système>"
```

Ces trois points reproduisent fidèlement ES-DE, y compris dans ses particularités :

- **Le chemin de la ROM est celui de `%ROM%`.** Il est donc échappé : un espace y apparaît précédé d'une barre oblique inverse. Pour un raccourci `.desktop`, c'est la ligne `Exec=`.
- **Le bit d'exécution n'est pas vérifié.** Un fichier non exécutable dans ces dossiers produit une erreur « Permission denied » sans arrêter le lancement.
- **Le nom du jeu vient du gamelist.** À défaut, c'est le nom du fichier sans extension.

La clé `scripts` de la conf choisit la politique : `always` (défaut), `auto` (suit le réglage `CustomEventScripts` d'ES-DE, désactivé par défaut dans ES-DE) ou `never`. Variables d'environnement ajoutées : `ESDE_LAUNCHER=es-de-launch`, `ESDE_SYSTEM`, `ESDE_EMULATOR_LABEL`, `ESDE_DETECTION_METHOD`.

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

## Conformité à ES-DE

La construction de la commande suit `FileData::launchGame()` d'ES-DE 3.5.0, étape par étape :

| Point | Comportement reproduit |
| --- | --- |
| `%ROM%` | Chemin échappé par une barre oblique inverse devant `\ ' " ! $ ^ & * ( ) { } [ ] ? ;` `<` `>` et l'espace, quel que soit le contexte (même entre guillemets) |
| `%BASENAME%`, `%FILENAME%`, `%ROMRAW%`, `%GAMEDIRRAW%`, `%ESPATH%` | Jamais échappés |
| `%ROMRAWWIN%` | Chemin avec des `\` à la place des `/`, sans lettre de lecteur |
| `%ROMPATH%` | Dossier des ROMs échappé, **avec** sa barre finale (d'où `ROMs//adam` dans certaines commandes) |
| `%GAMEDIR%`, `%EMUDIR%` | Dossier du jeu, dossier de l'émulateur, échappés |
| `~` | Remplacé partout dans la commande, avant les autres variables |
| Règles de recherche | Tous les `systempath` (PATH), puis tous les `staticpath` (`~`, `%ESPATH%`, `%ROMPATH%`, joker `*`, forme `chemin|commande`). Le bit d'exécution n'est pas vérifié. Sans `%EMULATOR_X%`, le premier mot de la commande doit exister. |
| Cœurs | `%CORE_X%/fichier`, éventuellement entre guillemets ; chemin échappé seulement s'il contient une espace ; `%EMUPATH%` pris en charge |
| `%STARTDIR%=…` | Valeur simple ou entre guillemets ; `%EMUDIR%`, `%GAMEDIR%`, `%GAMEENTRYDIR%` ; dossier créé au lancement s'il n'existe pas |
| `%INJECT%=…` | Valeur simple ou entre guillemets, seul `%BASENAME%` développé ; lignes concaténées sans séparateur ; ignoré au-delà de 4 096 octets |
| Raccourcis `.desktop` | Avec `%ENABLESHORTCUTS%` : la dernière ligne `Exec=` (codes `%f`, `%U`… retirés) remplace l'émulateur et `%ROM%` ; `Path=` seulement sans `%STARTDIR%` |
| Dossier interprété comme fichier | Si un fichier du même nom est à l'intérieur, c'est lui qui est lancé |
| Extensions | Comparées en respectant la casse ; `.` désigne un fichier sans extension |
| Variable inconnue | Laissée telle quelle, avec un avertissement (`%RPCS3_GAMEID%` est une syntaxe de RPCS3, pas d'ES-DE) |

### Différences volontaires

- **Le lanceur attend la fin de l'émulateur et renvoie son code de sortie.** ES-DE lance la commande en arrière-plan (`2>&1 &`) et lit sa sortie jusqu'à la fin, sans connaître le code de l'émulateur.
- **`%RUNINBACKGROUND%` ne change rien pour le lanceur.** Le lanceur attend aussi la fin du processus, puis exécute `game-end`. ES-DE, lui, n'attend pas et reporte `game-end` au retour dans son interface.
- **Le gamelist n'est pas modifié.** ES-DE met à jour le nombre de parties, la date de la dernière partie et le temps de jeu. Écrire dans le gamelist pendant qu'ES-DE tourne risquerait d'être écrasé par ES-DE.
- **L'écran de lancement, la mise en veille du rendu et l'interface ne sont pas reproduits.**
- **Pour une AppImage, `%ESPATH%` est déduit du dernier journal d'ES-DE.** C'est un point de montage temporaire ; aucune commande embarquée ne l'utilise.

## Valider sur votre installation

1. Lancez un jeu depuis ES-DE, puis repérez dans `~/ES-DE/logs/es_log.txt` la ligne qui suit « Expanded emulator launch command: ».
2. Lancez `es-de-launch --output-cmd <même ROM>` et comparez les deux commandes. Avec `%STARTDIR%`, seul le préfixe `cd … &&` doit différer.
3. Faites-le pour un jeu de chaque système, et signalez tout écart.

## Limites connues

- **Détection du système hors bibliothèque** : sans signature décisive, un fichier dont l'extension est partagée (`.chd` de CD, `.bin` sans en-tête, `.zip`) dépend des priorités de la conf.
- **Contenu des `.chd`** : il n'est pas lu (codecs de MAME), seulement leurs métadonnées.
- **Snap** : aucune version Snap n'est prévue par le code d'ES-DE ; la détection existe, mais n'est pas garantie.

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
