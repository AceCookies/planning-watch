# planning_watch

Surveillance automatique du planning de travail (sûreté aéroportuaire Nantes), avec :
- Notifications Discord + email des changements de planning et notes de service
- Synchronisation Google Agenda (couleurs différenciées)
- Dashboard web (planning + prédictions, notes, vérification de paie, prévisionnel de salaire)
- Prédiction du planning basée sur le cycle 8 semaines
- Vérification de paie par mois calendaire (barèmes CCN Prévention/Sécurité annexe VIII)

## Fichiers

| Fichier | Rôle |
|---|---|
| `planning_watch.py` | Script principal (surveillance, notifs, sync agenda) |
| `dashboard.py` | Générateur du dashboard HTML |
| `serve_dashboard.py` | Mini serveur HTTP avec auth |
| `cycle_predict.py` | Prédiction du planning (cycle 8 semaines) |
| `gen_verif_cycles.py` | Recalcul de la vérification de paie |
| `previsionnel_paie.py` | Estimation du salaire du mois en cours |
| `maj_releves.py` | Récupération/parsing des relevés d'activité |
| `planning-dashboard.service` | Service systemd du dashboard |

## Installation (restauration)

```bash
git clone <repo> planning_watch && cd planning_watch
python3 -m venv venv && source venv/bin/activate
pip install requests beautifulsoup4 pdfplumber google-auth google-auth-oauthlib google-api-python-client
cp .env.example .env   # puis remplir .env
# déposer oauth_client.json (Google) et relancer pour générer token.json
```

### Cron (4×/jour, 7j/7)
```
0 8,12,17,21 * * * cd /root/planning_watch && ./venv/bin/python planning_watch.py >> cron.log 2>&1
```

## ⚠️ Données NON versionnées (voir .gitignore)
Secrets (`.env`, `token.json`, `oauth_client.json`) et données RH privées
(`emargement.json`, bulletins, relevés, paie) ne sont pas sur GitHub.
À sauvegarder séparément de façon chiffrée.
