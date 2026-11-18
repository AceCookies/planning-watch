<div align="center">

# 🛡️ planning-watch

**Surveillance automatique du planning de sûreté aéroportuaire — Nantes-Atlantique**

Notifications en temps réel · Synchronisation agenda · Vérification de paie · Prédiction du planning

![Python](https://img.shields.io/badge/Python-3.10+-3776AB?logo=python&logoColor=white)
![Statut](https://img.shields.io/badge/statut-en%20production-2ecc71)
![Plateforme](https://img.shields.io/badge/VPS-systemd%20%2B%20cron-orange)
![Licence](https://img.shields.io/badge/usage-personnel-lightgrey)

</div>

---

## 📖 En bref

`planning-watch` surveille le site du personnel, détecte tout changement de planning ou nouvelle note de service, **prévient immédiatement** (Discord + email), synchronise le planning dans **Google Agenda**, et alimente un **dashboard web** complet : planning + prédictions, notes de service, prévisionnel de salaire et vérification des paies.

Le tout tourne **tout seul**, 4 fois par jour, 7 jours sur 7, sur un petit VPS.

---

## ✨ Fonctionnalités

| | Fonction | Détail |
|---|---|---|
| 🔔 | **Alertes instantanées** | Discord + email dès qu'un créneau change ou qu'une note tombe |
| 📅 | **Sync Google Agenda** | Chaque vacation ajoutée à l'agenda, couleurs par type (fret, formation, H+…) |
| 📋 | **Notes de service** | Récupération, archivage et notification des notes (PDF joint) |
| 🔮 | **Prévisionnel de salaire** | Estimation du net du mois en cours, calculée depuis les horaires déjà tombés |
| 🔍 | **Vérification de paie** | Comparaison bulletin ↔ heures réelles (barèmes CCN annexe VIII) |
| 📈 | **Prédiction du planning** | Jours ON/OFF + créneaux prévus via le cycle 8 semaines |
| 🖥️ | **Dashboard web** | Interface à onglets, protégée par mot de passe |

---

## 🖼️ Aperçu du dashboard

Le dashboard est organisé en **4 onglets**.

### 📋 Planning & prédictions
Calendrier mensuel typé comme le site du personnel. Une **ligne rouge** sépare le réel (publié) du prévisionnel (prédit, en pointillés).

![Onglet Planning](docs/tab_planning.png)

### 💰 Paie
Prévisionnel du mois en cours + vérification de chaque paie passée, avec un verdict clair (conforme / écart en ta faveur / à vérifier).

![Onglet Paie](docs/tab_paie.png)

### 📝 Notes de service
Historique des notes reçues, catégorisées par type (paie, service, codes, acompte…).

![Onglet Notes](docs/tab_notes.png)

### 📊 Stats
Tendances des modifications, répartition des types de vacation, horaires et codes rencontrés.

![Onglet Stats](docs/tab_stats.png)

---

## 🧩 Architecture

```
planning-watch/
├── planning_watch.py          # 🧠 Script principal (surveillance, notifs, sync agenda)
├── dashboard.py               # 🖥️  Génère le dashboard HTML
├── serve_dashboard.py         # 🌐 Mini serveur web (auth par mot de passe)
├── cycle_predict.py           # 🔮 Prédiction du planning (cycle 8 semaines)
├── previsionnel_paie.py       # 💶 Estimation du salaire du mois en cours
├── gen_verif_cycles.py        # 🔍 Recalcule la vérification de paie
├── maj_releves.py             # 📥 Récupère et parse les relevés d'activité
├── planning-dashboard.service # ⚙️  Service systemd du dashboard
├── .env.example               # 📝 Modèle de configuration
└── docs/                      # 🖼️  Captures du dashboard
```

---

## 🚀 Installation

> ⏱️ **~10 minutes.** Prérequis : un serveur Linux (VPS, Raspberry, homelab…) avec Python 3.10+ et `git`.

### Étape 1 — Récupérer le code

```bash
git clone https://github.com/AceCookies/planning-watch.git
cd planning-watch
```

### Étape 2 — Créer l'environnement Python

```bash
python3 -m venv venv
source venv/bin/activate
pip install requests beautifulsoup4 pdfplumber \
            google-auth google-auth-oauthlib google-api-python-client
```

### Étape 3 — Configurer (le cœur du réglage)

```bash
cp .env.example .env
nano .env        # remplir avec tes infos
```

<details>
<summary><b>👉 Que mettre dans le <code>.env</code> ? (cliquer pour déplier)</b></summary>

| Variable | À remplir avec |
|---|---|
| `SITE_URL`, `SITE_LOGIN`, `SITE_PASSWORD` | L'adresse et tes identifiants du site du personnel |
| `DISCORD_WEBHOOK_URL` | L'URL du webhook de ton salon Discord *(Paramètres → Intégrations → Webhooks)* |
| `SMTP_HOST`, `SMTP_USER`, `SMTP_PASSWORD`, `EMAIL_TO` | Ton serveur mail *(ex. Gmail + mot de passe d'application)* |
| `GCAL_CALENDAR_ID` | `primary` pour ton agenda principal |
| `DASHBOARD_USER`, `DASHBOARD_PASSWORD` | Un login/mot de passe pour protéger le dashboard |

</details>

### Étape 4 — Autoriser Google Agenda *(optionnel)*

1. Dépose ton fichier `oauth_client.json` (depuis Google Cloud Console) dans le dossier.
2. Lance une première fois : `python3 planning_watch.py`
3. Suis le lien affiché pour autoriser l'accès → un `token.json` est créé automatiquement.

### Étape 5 — Automatiser (cron)

```bash
crontab -e
```

Ajouter cette ligne (vérifie 4×/jour, 7j/7) :

```cron
0 8,12,17,21 * * * cd /root/planning-watch && ./venv/bin/python planning_watch.py >> cron.log 2>&1
```

### Étape 6 — Lancer le dashboard web

```bash
# copier le service systemd
cp planning-dashboard.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now planning-dashboard
```

Le dashboard est alors accessible sur `http://<ip-du-serveur>:8080` 🎉

---

## 🔄 Mettre à jour

Après une modification du code sur le serveur :

```bash
git add -A && git commit -m "description du changement" && git push
```

Pour restaurer sur une nouvelle machine : refais simplement l'**installation** ci-dessus (le code revient de GitHub ; recrée juste le `.env` et les fichiers Google).

---

## 🔒 Confidentialité & sécurité

Ce dépôt **ne contient aucune donnée personnelle ni aucun secret**. Sont volontairement exclus (voir [`.gitignore`](.gitignore)) :

- 🔑 **Secrets** : `.env`, `token.json`, `oauth_client.json`
- 🧾 **Données RH** : bulletins de paie, relevés d'activité, émargement, historique de paie

> ⚠️ Ces données ne doivent **jamais** être poussées sur GitHub, même en dépôt privé. Pour les sauvegarder, utilise un stockage chiffré séparé (NAS, homelab…).

---

## 📐 Barèmes de paie

Les calculs suivent la **convention collective Prévention et Sécurité, annexe VIII** (IDCC 1351) :

| Élément | Barème |
|---|---|
| Majoration nuit (21h–6h) | **+25 %** |
| Majoration dimanche | **+50 %** |
| Panier | **6,87 €** / jour travaillé |
| Base mensualisée | **151,67 h** |

Les variables (nuit, dimanche, paniers) sont décomptées sur le **mois calendaire travaillé** et payées le mois suivant.

---

<div align="center">
<sub>Outil personnel · non affilié à l'employeur · 🤖 développé avec l'aide de Claude</sub>
</div>
