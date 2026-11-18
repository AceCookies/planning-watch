#!/usr/bin/env python3
"""Prévisionnel de salaire du mois en cours, calculé depuis planning_state.json.
S'affine au fil du mois à mesure que les horaires tombent.
Barèmes CCN Prévention/Sécurité annexe VIII (IDCC 1351)."""
import json
import re
from datetime import date, datetime
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
PLANNING_STATE = SCRIPT_DIR / "planning_state.json"

# --- Barèmes (modifiables ici si taux revalorisés) ---
TAUX_HORAIRE = 14.19
MAJ_NUIT = TAUX_HORAIRE * 0.25        # +25 % (plage 21h-6h)
MAJ_DIMANCHE = TAUX_HORAIRE * 0.50    # +50 %
PANIER = 6.87                         # 1 par jour travaillé
PRIME_HABILLAGE = 2.16                # par jour travaillé
HEURES_CONTRAT = 151.67               # mensualisation temps plein
TAUX_NET = 0.828                      # ratio net/brut calibré sur 20 bulletins réels (80-87%)

TIME_RE = re.compile(r"(\d{1,2}):(\d{2})\s*-\s*(\d{1,2}):(\d{2})")


def _mins(h, m):
    return int(h) * 60 + int(m)


def _heures_creneau(d1, m1, d2, m2):
    deb = _mins(d1, m1); fin = _mins(d2, m2)
    if fin <= deb:
        fin += 1440
    return deb, fin


def analyser_jour(valeur: str):
    """Retourne dict {travaille, heures, nuit, duree, code} pour une valeur de planning."""
    v = (valeur or "").strip()
    vup = v.upper()
    # Non travaillés
    if not v or vup.startswith("OFF") or "NON DISPONIBLE" in vup:
        return {"travaille": False, "code": "OFF"}
    if vup.startswith("ML"):
        return {"travaille": False, "code": "ML"}
    if vup.startswith("CP"):
        return {"travaille": False, "code": "CP"}
    if vup.startswith("ABS") or vup.startswith("PER"):
        return {"travaille": False, "code": "ABS"}

    # Un ou plusieurs créneaux horaires (formation = plusieurs blocs)
    creneaux = TIME_RE.findall(v)
    if not creneaux:
        return {"travaille": False, "code": "?"}

    duree = 0.0
    nuit = 0.0
    for d1, m1, d2, m2 in creneaux:
        deb, fin = _heures_creneau(d1, m1, d2, m2)
        duree += (fin - deb) / 60
        nuit += sum(1 for mn in range(deb, fin) if (mn // 60) % 24 >= 21 or (mn // 60) % 24 < 6) / 60

    code = "travail"
    if "FOR" in vup:
        code = "formation"
    return {"travaille": True, "duree": round(duree, 2), "nuit": round(nuit, 2), "code": code}


def previsionnel_mois(state: dict, annee: int, mois: int) -> dict:
    """Calcule le prévisionnel pour un mois donné à partir du planning."""
    jours_trav = 0
    jours_formation = 0
    h_travail = 0.0
    h_formation = 0.0
    h_nuit = 0.0
    h_dimanche = 0.0
    paniers = 0
    jours_connus = 0      # jours du mois présents dans le planning
    jours_restants = 0    # jours du mois pas encore dans le planning
    jours_cp = 0          # congés payés (fausse le calcul : paiement CP à part)
    jours_maladie = 0     # ML (maintien de salaire à part)
    jours_absence = 0     # ABS/PER
    detail = []

    import calendar
    nb_jours = calendar.monthrange(annee, mois)[1]
    aujourdhui = date.today()

    for dd in range(1, nb_jours + 1):
        d = date(annee, mois, dd)
        ds = d.strftime("%d/%m/%Y")
        valeur = state.get(ds)
        if valeur is None:
            jours_restants += 1
            continue
        jours_connus += 1
        info = analyser_jour(valeur)
        if not info["travaille"]:
            if info["code"] == "CP":
                jours_cp += 1
            elif info["code"] == "ML":
                jours_maladie += 1
            elif info["code"] == "ABS":
                jours_absence += 1
            detail.append((ds, info["code"], 0.0))
            continue
        paniers += 1
        if info["code"] == "formation":
            jours_formation += 1
            h_formation += info["duree"]
        else:
            jours_trav += 1
            h_travail += info["duree"]
        h_nuit += info["nuit"]
        if d.weekday() == 6:   # dimanche
            h_dimanche += info["duree"]
        detail.append((ds, info["code"], info["duree"]))

    h_total = h_travail + h_formation

    # --- Calcul salaire ---
    base = HEURES_CONTRAT * TAUX_HORAIRE
    mn = h_nuit * MAJ_NUIT
    md = h_dimanche * MAJ_DIMANCHE
    pan = paniers * PANIER
    hab = paniers * PRIME_HABILLAGE
    brut = base + mn + md + pan + hab
    net = brut * TAUX_NET

    return {
        "mois": f"{annee}-{mois:02d}",
        "jours_travailles": jours_trav,
        "jours_formation": jours_formation,
        "jours_connus": jours_connus,
        "jours_restants": jours_restants,
        "h_travail": round(h_travail, 1),
        "h_formation": round(h_formation, 1),
        "h_total": round(h_total, 1),
        "h_nuit": round(h_nuit, 1),
        "h_dimanche": round(h_dimanche, 1),
        "paniers": paniers,
        "lignes": {
            "base": round(base, 2),
            "maj_nuit": round(mn, 2),
            "maj_dimanche": round(md, 2),
            "paniers": round(pan, 2),
            "habillage": round(hab, 2),
        },
        "brut": round(brut, 2),
        "net_estime": round(net, 2),
        "complet": jours_restants == 0,
        "jours_cp": jours_cp,
        "jours_maladie": jours_maladie,
        "jours_absence": jours_absence,
        # Fiabilité : "bonne" si mois standard, "reduite" si CP/absence (paiements à part)
        "fiabilite": "reduite" if (jours_cp or jours_absence) else ("moyenne" if jours_maladie else "bonne"),
    }


def previsionnel_courant(state: dict = None) -> dict:
    """Prévisionnel du mois en cours."""
    if state is None:
        state = json.loads(PLANNING_STATE.read_text(encoding="utf-8"))
    t = date.today()
    return previsionnel_mois(state, t.year, t.month)


if __name__ == "__main__":
    import sys
    state = json.loads(PLANNING_STATE.read_text(encoding="utf-8"))
    if len(sys.argv) == 3:
        p = previsionnel_mois(state, int(sys.argv[1]), int(sys.argv[2]))
    else:
        p = previsionnel_courant(state)
    print(json.dumps(p, ensure_ascii=False, indent=2))
