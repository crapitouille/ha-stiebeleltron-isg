# Changelog

## 0.2.0

- Nouveau capteur **Consigne tampon** (registre 518, diagnostic).
- Nouveau capteur binaire **Période de chauffage** : actif quand la consigne tampon dépasse 5,0 °C
  (seuil réglable dans les options). Indisponible si la valeur est invalide ou si la communication échoue.
- Chauffage CC1 : le mode **Off** écrit 36864 dans la consigne (1507), la dernière consigne est restaurée
  en quittant l'arrêt.
- Formulaire de configuration : port et identifiant esclave en saisie numérique ; la cause d'un échec de
  connexion est affichée.

## 0.1.0

- Première version : 3 capteurs de température et 3 thermostats (CC1, ECS confort, ECS réduit) en Modbus TCP.
