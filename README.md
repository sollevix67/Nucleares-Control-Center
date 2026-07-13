# NUCLEARES Control Center

Application locale en français réunissant les trois fonctions demandées pour le jeu **Nucleares** :

- tableau de bord temps réel et historique SQLite ;
- alarmes visuelles et sonores avec acquittement ;
- pilotage automatique des équipements exposés par le webserveur du jeu.
- supervision détaillée des réservoirs et des générateurs principaux/de secours.
- suivi du xénon et de l’iode avec protection automatique des rampes de puissance.

Elle fonctionne avec le même code sous **Windows 11** et **Ubuntu 24.04**. Elle n’utilise aucune bibliothèque Python externe.

> Cette application commande uniquement le simulateur de jeu Nucleares. Elle n’est ni conçue ni homologuée pour une installation réelle. Projet indépendant, non affilié à Aerilian Games.

## Démarrage rapide sous Windows 11

1. Installer Python 3.11 ou plus récent depuis <https://www.python.org/downloads/windows/> en cochant **Add Python to PATH**. Le lanceur accepte aussi bien la commande `python` que le lanceur `py -3`.
2. Lancer Nucleares et charger une partie.
3. Sur la tablette du joueur, ouvrir **Status**, puis cliquer sur **Start webserver**.
4. Double-cliquer de préférence sur `start_windows.pyw`, qui démarre directement avec Python sans utiliser `cmd.exe`. Le fichier `start_windows.bat`, désormais réduit à une seule commande, reste disponible si l’association des fichiers `.pyw` n’est pas installée.
5. Le tableau de bord s’ouvre à l’adresse <http://127.0.0.1:8790/>.
6. Vérifier les mesures, puis activer **Pilotage automatique**. Une confirmation est demandée.

## Démarrage rapide sous Ubuntu 24.04

```bash
chmod +x start_linux.sh
./start_linux.sh
```

Puis ouvrir <http://127.0.0.1:8790/>.

Si le jeu tourne sur un autre PC Windows, remplacer `game_url` dans `config.json` par l’adresse de ce PC, par exemple `http://192.168.1.50:8786/`. Voir la section « Jeu Windows, application Ubuntu » ci-dessous.

## Mode démonstration

Le simulateur inclus permet d’essayer le tableau de bord sans lancer le jeu :

- Windows : `start_demo_windows.bat` ;
- Linux : `chmod +x start_demo_linux.sh && ./start_demo_linux.sh`.

Le simulateur imite la découverte des variables, la télémétrie, les commandes POST et une dynamique simplifiée du réacteur. Il sert exclusivement aux essais logiciels.
Pour simuler également un module chimique installé, lancer `python mock_game.py --chemistry` avant `python app.py`.

## Fonctions du pilote automatique

| Zone | Régulation |
| --- | --- |
| Cœur | Température par position des barres, prise en compte de la criticité |
| Sécurité | SCRAM automatique au seuil critique, pompes primaires à 90 % |
| Production | Répartition de la demande réseau entre les groupes disponibles |
| Turbines | Régulation progressive de la puissance par les MSCV et maintien du bypass fermé à 0 % |
| Secondaire | Débit des pompes selon la vapeur et le niveau des générateurs |
| Condenseur | Remplissage entre 45 et 60 %, pompe à vide, circulation à 25 % |
| Rétention | Vidange automatique entre 75 et 50 % |
| Pressuriseur | Commande de la vanne motorisée entre 50 et 60 % |
| Primaire | Appoint d’eau entre 80 et 90 % |
| Chimie | Détection optionnelle du module, maintien du bore par dosage/filtration, sécurités des pompes |
| Poisons neutroniques | Suivi xénon/iode, alarme relative et ralentissement des rampes de puissance |

Le pilote ne commande que les variables annoncées comme accessibles en écriture par la version courante du jeu. Pour les circuits indexés, cette vérification est complétée par `STEAM_TURBINE_*_INSTALLED` et par l’état du générateur de vapeur. Une tranche marquée `NOT_INSTALLED` ne reçoit aucune commande MSCV ou bypass. Les pompes primaires et secondaires sont filtrées séparément par leur propre état : l’absence d’une pompe ne désactive donc plus par erreur une turbine installée. Une commande absente est ignorée et inscrite dans le journal. Les opérations qui exigent encore une interaction physique du personnage dans le jeu ne peuvent pas être automatisées par le webserveur.

En suivi réseau, les MSCV modulent la puissance vers la demande augmentée de la marge configurée. Les bypass de turbine ne servent pas de régulateur continu : ils sont explicitement maintenus à `0 %` pour envoyer la vapeur disponible vers la turbine. Pour viser exactement la demande affichée, régler **Marge réseau** à `0 MW`.

### Réservoirs et générateurs dans Supervision

La page **Supervision** est organisée en six sous-onglets : **Synthèse**, **Réacteur**, **Production**, **Fluides**, **Chimie** et **Secours**. Chaque zone conserve uniquement les panneaux utiles. Le sous-onglet Chimie respecte la détection du module : ses réservoirs de produits ne sont pas affichés lorsqu’il n’est pas installé.

La page **Supervision** affiche les niveaux disponibles du condenseur, du circuit primaire, du pressuriseur, du réservoir de refroidissement primaire, de la piscine du cœur, du réservoir externe et de la rétention. Les valeurs sont présentées en pourcentage lorsque la capacité est connue, sinon en litres. Le webserveur ne publiant pas la capacité maximale de la piscine et du réservoir externe, leurs jauges utilisent des références configurables de 100 000 L et 200 000 L. Ces échelles peuvent être modifiées dans **Réglages**.

Les trois générateurs principaux indiquent leur état de couplage, leur disjoncteur, leur puissance, leur vitesse et leur fréquence. Les deux groupes électrogènes de secours sont toujours affichés avec un état explicite **INSTALLÉ** ou **NON INSTALLÉ**. Lorsqu’un groupe est installé, son état de marche, son mode, son carburant en litres, son pressuriseur et ses besoins de maintenance sont également indiqués. Les principaux états anglais ou espagnols renvoyés par le jeu sont traduits en français. Le groupe de secours 2 est considéré non installé par défaut ; son état peut être forcé sur **Auto**, **Installé** ou **Non installé** dans Réglages si sa configuration change dans la partie.

Certaines versions du webserveur renvoient un code numérique à la place du texte pour les états lors d’une lecture groupée. L’application relit individuellement ces champs avant de déterminer l’installation des groupes. Une variable présente avec des valeurs de remplacement à zéro n’est donc plus considérée, à elle seule, comme une preuve que l’équipement est installé.

### Acquittement des alarmes

Le bouton **Alarmes** est rouge lorsqu’au moins une alarme reste à acquitter, vert lorsque toutes les alarmes actives sont acquittées, et neutre en l’absence d’alarme. Les boutons individuels utilisent le même code couleur. La page Alarmes comporte également un bouton **Tout acquitter** ; l’acquittement ne supprime pas l’alarme, qui disparaît uniquement lorsque sa condition revient à la normale.

### Transformateurs et bancs de résistances

La zone **Production** affiche trois chemins électriques : transformateur de production, transformateur du réseau externe et transformateur de secours. Le webserveur ne publie pas directement l’état interne, la tension ni la température des transformateurs. L’application indique donc explicitement une **télémétrie indirecte** et déduit leur mise sous tension à partir de `POWER_FROM_TURBINE_KW`, `POWER_FROM_EXTERNAL_KW`, `EMERGENCY_GENERATOR_POWER_OUTPUT_KW` et `EMERGENCY_BATTERIES_POWER_OUTPUT_KW`.

Le panneau des bancs de résistances affiche l’interrupteur général, les quatre bancs, la capacité d’absorption active, l’énergie effectivement dérivée, le surplus disponible et le taux de charge. Il utilise les variables `RESISTOR_BANKS_MAIN_SWITCH`, `RESISTOR_BANK_01_SWITCH` à `RESISTOR_BANK_04_SWITCH`, `RES_ABSORPTION_CAPACITY_MW`, `RES_EFFECTIVELY_DERIVED_ENERGY_MW` et `RES_DIVERT_SURPLUS_FROM_MW`.

Les pourcentages du jeu sont normalisés automatiquement : une valeur de vide condenseur comprise entre `0` et `1` est interprétée comme une fraction (`1,0 = 100 %`), tandis qu’une valeur déjà comprise sur l’échelle `0–100` est conservée.

### Module chimique optionnel

Une partie peut être lancée sans le module chimique. L’application distingue automatiquement les variables absentes, les pompes non installées, la lecture seule, les défauts et le module prêt. Un module absent ne produit aucune alarme et ne bloque aucune autre zone du pilote.

Le camion chimique n’est pas requis pour utiliser l’acide déjà présent dans le réservoir local. Ses variables sont affichées à titre informatif, mais elles ne bloquent ni le dosage ni la filtration. La zone des réservoirs chimiques n’apparaît que lorsque le module est installé. Le niveau du réservoir d’acide n’étant pas exposé par la liste actuelle du webserveur, elle le signale clairement ; toute future variable `CHEM_*_LEVEL`, `VOLUME`, `TANK` ou `RESERVOIR` sera détectée et affichée automatiquement.

Lorsque le module est prêt, le pilote utilise exclusivement les commandes POST `CHEM_BORON_DOSAGE_ORDERED_RATE` et `CHEM_BORON_FILTER_ORDERED_SPEED`, limitées à la plage `0–100 %`. Le dosage et la filtration sont mutuellement exclusifs. Une pompe à sec, en surcharge, à maintenir ou privée d’énergie provoque l’arrêt des commandes chimiques.

Si la consigne de bore est laissée vide, la concentration `CHEM_BORON_PPM` présente lors de l’activation est capturée et maintenue. Cela évite d’imposer une valeur arbitraire à une partie existante. Une consigne explicite, une bande morte et une puissance maximale peuvent être configurées dans **Réglages**.

### Xénon et iode

Les mesures `CORE_IODINE_GENERATION`, `CORE_IODINE_CUMULATIVE`, `CORE_XENON_GENERATION` et `CORE_XENON_CUMULATIVE` sont affichées dans Supervision avec leur tendance et un historique de 30 minutes. L’application apprend une référence au démarrage, puis déclenche un avertissement ou une alarme critique lorsque le xénon dépasse les rapports configurés.

Ces variables sont en lecture seule : la gestion est donc indirecte. Lorsque leur hausse devient rapide ou que le seuil xénon est dépassé, le pilote ralentit la variation de la cible réseau et de la consigne thermique afin d’éviter les changements brusques. Il n’utilise jamais `FUN_XENON_SPILL` ni `FUN_IODINE_SPILL`, qui sont des événements de jeu et non des commandes normales de conduite. Les seuils et les rampes sont réglables, et la protection peut être désactivée séparément.

## Réglages importants

Ils sont accessibles depuis l’onglet **Réglages** et conservés dans `config.json` :

- adresse du serveur du jeu ;
- périodes de lecture et de commande ;
- température cible du cœur ;
- marge de production au-dessus de la demande ;
- consigne de bore facultative, bande morte et puissance chimique maximale ;
- seuils xénon/iode et rampes de puissance/thermiques de protection ;
- activation individuelle de chaque zone automatique.

Le pilotage automatique est volontairement arrêté à chaque lancement (`auto_start: false`). Pour le démarrer immédiatement, cette valeur peut être changée manuellement, mais ce n’est pas recommandé pendant les premiers essais.

## Avertissement heuristique de l’antivirus

Certains antivirus peuvent considérer un fichier batch téléchargé et peu répandu comme suspect lorsqu’il lance un interpréteur. Le lanceur principal recommandé est donc `start_windows.pyw`, qui ne passe pas par `cmd.exe`. Le batch alternatif contient uniquement :

```bat
@echo off
python "%~dp0app.py"
```

Si Norton bloque encore le fichier, ne désactivez pas la protection globale. Exécutez LiveUpdate et une analyse complète, puis soumettez le fichier au portail Norton comme faux positif. Vous pouvez également démarrer l’application depuis un terminal PowerShell déjà ouvert avec `python app.py`.

## Jeu Windows, application Ubuntu

Le serveur Nucleares peut n’écouter que sur `localhost`. Dans ce cas, sur le PC Windows, ouvrir **Terminal Windows en administrateur** et créer un relais sur le port 8786 :

```powershell
netsh interface portproxy add v4tov4 listenaddress=0.0.0.0 listenport=8786 connectaddress=127.0.0.1 connectport=8785
netsh advfirewall firewall add rule name="Nucleares Webserver 8786" dir=in action=allow protocol=TCP localport=8786 profile=private
```

Dans l’application Ubuntu, utiliser `http://ADRESSE_IP_WINDOWS:8786/` comme adresse du jeu. Ne pas ouvrir ce port vers Internet.

Pour supprimer ultérieurement le relais :

```powershell
netsh interface portproxy delete v4tov4 listenaddress=0.0.0.0 listenport=8786
netsh advfirewall firewall delete rule name="Nucleares Webserver 8786"
```

## Docker sous Ubuntu

Dans `config.json`, mettre `http://host.docker.internal:8785/` si le jeu ou un relais tourne sur la machine hôte. Puis :

```bash
docker compose up -d --build
```

Le tableau de bord reste limité à la machine locale à l’adresse <http://127.0.0.1:8790/>.

## Création d’un exécutable

Les exécutables doivent être compilés sur leur plateforme cible :

- Windows : double-cliquer sur `build_windows.bat` ;
- Ubuntu : `chmod +x build_linux.sh && ./build_linux.sh`.

Le résultat est placé dans `dist/Nucleares-Control-Center`. Le format `onedir` conserve le dossier `static` nécessaire à l’interface.

## Tests

```bash
python3 -m unittest discover -s tests -v
```

Les tests utilisent le simulateur local et n’envoient aucune commande au jeu.

## Architecture

- `app.py` : client du jeu, télémétrie, alarmes, historique, contrôleurs et serveur du tableau de bord ;
- `static/` : interface HTML/CSS/JavaScript sans service externe ;
- `mock_game.py` : simulateur du webserveur pour les essais ;
- `data/telemetry.sqlite3` : historique généré automatiquement ;
- `config.json` : réglages persistants.

## Références techniques

Le protocole public du jeu utilise notamment `WEBSERVER_LIST_VARIABLES`, `WEBSERVER_BATCH_GET`, les commandes POST et `VALVE_PANEL_JSON`. Le projet Python communautaire [NuCon](https://git.dominik-roth.eu/dodox/NuCon) a servi de référence de compatibilité pour les noms récents de variables, les états des pompes et les plages de conduite. [LibNuclearesWeb](https://github.com/ggppjj/LibNuclearesWeb) documente une approche objet équivalente pour .NET 8/9. Ces deux projets sont sous licence MIT ; aucun de leur code n’est inclus dans cette application et aucune dépendance externe n’est nécessaire.
