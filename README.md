# WattKeeper - Stiebel Eltron ISG Modbus

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=crapitouille&repository=ha-stiebeleltron-isg&category=integration)
[![Add integration to my Home Assistant](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=stiebel_isg_modbus)
[![Tests](https://github.com/crapitouille/ha-stiebeleltron-isg/actions/workflows/tests.yml/badge.svg)](https://github.com/crapitouille/ha-stiebeleltron-isg/actions/workflows/tests.yml)

Pilote une pompe à chaleur **Stiebel Eltron** équipée d'un **ISG** via **Modbus TCP**,
sans YAML : tout se configure depuis l'interface de Home Assistant.

Cette intégration reprend, à l'identique, la configuration du module `modbus:` de
`configuration.yaml` (mêmes registres, mêmes plages de consigne, mêmes modes).

## Installation

### Avec HACS (recommandé)

1. Cliquez sur le bouton **HACS** ci-dessus pour ajouter ce dépôt (catégorie *Intégration*),
   puis téléchargez **WattKeeper - Stiebel Eltron ISG Modbus**.
2. Redémarrez Home Assistant.
3. Cliquez sur le bouton **Add integration** ci-dessus (ou *Paramètres → Appareils et services →
   Ajouter une intégration → « WattKeeper - Stiebel Eltron ISG Modbus »*).

> Sans les boutons : HACS → ⋮ → *Dépôts personnalisés* → URL
> `https://github.com/crapitouille/ha-stiebeleltron-isg`, catégorie *Intégration*.

### Manuelle

Copiez le dossier `custom_components/stiebel_isg_modbus` dans le dossier `custom_components`
de Home Assistant, puis redémarrez.

## Configuration

| Champ | Valeur par défaut | Rôle |
|---|---|---|
| Adresse IP / hôte | — | Adresse de l'ISG |
| Port | 502 | Port Modbus TCP |
| Identifiant esclave | 1 | `slave` / unit id |

Options (roue dentée de l'intégration) : intervalle d'interrogation (30 s par défaut)
et délai d'attente Modbus (5 s par défaut).

> Le Modbus TCP doit être activé sur l'ISG. L'ISG n'accepte qu'un nombre très limité
> de connexions simultanées : ne laissez pas l'ancien bloc `modbus:` actif en même temps.

## Entités

| Entité | Type | Registre | Détail |
|---|---|---|---|
| Température extérieure | capteur | 506 (input) | int16, ×0,1 °C |
| CC1 température actuelle | capteur | 507 (input) | int16, ×0,1 °C |
| ECS température actuelle | capteur | 521 (input) | int16, ×0,1 °C |
| Chauffage CC1 | thermostat | T° 507, consigne 1507 | 20–45 °C, pas 0,5 |
| ECS confort | thermostat | T° 521, consigne 1509 | 40–60 °C, pas 0,5 |
| ECS réduit | thermostat | T° 521, consigne 1510 | 40–60 °C, pas 0,5 |

Les adresses sont celles passées telles quelles à pymodbus (base 0), comme dans le YAML
d'origine. Une sonde absente (valeur `-32768`) rend le capteur *indisponible*.

### Modes (registre global 1500)

| Mode Home Assistant | Valeur lue | Valeur écrite |
|---|---|---|
| Arrêt (`off`) | 1 | 1 (ECS) — **36864 dans 1507 pour CC1**, voir ci-dessous |
| Auto (`auto`) | 2 | 2 |
| Chauffage (`heat`) | 3, 4 ou 5 | 3 |

## Dépannage : « Impossible de se connecter à l'ISG »

Le message d'erreur du formulaire affiche la cause réelle (elle est aussi dans
*Paramètres → Système → Journaux*). Causes habituelles :

1. **Le bloc `modbus:` du YAML est toujours actif** : il garde la connexion ouverte et l'ISG
   refuse un second client. Supprimez (ou commentez) le bloc, redémarrez Home Assistant,
   puis ajoutez l'intégration.
2. Modbus TCP désactivé sur l'ISG, mauvaise adresse ou mauvais port.
3. Pare-feu / VLAN entre Home Assistant et l'ISG.

Pour plus de détails, ajoutez dans `configuration.yaml` :

```yaml
logger:
  logs:
    custom_components.stiebel_isg_modbus: debug
    pymodbus: debug
```

## Migration depuis `configuration.yaml`

1. Installer et configurer l'intégration (ci-dessus).
2. Supprimer le bloc `modbus:` de `configuration.yaml` et redémarrer.
3. Les `entity_id` changent : l'ancien `sensor.pac_temp_exterieure` devient par exemple
   `sensor.stiebel_eltron_isg_outdoor_temperature`. Renommez les entités si besoin
   (*Paramètres → Entités*) et mettez à jour vos automatisations, tableaux de bord
   et le tableau de bord Énergie.

### Chauffage CC1 : « Off » = consigne fixe désactivée (36864)

Comme dans l'ancien YAML (`state_off: 36864` sur le registre 1507), le mode **Off** du
thermostat **Chauffage CC1** n'écrit pas le mode global : il écrit la valeur **36864** dans le
registre de consigne (1507), ce qui désactive la consigne de chauffage fixe.

- Quand le registre contient 36864, CC1 apparaît en **Off** et n'a pas de consigne affichée.
- **Auto** / **Heat** : la dernière consigne connue est réécrite dans 1507, puis le mode global
  (registre 1500) n'est écrit que s'il n'a pas déjà la bonne valeur — il est partagé avec l'ECS.
- Régler une température sur CC1 réactive aussi la consigne (comme avant).
- La dernière consigne est conservée au redémarrage de Home Assistant. Si CC1 est à l'arrêt et
  qu'aucune consigne n'est connue, passer en Auto / Heat est refusé avec un message : réglez d'abord
  une température.
- Le mode global en veille (1500 = 1) est lu comme **Off** pour CC1 sans toucher à la consigne.

Pour l'ECS (confort et réduit), **Off** écrit toujours le mode global (1500 = 1).

## Développement et tests de non-régression

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements_test.txt
pytest
pytest --cov=custom_components.stiebel_isg_modbus --cov-report=term-missing
```

La suite couvre :

- le (dé)codage des registres (int16 signé, sonde absente, arrondis) et la **fidélité à l'ancien YAML**
  (adresses, plages, mapping des modes) ;
- le client Modbus : reconnexion, sérialisation des requêtes, compatibilité `device_id` / `slave`
  (pymodbus ≥ 3.10 et antérieur), erreurs de connexion vs erreurs de registre ;
- l'assistant de configuration et les options ;
- le cycle de vie, la cadence d'interrogation, la perte/reprise de communication ;
- capteurs et thermostats (écritures, consignes hors plage, modes, échecs d'écriture) ;
- des tests **bout en bout contre un vrai serveur Modbus TCP pymodbus** local ;
- la cohérence des traductions (`en`, `fr`) et des métadonnées (`manifest.json`, `hacs.json`).

La logique sans dépendance à Home Assistant est isolée dans `registers.py` et `hub.py`.

## Liens

- Dépôt : <https://github.com/crapitouille/ha-stiebeleltron-isg>
- Signaler un problème : <https://github.com/crapitouille/ha-stiebeleltron-isg/issues>
