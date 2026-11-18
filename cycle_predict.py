#!/usr/bin/env python3
"""
cycle_predict.py — Prédiction ON/OFF basée sur la grille de cycle 8 semaines.

La grille officielle (document "Cycle de travail" SafeSquare) définit un
roulement de 8 lignes. La semaine du 16/11/2026 = Ligne 5 (ancrage).
Chaque semaine suivante avance d'une ligne, et ça reboucle L8 -> L1.

Prédit si un jour donné est ON (travaillé) ou OFF, indépendamment de l'heure.
"""

from datetime import datetime, date, timedelta

# --- Grille de cycle (à confirmer/ajuster par l'utilisateur) ---
# True = ON (travaillé), False = OFF. Ordre : Lun Mar Mer Jeu Ven Sam Dim
GRILLE = {
    1: [False, False, True,  True,  True,  True,  False],
    2: [False, True,  True,  True,  False, True,  True ],
    3: [False, True,  True,  True,  True,  False, False],
    4: [True,  True,  False, False, True,  True,  True ],
    5: [False, True,  True,  True,  True,  False, False],
    6: [True,  True,  False, False, True,  True,  True ],
    7: [True,  False, True,  True,  True,  False, False],
    8: [True,  False, False, True,  True,  True,  True ],
}

# Ancrage : la semaine contenant cette date est la Ligne N
ANCRAGE_DATE = date(2026, 11, 16)   # lundi de la semaine du 16/11
ANCRAGE_LIGNE = 5


def lundi_de(d: date) -> date:
    """Retourne le lundi de la semaine contenant d."""
    return d - timedelta(days=d.weekday())


def ligne_pour_semaine(d: date) -> int:
    """Quelle ligne de cycle s'applique à la semaine contenant d ?"""
    l_ancrage = lundi_de(ANCRAGE_DATE)
    l_cible = lundi_de(d)
    semaines_ecart = (l_cible - l_ancrage).days // 7
    ligne = ((ANCRAGE_LIGNE - 1 + semaines_ecart) % 8) + 1
    return ligne


def est_on(d: date) -> bool:
    """True si le jour d est travaillé (ON) selon la grille."""
    ligne = ligne_pour_semaine(d)
    return GRILLE[ligne][d.weekday()]


def predire(depuis: date, nb_jours: int = 28) -> list[dict]:
    """Prédit ON/OFF pour les nb_jours à venir depuis 'depuis'."""
    out = []
    for i in range(nb_jours):
        d = depuis + timedelta(days=i)
        out.append({
            "date": d,
            "ligne": ligne_pour_semaine(d),
            "on": est_on(d),
        })
    return out


if __name__ == "__main__":
    print("Cycle de travail — prédiction ON/OFF\n")
    JOURS = ["Lun", "Mar", "Mer", "Jeu", "Ven", "Sam", "Dim"]
    today = date.today()
    cur_ligne = None
    for p in predire(lundi_de(today), 28):
        d = p["date"]
        if p["ligne"] != cur_ligne:
            cur_ligne = p["ligne"]
            print(f"\n--- Semaine du {d.strftime('%d/%m')} : Ligne {cur_ligne} ---")
        sym = "🟦 ON " if p["on"] else "⬜ OFF"
        print(f"  {JOURS[d.weekday()]} {d.strftime('%d/%m')} : {sym}")


# ---------------------------------------------------------------------------
# Créneau matin/aprem par ligne de cycle (calé sur 19 mois d'émargement)
# ---------------------------------------------------------------------------
# Pattern 2 sem matin / 2 sem aprem : L1-L2 matin, L3-L4 aprem, L5-L6 matin, L7-L8 aprem
LIGNE_CRENEAU = {
    1: "MATIN", 2: "MATIN", 3: "APREM", 4: "APREM",
    5: "MATIN", 6: "MATIN", 7: "APREM", 8: "APREM",
}
# Fiabilité observée par ligne (%)
LIGNE_FIABILITE = {1: 100, 2: 96, 3: 78, 4: 85, 5: 81, 6: 94, 7: 75, 8: 92}

# Horaires-types par créneau (début/fin les plus fréquents)
CRENEAU_HORAIRE = {
    "MATIN": {"debut": "03:30", "fin": "12:30", "plage_debut": "03:30-05:00", "plage_fin": "10:30-13:00"},
    "APREM": {"debut": "14:00", "fin": "22:30", "plage_debut": "13:30-15:00", "plage_fin": "21:00-23:00"},
}


def creneau_pour(d: date) -> str:
    """MATIN ou APREM selon la ligne de cycle de la semaine de d."""
    return LIGNE_CRENEAU[ligne_pour_semaine(d)]


def predire_complet(depuis: date, nb_jours: int = 42) -> list[dict]:
    """Prédiction complète : ON/OFF + créneau + horaire-type."""
    out = []
    for i in range(nb_jours):
        d = depuis + timedelta(days=i)
        ligne = ligne_pour_semaine(d)
        on = est_on(d)
        item = {"date": d, "ligne": ligne, "on": on}
        if on:
            cr = LIGNE_CRENEAU[ligne]
            item["creneau"] = cr
            item["horaire"] = horaire_pour(d)
            item["fiabilite"] = LIGNE_FIABILITE[ligne]
        out.append(item)
    return out


# Horaire-type par (créneau, jour de semaine) — calé sur 19 mois d'émargement
CRENEAU_JOUR_HORAIRE = {"MATIN": {"0": ["03:30", "11:00"], "2": ["04:00", "11:30"], "3": ["03:30", "11:00"], "4": ["05:00", "12:30"], "1": ["03:30", "10:30"], "5": ["03:30", "11:30"], "6": ["04:00", "12:30"]}, "APREM": {"1": ["14:30", "22:30"], "0": ["14:00", "22:30"], "2": ["14:30", "22:30"], "5": ["12:30", "22:00"], "6": ["14:30", "23:00"], "3": ["14:00", "22:00"], "4": ["14:30", "22:30"]}}
CRENEAU_FALLBACK = {"MATIN": ["03:30", "12:30"], "APREM": ["14:00", "22:30"]}


def horaire_pour(d: date) -> str:
    """Horaire-type estimé pour un jour travaillé, selon créneau + jour de semaine."""
    cr = LIGNE_CRENEAU[ligne_pour_semaine(d)]
    tab = CRENEAU_JOUR_HORAIRE.get(cr, {})
    deb, fin = tab.get(str(d.weekday()), tab.get(d.weekday())) if (str(d.weekday()) in tab or d.weekday() in tab) else CRENEAU_FALLBACK[cr]
    return f"{deb}-{fin}"
