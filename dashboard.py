#!/usr/bin/env python3
"""
dashboard.py — Génère un dashboard HTML d'analyse du planning.

Analyse :
  - le planning actuel (planning_state.json)
  - l'historique des changements (historique_complet.csv)
Produit un dashboard.html avec stats, tendances et graphiques.

Utilisé par planning_watch.py (régénéré à chaque passage) et par le
mini-serveur web (serve_dashboard.py).
"""

import json
import re
import csv
import html as _html
from datetime import datetime
from pathlib import Path
from collections import Counter, defaultdict
try:
    import cycle_predict
except Exception:
    cycle_predict = None

SCRIPT_DIR = Path(__file__).resolve().parent
STATE_FILE = SCRIPT_DIR / "planning_state.json"
HIST_FILE = SCRIPT_DIR / "historique_complet.csv"
NOTES_HIST_FILE = SCRIPT_DIR / "notes_historique.json"
PAIE_FILE = SCRIPT_DIR / "paie_mensuelle.json"
VERIF_FILE = SCRIPT_DIR / "verif_paie.json"
DASHBOARD_FILE = SCRIPT_DIR / "dashboard.html"

JOURS = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]
MOIS = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet",
        "août", "septembre", "octobre", "novembre", "décembre"]
TIME_RE = re.compile(r"(\d{1,2})[:h](\d{2})\s*-\s*(\d{1,2})[:h](\d{2})")
CODES = ["FRET", "FOR", "H+", "PARIFS", "RET_VOL", "ABS", "ML", "PM", "DEC_48h"]

# Palette
C_BG = "#0f1117"
C_CARD = "#1a1d27"
C_CARD2 = "#232733"
C_TEXT = "#e4e6eb"
C_MUTED = "#8a8f9e"
C_NOTE = "#aab0c0"  # légendes : plus clair que C_MUTED pour rester lisible sur fond foncé
C_ACCENT = "#5865f2"
C_GREEN = "#2ecc71"
C_ORANGE = "#e67e22"
C_RED = "#e74c3c"
C_BLUE = "#3498db"
C_PURPLE = "#9b59b6"
# Couleurs spécifiques planning
C_MALADIE = "#d6457d"   # rose/magenta — maladie (ML)
C_ABSENCE = "#7f8c8d"   # gris-brun — absence (ABS)
C_HPLUS = "#f1c40f"     # jaune — heure sup (H+)
C_FRET = "#c0392b"      # rouge foncé — fret 22h30
C_MATIN = "#e67e22"     # orange — matin
C_APREM = "#3498db"     # bleu — après-midi
C_FORMATION = "#9b59b6" # violet — formation
C_CONGE = "#16a085"     # vert-émeraude — congés CP


def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def load_history() -> list[dict]:
    rows = []
    if HIST_FILE.exists():
        try:
            with HIST_FILE.open(encoding="utf-8") as f:
                rows = list(csv.DictReader(f, delimiter=";"))
        except Exception:
            pass
    return rows


def load_verif() -> dict:
    if VERIF_FILE.exists():
        try:
            return json.loads(VERIF_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def load_paie() -> dict:
    if PAIE_FILE.exists():
        try:
            return json.loads(PAIE_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def load_notes() -> list[dict]:
    if NOTES_HIST_FILE.exists():
        try:
            return json.loads(NOTES_HIST_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return []


def find_codes(value: str) -> list[str]:
    v = value.upper()
    out = []
    for c in CODES:
        key = "PARIF" if c == "PARIFS" else c.upper()
        if key in v:
            out.append(c)
    return out


def classify(value: str) -> str:
    v = value.strip().upper()
    if "NON DISPONIBLE" in v:
        return "non_dispo"
    if v == "OFF":
        return "OFF"
    if v == "ML":
        return "ML"
    if v == "ABS":
        return "ABS"
    if v.startswith("CP"):
        return "CP"
    if TIME_RE.search(v):
        return "travail"
    return "autre"


def analyze(state: dict, history: list[dict]) -> dict:
    """Produit toutes les stats du dashboard."""
    types = Counter()
    codes = Counter()
    debut = Counter()
    fin = Counter()
    par_js = defaultdict(Counter)
    future_days = {}  # jours à venir (non_dispo exclus) pour les tendances

    today = datetime.now().date()

    for ds, val in state.items():
        d = datetime.strptime(ds, "%d/%m/%Y").date()
        js = JOURS[d.weekday()]
        t = classify(val)
        types[t] += 1
        par_js[js][t] += 1
        for c in find_codes(val):
            codes[c] += 1
        m = TIME_RE.search(val)
        if m:
            h1, m1, h2, m2 = m.groups()
            debut[f"{int(h1):02d}h"] += 1
            fin[f"{int(h2):02d}h{m2}"] += 1

    # --- Historique des changements ---
    detect_js = Counter()
    concerne_js = Counter()
    delais = []
    retro = 0
    code_apparitions = Counter()
    deltas = []

    for row in history:
        try:
            dd = datetime.strptime(row["detecte_le"].split()[0], "%d/%m/%Y").date()
            jc = datetime.strptime(row["jour_concerne"], "%d/%m/%Y").date()
        except Exception:
            continue
        detect_js[JOURS[dd.weekday()]] += 1
        concerne_js[JOURS[jc.weekday()]] += 1
        delai = (jc - dd).days
        delais.append(delai)
        if delai < 0:
            retro += 1
        for c in find_codes(row.get("apres", "")):
            code_apparitions[c] += 1
        dm = row.get("delta_minutes", "")
        if dm and dm.lstrip("-").isdigit():
            deltas.append(int(dm))

    # Taux de modification par jour de semaine (sur jours concernés)
    # combien de fois chaque jour-de-semaine a été touché
    tendances = {}
    for js in JOURS:
        tendances[js] = concerne_js.get(js, 0)

    return {
        "today": today,
        "types": types,
        "codes": codes,
        "debut": debut,
        "fin": fin,
        "par_js": par_js,
        "detect_js": detect_js,
        "concerne_js": concerne_js,
        "delais": delais,
        "retro": retro,
        "nb_modifs": len(history),
        "code_apparitions": code_apparitions,
        "deltas": deltas,
        "tendances": tendances,
    }


# ---------------------------------------------------------------------------
# Génération HTML
# ---------------------------------------------------------------------------


def _bar(label, value, maxv, color, suffix=""):
    pct = (value / maxv * 100) if maxv else 0
    return f"""
    <div style="display:flex;align-items:center;margin:6px 0;gap:10px;">
      <div style="width:90px;font-size:13px;color:{C_MUTED};text-align:right;flex-shrink:0;">{_html.escape(str(label))}</div>
      <div style="flex:1;background:{C_CARD2};border-radius:6px;height:22px;position:relative;overflow:hidden;">
        <div style="width:{pct:.0f}%;background:{color};height:100%;border-radius:6px;min-width:2px;"></div>
      </div>
      <div style="width:50px;font-size:13px;font-weight:600;color:{C_TEXT};">{value}{suffix}</div>
    </div>"""


def _card(titre, contenu, emoji=""):
    return f"""
    <div style="background:{C_CARD};border-radius:16px;padding:22px;margin-bottom:18px;">
      <div style="font-size:16px;font-weight:700;color:{C_TEXT};margin-bottom:16px;">
        {emoji} {_html.escape(titre)}
      </div>
      {contenu}
    </div>"""


def _val_color(val: str) -> tuple[str, str]:
    """Retourne (couleur, libellé affiché) pour une valeur de planning."""
    v = val.strip().upper()
    if "NON DISPONIBLE" in v:
        return (C_MUTED, "— non publié —")
    if v == "OFF":
        return (C_GREEN, "OFF")
    if v == "ML":
        return (C_RED, "🤒 Maladie")
    if v == "ABS":
        return (C_ORANGE, "⚠️ Absence")
    if v.startswith("CP"):
        return (C_PURPLE, "🏖️ Congé")
    # Travail : couleur selon codes
    if "FOR" in v:
        return (C_PURPLE, val)
    if "H+" in v:
        return (C_ORANGE, val)
    m = TIME_RE.search(v)
    if m and m.group(4) == "30" and m.group(3) == "22":
        return (C_RED, val)  # fin 22h30 (fret potentiel)
    return (C_BLUE, val)


def render_planning_card(state: dict, today) -> str:
    """Vue calendrier mensuel : planning réel + prédiction, comme le site du perso."""
    from datetime import timedelta
    import calendar as _cal

    jours = {}
    for ds, val in state.items():
        if "NON DISPONIBLE" in val.upper():
            continue
        d = datetime.strptime(ds, "%d/%m/%Y").date()
        jours[d] = ("pub", val)
    derniere_pub = max(jours.keys()) if jours else today
    if cycle_predict is not None:
        for p in cycle_predict.predire_complet(derniere_pub + timedelta(days=1), 56):
            d = p["date"]
            if d not in jours:
                jours[d] = ("pred", p)

    if not jours:
        return _card("Mon planning & prédictions", "<div>Aucune donnée</div>", "P")

    def case_contenu(d):
        entry = jours.get(d)
        if entry is None:
            return (None, None, False)
        kind, data = entry
        if kind == "pub":
            v = data.strip().upper()
            if v == "OFF":
                return ("OFF", None, False)
            if v == "ML":
                return ("🤒 Mal.", C_MALADIE, False)
            if v == "ABS":
                return ("Abs.", C_ABSENCE, False)
            if v.startswith("CP"):
                return ("🏖️ CP", C_CONGE, False)
            m = TIME_RE.search(v)
            if m:
                deb = m.group(1) + ":" + m.group(2)
                fin = m.group(3) + ":" + m.group(4)
                if "FOR" in v:
                    return ("📚 Form.<br>" + deb, C_FORMATION, False)
                if "H+" in v:
                    return (deb + "<br>" + fin + "<br>H+", C_HPLUS, False)
                if (m.group(3) == "22" and m.group(4) == "30") and d.weekday() <= 4:
                    return ("📦 " + deb + "<br>" + fin, C_FRET, False)
                col = C_MATIN if int(m.group(1)) < 12 else C_APREM
                return (deb + "<br>" + fin, col, False)
            return (data[:8], C_BLUE, False)
        else:
            if data["on"]:
                cr = data["creneau"]
                h = data["horaire"].split("-")
                col = C_MATIN if cr == "MATIN" else C_APREM
                return (h[0] + "<br>" + h[1], col, True)
            return ("OFF", None, True)

    toutes = sorted(jours.keys())
    mois_set = sorted({(d.year, d.month) for d in toutes})
    JNOMS = ["L", "M", "M", "J", "V", "S", "D"]
    cal = _cal.Calendar(firstweekday=0)

    mois_html = []
    for (yy, mm) in mois_set:
        titre = MOIS[mm - 1].capitalize() + " " + str(yy)
        th = "".join(
            '<div style="flex:1;text-align:center;font-size:10px;color:' + C_MUTED
            + ';padding:3px 0;">' + j + '</div>' for j in JNOMS
        )
        semaines_html = []
        for semaine in cal.monthdayscalendar(yy, mm):
            cells = []
            for i, day in enumerate(semaine):
                if day == 0:
                    cells.append('<div style="flex:1;aspect-ratio:1;"></div>')
                    continue
                d = datetime(yy, mm, day).date()
                txt, bgcol, est_pred = case_contenu(d)
                is_today = (d == today)
                is_we = i >= 5
                if txt is None:
                    fond = "#15171f" if not is_we else "#111319"
                    cells.append(
                        '<div style="flex:1;aspect-ratio:1;border-radius:6px;background:'
                        + fond + ';padding:3px;"><div style="font-size:10px;color:#44485a;">'
                        + str(day) + '</div></div>'
                    )
                    continue
                if bgcol is None:
                    bg = "#1a1d27"
                    contenu = '<div style="font-size:9px;color:#5a5f70;margin-top:4px;">OFF</div>'
                    num_col = C_MUTED
                else:
                    bg = bgcol
                    contenu = ('<div style="font-size:9px;color:#fff;line-height:1.25;'
                               'margin-top:2px;font-weight:600;">' + txt + '</div>')
                    num_col = "#fff"
                border = ("box-shadow:0 0 0 2px " + C_ACCENT + ";") if is_today else ""
                weight = "700" if is_today else "500"
                if est_pred:
                    # Jour prévisionnel : estompé + contour pointillé + petit badge
                    op = "opacity:0.6;"
                    extra_border = "border:1px dashed rgba(255,255,255,0.35);"
                    badge = ('<div style="position:absolute;top:2px;right:3px;font-size:7px;'
                             'color:rgba(255,255,255,0.75);font-weight:700;letter-spacing:.3px;">prév</div>')
                    pos = "position:relative;"
                else:
                    op = ""
                    extra_border = ""
                    badge = ""
                    pos = ""
                cells.append(
                    '<div style="flex:1;aspect-ratio:1;display:flex;flex-direction:column;'
                    'border-radius:6px;background:' + bg + ';padding:3px;overflow:hidden;'
                    + pos + border + op + extra_border + '">'
                    + badge
                    + '<div style="font-size:10px;color:' + num_col + ';font-weight:' + weight + ';">'
                    + str(day) + '</div>' + contenu + '</div>'
                )
            semaines_html.append(
                '<div style="display:flex;gap:3px;margin-bottom:3px;">' + "".join(cells) + '</div>'
            )
            # Séparateur "réel / prévisionnel" juste après la semaine du dernier jour publié
            jours_sem = [datetime(yy, mm, dj).date() for dj in semaine if dj != 0]
            if derniere_pub in jours_sem:
                semaines_html.append(
                    '<div style="display:flex;align-items:center;gap:8px;margin:8px 0 10px;">'
                    '<div style="flex:1;height:2px;background:linear-gradient(90deg,transparent,#e74c3c);"></div>'
                    '<div style="font-size:10px;font-weight:700;color:#e74c3c;white-space:nowrap;'
                    'text-transform:uppercase;letter-spacing:.5px;">▼ Prévisionnel à partir d\'ici</div>'
                    '<div style="flex:1;height:2px;background:linear-gradient(90deg,#e74c3c,transparent);"></div>'
                    '</div>'
                )
        mois_html.append(
            '<div style="margin-bottom:18px;">'
            '<div style="font-size:14px;font-weight:700;color:' + C_TEXT
            + ';margin-bottom:8px;text-transform:capitalize;">' + titre + '</div>'
            '<div style="display:flex;gap:3px;margin-bottom:4px;">' + th + '</div>'
            + "".join(semaines_html) + '</div>'
        )

    def pastille(col, txt):
        return ('<span style="display:inline-block;width:10px;height:10px;border-radius:3px;'
                'background:' + col + ';vertical-align:middle;margin-right:3px;"></span>' + txt)
    legende = (
        '<div style="font-size:11px;color:' + C_MUTED + ';margin-top:8px;line-height:2;">'
        + pastille(C_MATIN, "matin") + ' &nbsp; '
        + pastille(C_APREM, "après-midi") + ' &nbsp; '
        + pastille(C_FRET, "📦 fret") + ' &nbsp; '
        + pastille(C_HPLUS, "H+ sup") + '<br>'
        + pastille(C_FORMATION, "📚 formation") + ' &nbsp; '
        + pastille(C_CONGE, "🏖️ CP") + ' &nbsp; '
        + pastille(C_MALADIE, "🤒 maladie") + ' &nbsp; '
        + pastille(C_ABSENCE, "absence") + ' &nbsp; '
        '<span style="color:#5a5f70;">OFF = repos</span><br>'
        '<span style="opacity:0.7;">Au-dessus de la ligne <span style="color:#e74c3c;font-weight:700;">rouge</span> = '
        '<strong>planning réel</strong> publié. En dessous (cases en pointillés, badge « prév ») = '
        '<strong>prédictions</strong> (cycle 8 sem. + horaires types sur 19 mois).</span></div>'
    )
    return _card("Mon planning & prédictions", "".join(mois_html) + legende, "📋")


def render_notes_card(notes: list[dict]) -> str:
    """Carte listant l'historique des notes de service reçues."""
    if not notes:
        contenu = f'<div style="font-size:13px;color:{C_MUTED};">Aucune note enregistrée pour le moment. Elles apparaîtront ici dès réception.</div>'
        return _card("Notes de service", contenu, "📄")
    # Tri par date décroissante
    def _key(n):
        try:
            return datetime.strptime(n.get("date", ""), "%d/%m/%Y %H:%M")
        except Exception:
            return datetime.min
    notes_tri = sorted(notes, key=_key, reverse=True)

    def categorie(titre):
        """Retourne (emoji, couleur, label) selon le contenu du titre."""
        t = titre.lower()
        if "paie" in t or "bulletin" in t or "salaire" in t:
            return "💰", C_GREEN, "Paie"
        if "acompte" in t or "avance" in t:
            return "💵", C_HPLUS, "Acompte"
        if "code" in t:
            return "🔑", C_PURPLE, "Codes"
        if "consigne" in t or "service" in t or "pif" in t or "procédure" in t:
            return "📋", C_BLUE, "Service"
        if "planning" in t or "horaire" in t:
            return "📅", C_ORANGE, "Planning"
        return "📄", C_MUTED, "Note"

    rows = []
    for n in notes_tri[:30]:
        titre = _html.escape(n.get("titre", "Document"))
        date_raw = n.get("date", "")
        # séparer date et heure
        parts = date_raw.split(" ")
        jour = _html.escape(parts[0]) if parts else ""
        heure = _html.escape(parts[1]) if len(parts) > 1 else ""
        emoji, coul, label = categorie(titre)
        rows.append(f"""
        <div style="display:flex;align-items:center;gap:12px;padding:12px 13px;border-radius:10px;
                    background:{C_CARD2};margin-bottom:8px;border-left:3px solid {coul};">
          <div style="font-size:22px;line-height:1;">{emoji}</div>
          <div style="flex:1;min-width:0;">
            <div style="font-size:14px;color:{C_TEXT};font-weight:700;margin-bottom:3px;
                        white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">{titre}</div>
            <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;">
              <span style="font-size:10px;font-weight:700;color:{coul};background:{coul}22;
                           padding:2px 7px;border-radius:5px;">{label}</span>
              <span style="font-size:12px;color:{C_NOTE};">{jour}</span>
              <span style="font-size:11px;color:{C_MUTED};">{heure}</span>
            </div>
          </div>
        </div>""")
    entete = (f'<div style="font-size:13px;color:{C_NOTE};margin-bottom:14px;">'
              f'<strong style="color:{C_TEXT};">{len(notes)}</strong> note(s) de service reçue(s) · '
              f'triées de la plus récente à la plus ancienne</div>')
    return _card("Notes de service", entete + "".join(rows), "📄")



def render_verif_card(verif: dict) -> str:
    """Carte vérification paie par MOIS CALENDAIRE : bulletin payé vs heures calculées (CCN annexe VIII)."""
    if not verif:
        return ""
    # Format : clés YYYY-MM (mois du bulletin), champs mois/mois_paye/partiel/nuit/dim/paniers avec 'etat'
    # etat : 'ok' conforme · 'plus' payé+ (avantage) · 'moins' manquant (défaveur) · 'partiel' données incomplètes
    items = sorted(verif.items())

    def pastille(etat):
        if etat == "ok":    return C_GREEN, "✓"
        if etat == "plus":  return C_BLUE, "▲"
        if etat == "moins": return C_ORANGE, "▼"
        return C_MUTED, "◐"

    nb_ok = nb_plus = nb_moins = nb_part = 0
    rows = []
    for bk, v in reversed(items):
        label = v.get("mois", bk)
        mois_paye = v.get("mois_paye", "")
        partiel = v.get("partiel", False)
        lignes = []
        etats = []
        for cle, nom, unite, unite_txt in [("nuit","Nuit","h"," h"), ("dim","Dim.","h"," h"), ("paniers","Paniers",""," panier(s)")]:
            dd = v.get(cle)
            if not dd: continue
            etat = dd.get("etat", "ok")
            etats.append(etat)
            col, sym = pastille(etat)
            ec = dd["ecart"]
            if etat == "ok":        detail = "conforme"
            elif etat == "plus":    detail = f"+{abs(ec):g}{unite_txt}"
            elif etat == "moins":   detail = f"-{abs(ec):g}{unite_txt}"
            else:                   detail = "n/c"
            lignes.append(
                f'<span style="font-size:11px;color:{C_MUTED};">{nom} '
                f'<span style="color:{C_TEXT};font-weight:600;">{dd["paye"]}{unite}</span> '
                f'<span style="color:{col};font-weight:600;">{sym} {detail}</span></span>'
            )
        if partiel:
            nb_part += 1; badge = f'<span style="color:{C_MUTED};">◐</span>'; bordure = "#3a3f4e"
        elif "moins" in etats:
            nb_moins += 1; badge = f'<span style="color:{C_ORANGE};">⚠️</span>'; bordure = C_ORANGE
        elif "plus" in etats:
            nb_plus += 1; badge = f'<span style="color:{C_BLUE};">▲</span>'; bordure = "transparent"
        else:
            nb_ok += 1; badge = '<span style="color:#2ecc71;">✅</span>'; bordure = "transparent"
        suffixe = ' <span style="color:#5a5f70;">· émargement incomplet</span>' if partiel else ''
        rows.append(f"""
        <div style="background:{C_CARD2};border-radius:9px;padding:11px 13px;margin-bottom:7px;border-left:3px solid {bordure};">
          <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:3px;">
            <span style="font-size:13px;font-weight:700;color:{C_TEXT};text-transform:capitalize;">{badge} {label}</span>
            <span style="font-size:12px;color:{C_BLUE};font-weight:600;">net {v.get("net","?")}&euro;</span>
          </div>
          <div style="font-size:10px;color:#5a5f70;margin-bottom:6px;">payé sur {mois_paye}{suffixe}</div>
          <div style="display:flex;gap:14px;flex-wrap:wrap;">{" ".join(lignes)}</div>
        </div>""")

    verifiables = nb_ok + nb_plus + nb_moins
    if nb_moins == 0:
        verdict = (f'<div style="background:#16331f;border:1px solid #2ecc71;border-radius:9px;'
                   f'padding:12px 14px;margin-bottom:14px;">'
                   f'<div style="font-size:14px;font-weight:700;color:#2ecc71;margin-bottom:3px;">'
                   f'✅ Aucune erreur en ta défaveur</div>'
                   f'<div style="font-size:12px;color:{C_NOTE};line-height:1.6;">'
                   f'Sur {verifiables} mois entièrement vérifiables : <strong style="color:{C_TEXT};">{nb_ok} parfaitement conformes</strong>'
                   f'{f" · {nb_plus} avec un léger écart en ta faveur" if nb_plus else ""}. '
                   f'{f"{nb_part} mois en données partielles (émargement incomplet, non comptés)." if nb_part else ""}</div></div>')
    else:
        verdict = (f'<div style="background:#33261a;border:1px solid {C_ORANGE};border-radius:9px;'
                   f'padding:12px 14px;margin-bottom:14px;">'
                   f'<div style="font-size:14px;font-weight:700;color:{C_ORANGE};margin-bottom:3px;">'
                   f'⚠️ {nb_moins} mois à vérifier</div>'
                   f'<div style="font-size:12px;color:{C_NOTE};line-height:1.6;">'
                   f'{nb_ok} conformes · {nb_plus} en ta faveur · {nb_moins} où le calcul dépasse le payé.</div></div>')

    note = (f'<div style="background:{C_CARD2};border-radius:9px;padding:12px 14px;margin-top:10px;'
            f'font-size:12px;color:{C_NOTE};line-height:1.7;">'
            f'<div style="margin-bottom:6px;"><strong style="color:{C_TEXT};">Barèmes CCN Prévention/Sécurité annexe VIII</strong> '
            f'(IDCC 1351) : nuit +25&nbsp;% (21h-6h) · dimanche +50&nbsp;% · panier 6,87&euro; (1/jour travaillé)</div>'
            f'<div><span style="color:{C_GREEN};font-weight:700;">✅ conforme</span> = mon calcul = ton bulletin &nbsp;·&nbsp; '
            f'<span style="color:{C_BLUE};font-weight:700;">▲</span> = payé un peu plus que mon calcul (en ta faveur) &nbsp;·&nbsp; '
            f'<span style="color:{C_ORANGE};font-weight:700;">▼</span> = à vérifier &nbsp;·&nbsp; '
            f'<span style="color:{C_MUTED};font-weight:700;">◐</span> = mois dont l\'historique est incomplet</div>'
            f'<div style="margin-top:4px;color:{C_MUTED};">Écarts en <strong>heures</strong> (nuit, dimanche) ou <strong>paniers</strong> — '
            f'<u>jamais en euros</u>.</div>'
            f'<div style="margin-top:6px;color:{C_MUTED};">Les variables (nuit, dimanche, paniers) sont décomptées sur le '
            f'<strong>mois calendaire travaillé</strong> et payées le mois suivant.</div></div>')
    return _card("Vérification paie", verdict + "".join(rows) + note, "🔍")


def render_previsionnel_card(state: dict, today) -> str:
    """Carte prévisionnel de salaire du mois en cours, calculé depuis le planning."""
    try:
        import previsionnel_paie as pp
    except Exception:
        return ""
    try:
        p = pp.previsionnel_mois(state, today.year, today.month)
    except Exception:
        return ""
    if p["jours_connus"] == 0:
        return ""

    MOISN = ["", "janvier", "février", "mars", "avril", "mai", "juin", "juillet",
             "août", "septembre", "octobre", "novembre", "décembre"]
    mois_lbl = f"{MOISN[today.month]} {today.year}"
    complet = p["complet"]
    statut = ("mois complet" if complet
              else f"{p['jours_restants']} jour(s) encore inconnu(s)")

    # Bandeau de fiabilité selon présence de CP / maladie / absence
    fiab = p.get("fiabilite", "bonne")
    alertes = []
    if p.get("jours_cp"):
        alertes.append(f'{p["jours_cp"]} jour(s) de congés payés')
    if p.get("jours_maladie"):
        alertes.append(f'{p["jours_maladie"]} jour(s) de maladie')
    if p.get("jours_absence"):
        alertes.append(f'{p["jours_absence"]} jour(s) d\'absence')
    if fiab == "reduite":
        fiab_html = (f'<div style="background:#33261a;border:1px solid {C_ORANGE};border-radius:9px;'
                     f'padding:10px 13px;margin-bottom:12px;font-size:12px;color:{C_NOTE};line-height:1.5;">'
                     f'<strong style="color:{C_ORANGE};">⚠️ Fiabilité réduite ce mois</strong> — '
                     f'présence de {", ".join(alertes)}. Les congés et absences sont payés '
                     f'séparément (parfois avec primes), donc le net réel peut s\'écarter nettement de l\'estimation.</div>')
    elif fiab == "moyenne":
        fiab_html = (f'<div style="background:#1a2633;border:1px solid {C_BLUE};border-radius:9px;'
                     f'padding:10px 13px;margin-bottom:12px;font-size:12px;color:{C_NOTE};line-height:1.5;">'
                     f'<strong style="color:{C_BLUE};">ℹ️ {", ".join(alertes)}</strong> — '
                     f'maintien de salaire indemnisé à part, le net réel peut varier un peu.</div>')
    else:
        fiab_html = (f'<div style="background:#16331f;border:1px solid #2ecc71;border-radius:9px;'
                     f'padding:10px 13px;margin-bottom:12px;font-size:12px;color:{C_NOTE};line-height:1.5;">'
                     f'<strong style="color:#2ecc71;">✓ Mois standard</strong> — '
                     f'pas de congés ni d\'absence, l\'estimation est fiable (±quelques dizaines d\'€ de primes variables).</div>')

    # Grande valeur net estimée
    net = f"{p['net_estime']:.0f}"
    brut = f"{p['brut']:.0f}"

    # Barre de progression du mois
    import calendar
    nb_j = calendar.monthrange(today.year, today.month)[1]
    pct = int(100 * p["jours_connus"] / nb_j)

    lignes = p["lignes"]
    def ligne(nom, val, detail=""):
        det = f'<span style="color:{C_MUTED};font-size:11px;"> {detail}</span>' if detail else ""
        return (f'<div style="display:flex;justify-content:space-between;padding:5px 0;'
                f'border-bottom:1px solid #22262f;">'
                f'<span style="font-size:12px;color:{C_NOTE};">{nom}{det}</span>'
                f'<span style="font-size:13px;color:{C_TEXT};font-weight:600;">{val:.2f}&euro;</span></div>')

    corps = (
        # Bandeau net estimé
        f'<div style="background:linear-gradient(135deg,#1a3a2e,#16331f);border:1px solid #2ecc71;'
        f'border-radius:12px;padding:16px;margin-bottom:14px;text-align:center;">'
        f'<div style="font-size:12px;color:{C_NOTE};text-transform:uppercase;letter-spacing:1px;">'
        f'Net estimé · {mois_lbl}</div>'
        f'<div style="font-size:34px;font-weight:800;color:#2ecc71;margin:4px 0;">~{net}&euro;</div>'
        f'<div style="font-size:12px;color:{C_MUTED};">brut ~{brut}&euro; · {statut}</div>'
        # barre de progression
        f'<div style="background:#0d0f14;border-radius:6px;height:7px;margin-top:10px;overflow:hidden;">'
        f'<div style="background:#2ecc71;height:100%;width:{pct}%;"></div></div>'
        f'<div style="font-size:10px;color:{C_MUTED};margin-top:4px;">{p["jours_connus"]}/{nb_j} jours du mois connus</div>'
        f'</div>'

        + fiab_html +

        # Compteurs clés
        f'<div style="display:flex;gap:8px;margin-bottom:14px;flex-wrap:wrap;">'
        f'<div style="flex:1;min-width:80px;background:{C_CARD2};border-radius:9px;padding:10px;text-align:center;">'
        f'<div style="font-size:20px;font-weight:700;color:{C_BLUE};">{p["h_total"]:.0f}h</div>'
        f'<div style="font-size:10px;color:{C_MUTED};">travaillées</div></div>'
        f'<div style="flex:1;min-width:80px;background:{C_CARD2};border-radius:9px;padding:10px;text-align:center;">'
        f'<div style="font-size:20px;font-weight:700;color:{C_ORANGE};">{p["h_nuit"]:.1f}h</div>'
        f'<div style="font-size:10px;color:{C_MUTED};">de nuit</div></div>'
        f'<div style="flex:1;min-width:80px;background:{C_CARD2};border-radius:9px;padding:10px;text-align:center;">'
        f'<div style="font-size:20px;font-weight:700;color:{C_PURPLE};">{p["h_dimanche"]:.1f}h</div>'
        f'<div style="font-size:10px;color:{C_MUTED};">dimanche</div></div>'
        f'<div style="flex:1;min-width:80px;background:{C_CARD2};border-radius:9px;padding:10px;text-align:center;">'
        f'<div style="font-size:20px;font-weight:700;color:{C_HPLUS};">{p["paniers"]}</div>'
        f'<div style="font-size:10px;color:{C_MUTED};">paniers</div></div>'
        f'</div>'

        # Détail du calcul
        f'<div style="background:{C_CARD2};border-radius:9px;padding:12px 14px;">'
        f'<div style="font-size:12px;font-weight:700;color:{C_TEXT};margin-bottom:6px;">Détail de l\'estimation</div>'
        + ligne("Salaire de base", lignes["base"], "151,67h")
        + ligne("Majoration nuit", lignes["maj_nuit"], f'{p["h_nuit"]:.1f}h × 3,55€')
        + ligne("Majoration dimanche", lignes["maj_dimanche"], f'{p["h_dimanche"]:.1f}h × 7,10€')
        + ligne("Paniers", lignes["paniers"], f'{p["paniers"]} × 6,87€')
        + ligne("Prime habillage", lignes["habillage"], f'{p["paniers"]} × 2,16€')
        + f'<div style="display:flex;justify-content:space-between;padding:8px 0 2px;margin-top:4px;">'
          f'<span style="font-size:13px;color:{C_TEXT};font-weight:700;">Brut estimé</span>'
          f'<span style="font-size:15px;color:#2ecc71;font-weight:800;">{p["brut"]:.2f}&euro;</span></div>'
        f'</div>'

        f'<div style="font-size:11px;color:{C_MUTED};margin-top:10px;line-height:1.6;">'
        f'⚠️ <strong>Estimation</strong> basée sur les horaires déjà au planning. '
        f'Hors indemnité transport, prime de décalage, maintien maladie et ajustement du compteur 140H. '
        f'Elle se précise à chaque nouveau jour qui tombe.</div>'
    )
    return _card("Prévisionnel de paie", corps, "🔮")


def render_paie_card(paie: dict) -> str:
    """Carte suivi paie : compteurs mensuels des variables."""
    if not paie:
        return ""
    MOIS_FR_COURT = ["jan","fév","mar","avr","mai","juin","juil","aoû","sep","oct","nov","déc"]
    # Derniers 6 mois
    items = list(paie.items())[-6:]
    rows = []
    for mkey, e in reversed(items):
        mm, yy = mkey.split("/")
        label = f"{MOIS_FR_COURT[int(mm)-1]} {yy}"
        htot = e.get("h_trav", 0)
        rows.append(f"""
        <div style="background:{C_CARD2};border-radius:10px;padding:14px;margin-bottom:8px;">
          <div style="display:flex;justify-content:space-between;align-items:baseline;margin-bottom:8px;">
            <div style="font-size:14px;font-weight:700;color:{C_TEXT};text-transform:capitalize;">{label}</div>
            <div style="font-size:13px;color:{C_BLUE};font-weight:600;">{htot}h travaillées</div>
          </div>
          <div style="display:flex;gap:6px;flex-wrap:wrap;font-size:11px;">
            <span style="background:#2d3142;color:{C_MUTED};padding:3px 8px;border-radius:5px;">🌙 {e.get('nuit',0)}h nuit</span>
            <span style="background:#2d3142;color:{C_MUTED};padding:3px 8px;border-radius:5px;">📅 {e.get('dim',0)}h dim</span>
            <span style="background:#2d3142;color:{C_MUTED};padding:3px 8px;border-radius:5px;">🍽️ {e.get('paniers',0)} paniers</span>
            <span style="background:#2d3142;color:{C_MUTED};padding:3px 8px;border-radius:5px;">⏰ {e.get('hplus',0)} H+</span>
            {f'<span style="background:#4a2d2d;color:#e99;padding:3px 8px;border-radius:5px;">🤒 {e.get("ml",0)} ML</span>' if e.get('ml',0) else ''}
            {f'<span style="background:#4a3d2d;color:#eb9;padding:3px 8px;border-radius:5px;">🔀 {e.get("sms_modif",0)} modifs</span>' if e.get('sms_modif',0) else ''}
          </div>
        </div>""")
    note = f'<div style="font-size:11px;color:{C_MUTED};margin-top:4px;line-height:1.5;">Compteurs recalculés depuis tes relevés d\'activité. Heures de nuit = 21h-6h. 1 panier/jour travaillé. À croiser avec ta fiche de paie.</div>'
    return _card("Suivi paie (variables)", "".join(rows) + note, "💰")


def render_dashboard(a: dict, state: dict = None, notes: list = None, paie: dict = None, verif: dict = None) -> str:
    """Construit le HTML complet du dashboard à partir des stats analysées."""
    today = a["today"]
    maj = datetime.now().strftime("%d/%m/%Y à %Hh%M")
    state = state or {}
    notes = notes or []
    paie = paie or {}
    verif = verif or {}

    # --- Carte : répartition des jours ---
    type_labels = {"travail": ("Travaillés", C_BLUE), "OFF": ("Repos (OFF)", C_GREEN),
                   "non_dispo": ("Non publié", C_MUTED), "ML": ("Maladie", C_RED),
                   "ABS": ("Absence", C_ORANGE), "CP": ("Congés", C_PURPLE),
                   "autre": ("Autre", C_MUTED)}
    maxt = max(a["types"].values()) if a["types"] else 1
    bars = ""
    for t, n in a["types"].most_common():
        lbl, col = type_labels.get(t, (t, C_MUTED))
        bars += _bar(lbl, n, maxt, col)
    card_types = _card("Répartition des journées", bars, "📅")

    # --- Carte : heures de fin ---
    maxf = max(a["fin"].values()) if a["fin"] else 1
    bars = ""
    for h, n in sorted(a["fin"].items()):
        col = C_RED if h.startswith("22h30") else C_BLUE
        bars += _bar(h, n, maxf, col)
    note_fin = f'<div style="font-size:12px;color:{C_MUTED};margin-top:10px;">🔴 22h30 = ton heure de fin la plus fréquente (créneaux FRET potentiels en semaine)</div>'
    card_fin = _card("Heures de fin de vacation", bars + note_fin, "🕐")

    # --- Carte : heures de début ---
    maxd = max(a["debut"].values()) if a["debut"] else 1
    bars = ""
    for h, n in sorted(a["debut"].items()):
        bars += _bar(h, n, maxd, C_ORANGE)
    card_debut = _card("Heures de début de vacation", bars, "🌅")

    # --- Carte : codes spéciaux ---
    code_desc = {"H+": "Heures sup", "PARIFS": "Affecté PARIF", "FOR": "Formation",
                 "FRET": "Fret DHL", "RET_VOL": "Retard vol", "ABS": "Absence",
                 "ML": "Maladie", "PM": "Après-midi", "DEC_48h": "Changé <48h"}
    if a["codes"]:
        maxc = max(a["codes"].values())
        bars = ""
        for c, n in a["codes"].most_common():
            bars += _bar(code_desc.get(c, c), n, maxc, C_PURPLE, "x")
        card_codes = _card("Codes rencontrés (planning actuel)", bars, "🏷️")
    else:
        card_codes = ""

    # --- Cartes planning + notes ---
    card_planning = render_planning_card(state, today)
    card_notes = render_notes_card(notes)
    card_paie = render_paie_card(paie)
    card_verif = render_verif_card(verif)
    card_previsionnel = render_previsionnel_card(state, today)

    # --- Carte : TENDANCES des modifications ---
    nb = a["nb_modifs"]
    concerne = a["concerne_js"]
    detect = a["detect_js"]
    tend_html = ""

    if nb >= 5:
        # Jours les plus modifiés
        maxcon = max(concerne.values()) if concerne else 1
        bars_con = ""
        for js in JOURS:
            n = concerne.get(js, 0)
            col = C_RED if n == maxcon and n > 0 else C_ORANGE
            bars_con += _bar(js.capitalize(), n, maxcon, col, "x")

        # Quand détectées
        maxdet = max(detect.values()) if detect else 1
        bars_det = ""
        for js in JOURS:
            n = detect.get(js, 0)
            bars_det += _bar(js.capitalize(), n, maxdet, C_BLUE, "x")

        # Stats délai
        delais = a["delais"]
        retro = a["retro"]
        futur = sum(1 for d in delais if d > 0)
        import statistics
        fut_pos = [d for d in delais if d >= 0]
        med = int(statistics.median(fut_pos)) if fut_pos else 0

        tend_html = f"""
        <div style="font-size:14px;color:{C_TEXT};font-weight:600;margin:4px 0 10px;">📍 Quels jours sont le plus souvent modifiés</div>
        {bars_con}
        <div style="font-size:14px;color:{C_TEXT};font-weight:600;margin:20px 0 10px;">📆 Quand les modifs tombent (jour de détection)</div>
        {bars_det}
        <div style="display:flex;gap:12px;margin-top:20px;flex-wrap:wrap;">
          <div style="flex:1;min-width:120px;background:{C_CARD2};border-radius:10px;padding:14px;text-align:center;">
            <div style="font-size:26px;font-weight:800;color:{C_ORANGE};">{retro}</div>
            <div style="font-size:12px;color:{C_MUTED};">modifs rétroactives<br>(après coup → paie)</div>
          </div>
          <div style="flex:1;min-width:120px;background:{C_CARD2};border-radius:10px;padding:14px;text-align:center;">
            <div style="font-size:26px;font-weight:800;color:{C_BLUE};">~{med}j</div>
            <div style="font-size:12px;color:{C_MUTED};">délai médian<br>avant le jour modifié</div>
          </div>
          <div style="flex:1;min-width:120px;background:{C_CARD2};border-radius:10px;padding:14px;text-align:center;">
            <div style="font-size:26px;font-weight:800;color:{C_GREEN};">{nb}</div>
            <div style="font-size:12px;color:{C_MUTED};">modifs enregistrées<br>au total</div>
          </div>
        </div>
        <div style="font-size:12px;color:{C_MUTED};margin-top:14px;line-height:1.5;">
          ⚠️ Tendances basées sur {nb} modifications. Plus l'historique grandit, plus elles sont fiables.
          Ce ne sont pas des prédictions certaines : le planning dépend de décisions humaines (absences, besoins ponctuels).
        </div>"""
    else:
        tend_html = f"""<div style="font-size:14px;color:{C_MUTED};line-height:1.6;">
          Encore trop peu de modifications enregistrées ({nb}) pour dégager des tendances fiables.
          Le système accumule les données à chaque changement détecté — reviens dans quelques semaines ! 📈
        </div>"""
    card_tend = _card("Tendances des modifications", tend_html, "🔮")

    # --- En-tête avec compteurs ---
    n_trav = a["types"].get("travail", 0)
    n_off = a["types"].get("OFF", 0)
    n_pub = sum(v for k, v in a["types"].items() if k != "non_dispo")

    return f"""<!DOCTYPE html>
<html lang="fr"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Dashboard Planning</title>
<style>
  * {{ box-sizing:border-box; margin:0; padding:0; }}
  body {{ background:{C_BG}; color:{C_TEXT}; font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif; padding:20px; }}
  .wrap {{ max-width:720px; margin:0 auto; }}
  a {{ color:{C_ACCENT}; }}
  .tabs {{ display:flex; gap:6px; margin-bottom:18px; position:sticky; top:0; z-index:10;
           background:{C_BG}; padding:10px 0; flex-wrap:wrap; }}
  .tab {{ flex:1; min-width:72px; text-align:center; padding:11px 8px; border-radius:11px;
          background:{C_CARD}; color:{C_MUTED}; font-size:13px; font-weight:600; cursor:pointer;
          border:1px solid transparent; transition:all .15s; user-select:none; }}
  .tab:hover {{ background:{C_CARD2}; color:{C_TEXT}; }}
  .tab.active {{ background:{C_CARD2}; color:{C_TEXT}; border-color:{C_ACCENT}; }}
  .tab .ic {{ font-size:18px; display:block; margin-bottom:2px; }}
  .page {{ display:none; }}
  .page.active {{ display:block; animation:fade .2s; }}
  @keyframes fade {{ from {{ opacity:0; }} to {{ opacity:1; }} }}
</style></head>
<body>
<div class="wrap">

  <div style="text-align:center;padding:20px 0 28px;">
    <div style="font-size:38px;">🛡️</div>
    <h1 style="font-size:24px;font-weight:800;margin-top:8px;">Dashboard Planning</h1>
    <div style="font-size:13px;color:{C_MUTED};margin-top:6px;">Sûreté Aéroport Nantes · analyse de {sum(a['types'].values())} jours</div>
  </div>

  <div style="display:flex;gap:12px;margin-bottom:18px;flex-wrap:wrap;">
    <div style="flex:1;min-width:100px;background:{C_CARD};border-radius:14px;padding:18px;text-align:center;">
      <div style="font-size:28px;font-weight:800;color:{C_BLUE};">{n_trav}</div>
      <div style="font-size:12px;color:{C_MUTED};">jours travaillés</div>
    </div>
    <div style="flex:1;min-width:100px;background:{C_CARD};border-radius:14px;padding:18px;text-align:center;">
      <div style="font-size:28px;font-weight:800;color:{C_GREEN};">{n_off}</div>
      <div style="font-size:12px;color:{C_MUTED};">jours de repos</div>
    </div>
    <div style="flex:1;min-width:100px;background:{C_CARD};border-radius:14px;padding:18px;text-align:center;">
      <div style="font-size:28px;font-weight:800;color:{C_ORANGE};">{a['nb_modifs']}</div>
      <div style="font-size:12px;color:{C_MUTED};">modifs suivies</div>
    </div>
  </div>

  <div class="tabs">
    <div class="tab active" data-page="planning"><span class="ic">📋</span>Planning</div>
    <div class="tab" data-page="paie"><span class="ic">💰</span>Paie</div>
    <div class="tab" data-page="notes"><span class="ic">📝</span>Notes</div>
    <div class="tab" data-page="stats"><span class="ic">📊</span>Stats</div>
  </div>

  <div class="page active" id="page-planning">
    {card_planning}
  </div>

  <div class="page" id="page-paie">
    {card_previsionnel}
    {card_verif}
    {card_paie}
  </div>

  <div class="page" id="page-notes">
    {card_notes}
  </div>

  <div class="page" id="page-stats">
    {card_tend}
    {card_types}
    {card_fin}
    {card_debut}
    {card_codes}
  </div>

  <div style="text-align:center;font-size:12px;color:{C_MUTED};padding:16px 0;">
    Mis à jour le {maj} · 🤖 planning_watch
  </div>

</div>
<script>
  (function() {{
    var tabs = document.querySelectorAll('.tab');
    function show(name) {{
      document.querySelectorAll('.page').forEach(function(p) {{ p.classList.remove('active'); }});
      tabs.forEach(function(t) {{ t.classList.remove('active'); }});
      var pg = document.getElementById('page-' + name);
      if (pg) pg.classList.add('active');
      document.querySelector('.tab[data-page="' + name + '"]').classList.add('active');
      try {{ localStorage.setItem('dash_tab', name); }} catch(e) {{}}
      if (location.hash !== '#' + name) history.replaceState(null, '', '#' + name);
      window.scrollTo(0, 0);
    }}
    tabs.forEach(function(t) {{
      t.addEventListener('click', function() {{ show(t.dataset.page); }});
    }});
    var start = (location.hash || '').replace('#', '');
    if (!start) {{ try {{ start = localStorage.getItem('dash_tab') || 'planning'; }} catch(e) {{ start = 'planning'; }} }}
    if (document.getElementById('page-' + start)) show(start);
  }})();
</script>
</body></html>"""


def generate(state=None, history=None, notes=None, paie=None, verif=None) -> str:
    """Génère le dashboard.html et retourne son chemin."""
    if state is None:
        state = load_state()
    if history is None:
        history = load_history()
    if notes is None:
        notes = load_notes()
    if paie is None:
        paie = load_paie()
    if verif is None:
        verif = load_verif()
    a = analyze(state, history)
    html = render_dashboard(a, state=state, notes=notes, paie=paie, verif=verif)
    DASHBOARD_FILE.write_text(html, encoding="utf-8")
    return str(DASHBOARD_FILE)


if __name__ == "__main__":
    path = generate()
    print(f"Dashboard généré : {path}")
