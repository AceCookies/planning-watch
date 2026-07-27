# Surveillance du planning —

Ce script vérifie ton planning sur le site du personnel et t'envoie une
notification **Discord + email** dès qu'un horaire change.

## 1. Installation sur le Raspberry Pi

```bash
# Copier le dossier planning_watch sur le Pi, puis :
cd planning_watch
python3 -m venv venv
source venv/bin/activate
pip install requests beautifulsoup4 python-dotenv google-api-python-client google-auth
```

## 2. Configuration

```bash
cp .env.example .env
nano .env
```

Remplis :

- **SITE_LOGIN / SITE_PASSWORD** : tes identifiants du site (ils restent
  uniquement sur ton Pi).
- **PLANNING_PAGE** : connecte-toi au site dans ton navigateur, ouvre ton
  planning, et copie le chemin de l'URL (tout ce qui vient après
  `le site`). C'est la seule info que je n'avais pas depuis tes captures.
- **DISCORD_WEBHOOK_URL** : dans ton serveur Discord → clic droit sur un
  salon → *Modifier le salon* → *Intégrations* → *Webhooks* → *Nouveau
  webhook* → *Copier l'URL du webhook*.
- **SMTP_USER / SMTP_PASSWORD** : pour Gmail, crée un *mot de passe
  d'application* sur https://myaccount.google.com/apppasswords (il faut que
  la validation en 2 étapes soit activée). N'utilise pas ton mot de passe
  Gmail normal.

Protège le fichier :

```bash
chmod 600 .env
```

## 3. Premier test

```bash
source venv/bin/activate
python planning_watch.py
```

- 1ère exécution → « Première exécution : planning sauvegardé comme
  référence. » (aucune notification, c'est normal)
- Exécutions suivantes → notification seulement si quelque chose a changé.

Pour tester la notification : ouvre `planning_state.json`, modifie un
horaire à la main, sauvegarde, puis relance le script. Tu devrais recevoir
le message Discord + l'email.

## 4. Channel Discord pour les documents

Quand un document bloque l'accès au planning, le script le télécharge et
l'envoie en pièce jointe dans un channel Discord dédié — tu peux donc le
**lire avant** d'aller le valider sur le site.

1. Crée un channel (ex : `#documents-travail`) dans ton serveur Discord.
2. Modifier le salon → Intégrations → Webhooks → Nouveau webhook → Copier l'URL.
3. Colle l'URL dans `DISCORD_DOCS_WEBHOOK_URL` du `.env`.

Limite : Discord accepte les fichiers jusqu'à ~25 Mo. Au-delà, tu reçois un
message avec le lien direct vers le document à la place.

## 5. Google Agenda

Le script peut créer/mettre à jour automatiquement tes vacations dans un
agenda Google dédié :

- `14:30-22:30` → événement **🛡️ Travail** de 14h30 à 22h30
- `03:30-10:30 => PARIFS H+` → **🛡️ Travail · => PARIFS H+**
- `22:00-06:00` → événement qui se termine le lendemain matin (passage minuit géré)
- `CP OUVRABL` → événement journée entière **🏖️ Congé (CP)**
- `OFF` → aucun événement (et si un horaire devient OFF, l'événement est supprimé)

### Mise en place (une seule fois, ~10 min)

1. **Créer un agenda dédié** dans Google Agenda (ex : "Travail ENTREPRISE").
   ⚠️ Utilise un agenda séparé, pas ton agenda principal, pour que le script
   ne touche jamais à tes événements perso.
2. **Créer un compte de service Google** :
   - Va sur https://console.cloud.google.com → crée un projet (ex : `planning-watch`)
   - Menu → *API et services* → *Bibliothèque* → active **Google Calendar API**
   - Menu → *IAM et administration* → *Comptes de service* → *Créer un compte de service*
     (nom : `planning-watch`, pas besoin de rôle)
   - Ouvre le compte de service → onglet *Clés* → *Ajouter une clé* → *JSON*
   - Télécharge le fichier et copie-le sur le Pi sous le nom `service_account.json`
     dans le dossier du script, puis `chmod 600 service_account.json`
3. **Partager l'agenda avec le compte de service** :
   - Dans Google Agenda → paramètres de ton agenda "Travail ENTREPRISE"
   - *Partager avec des personnes* → ajoute l'adresse email du compte de service
     (du type `planning-watch@....iam.gserviceaccount.com`, visible dans le
     fichier JSON, champ `client_email`)
   - Autorisation : **"Apporter des modifications aux événements"**
4. **Récupérer l'ID de l'agenda** : paramètres de l'agenda → section
   *Intégrer l'agenda* → *ID de l'agenda* (du type `xxxx@group.calendar.google.com`)
5. Dans le `.env` : `GCAL_ENABLED=true` et `GCAL_CALENDAR_ID=...`

À la première exécution, tout le planning visible est synchronisé. Ensuite,
seules les journées modifiées sont mises à jour.

## 6. Automatisation avec cron

Pour vérifier toutes les heures entre 6h et 23h :

```bash
crontab -e
```

Ajoute (adapte le chemin) :

```
0 6-23 * * * cd /home/pi/planning_watch && ./venv/bin/python planning_watch.py >> cron.log 2>&1
```

## 7. Annotations du planning (H+, DEC_48h, PARIFS...)

Les codes du planning sont traduits automatiquement dans les notifications
et dans Google Agenda, grâce au fichier **`annotations.json`** :

```json
"H+": {
  "emoji": "⏰",
  "label": "Heure(s) supplémentaire(s)",   ← notifications & description agenda
  "short": "Heures supp",                  ← titre de l'événement agenda
  "urgent": false                          ← true = alerte 🚨 en tête de notif
}
```

Codes déjà configurés : `H+`, `DEC_48h` (marqué **urgent** : changement sous
48h, la notification est préfixée 🚨), `PARIFS`.

**Pour ajouter un nouveau code** : ouvre `annotations.json`, copie un bloc
existant, adapte-le, sauvegarde. C'est tout — pas besoin de toucher au script.

## 8. Changements rétroactifs & annotations retirées

Deux cas particulièrement importants sont mis en évidence :

- **Changement sur un jour déjà passé** (ex : tu es resté plus tard que
  prévu) → la notification est marquée `🕓 RÉTROACTIF` avec le calcul de la
  différence de temps travaillé, ex : `[+1h05 de travail]`. L'événement
  Google Agenda est aussi corrigé avec les heures réelles.
- **Annotation retirée** (ex : un `H+` qui disparaît) → ligne dédiée
  `❗ Annotation(s) retirée(s) : H+ (Heure(s) supplémentaire(s))`.

En plus des notifications, tous les changements rétroactifs sont consignés
dans **`historique_retroactif.csv`** (ouvrable dans Excel/LibreOffice, ou
sur le Pi avec `cat`). Pratique pour vérifier ta fiche de paie en fin de
mois : date de détection, jour concerné, horaire avant/après, différence en
minutes, annotations retirées.

## 9. Format des notifications

Les notifications Discord utilisent des **embeds** : une carte de synthèse
suivie d'une carte par jour modifié, avec un code couleur.

| Couleur | Signification |
|---------|---------------|
| 🔴 Rouge | À vérifier : DEC_48h ou annotation retirée |
| 🟠 Orange | Modification sur un jour déjà passé |
| 🔵 Bleu | Horaire modifié |
| 🟢 Vert | Nouveau créneau / journée travaillée ajoutée |
| ⚪ Gris | Créneau retiré / journée libérée |

Chaque carte affiche la date en clair (« mercredi 17 juin »), l'écart de
temps travaillé le cas échéant, les annotations actives, et l'échéance
(« demain », « dans 7 jours », « il y a 36 jours »).

L'email reprend les mêmes informations en texte simple.

### Ping Discord

Renseigne `DISCORD_PING` dans le `.env` pour recevoir une vraie notification
(badge rouge + son) et pas seulement un message dans le channel :

1. Discord → Paramètres utilisateur → Avancés → active le **Mode développeur**
2. Clic droit sur ton pseudo → **Copier l'identifiant**
3. Colle le numéro dans `DISCORD_PING=` (ou mets `@here`)

Avec `DISCORD_PING_URGENT_ONLY=true`, tu n'es pingé que pour les cas
importants (DEC_48h, annotation retirée, changement rétroactif) ; les
changements banals arrivent silencieusement. Les documents à valider
pingent toujours, puisqu'ils bloquent l'accès au planning.

## 10. Documents internes ("Nouveau Document")

Le site publie des documents (notes de paie, notes de service, doc de
liaison...) signalés par le lien rouge clignotant « Nouveau Document ». Le
script les détecte automatiquement à chaque passage :

1. Récupère la liste des notes non lues
2. Télécharge le PDF
3. L'envoie dans ton channel Discord dédié **et** par email en pièce jointe,
   avec un ping
4. Selon `NOTES_AUTO_MARKLU` :
   - `false` (défaut) : te laisse valider toi-même sur le site
   - `true` : clique « Pris en compte » automatiquement

Réglages dans `.env` :

```
NOTES_ENABLED=true
NOTES_AUTO_MARKLU=false
```

⚠️ **À propos de la validation automatique** : cliquer « Pris en compte »
atteste officiellement que tu as lu et compris le document. En mode
automatique, le PDF t'est toujours envoyé (Discord + email) avant la
validation, mais celle-ci part sans que tu aies eu à ouvrir le document.
À utiliser en connaissance de cause, notamment pour les consignes de sûreté.

Le fichier `notes_seen.json` mémorise les notes déjà traitées pour ne pas
les renvoyer à chaque passage.

## 10. Anciens types de notifications

| Symbole | Signification |
|---------|---------------|
| 🔄 | Un horaire existant a été modifié (ex : `14:30-22:30 → OFF`) |
| 📅 | Une semaine "non disponible" vient d'être publiée |
| 🆕 | Une nouvelle date est apparue dans le planning |
| ⚠️ | Un horaire publié a été retiré |

## Documents bloquants ("prendre en compte")

Quand une note de service ou une notification doit être validée avant
d'accéder au planning, le script :

- **par défaut** : t'envoie une notification 📄 avec le titre du document
  (une seule fois, pas de spam) et réessaie au prochain passage du cron.
  Une fois que tu l'as validé sur le site, tout reprend normalement.
- **si `AUTO_ACKNOWLEDGE=true` dans `.env`** : clique "Prendre en compte"
  automatiquement et continue vers le planning. ⚠️ Attention : cela vaut
  confirmation officielle que tu as lu le document — tu ne le verras jamais.

## Dépannage

- **« Formulaire de connexion introuvable »** : vérifie `LOGIN_PAGE` (essaie
  `/` ou le chemin exact de la page de connexion).
- **« La connexion semble avoir échoué »** : vérifie login/mot de passe.
- **« Aucune date trouvée »** : vérifie `PLANNING_PAGE`.
- Les logs sont dans `planning_watch.log`.
- Le fichier `pending_ack.txt` sert de marqueur anti-spam quand un document
  est en attente — il se supprime tout seul.

## Notes

- Tes identifiants ne quittent jamais ton Raspberry Pi.
- Le script est très léger (quelques secondes par exécution, aucun navigateur).
- Si le site évolue un jour et que le script ne trouve plus le tableau,
  l'erreur apparaîtra dans les logs — reviens me voir avec le nouveau HTML
  et je l'adapterai.
