#!/usr/bin/env python3
"""
planning_watch.py — Surveillance du planning sur le site du personnel HUB SAFE.

Fonctionnalités :
  1. Connexion au site avec tes identifiants
  2. Gestion des documents bloquants ("prendre en compte") :
     téléchargement + envoi sur Discord, notification, validation auto en option
  3. Extraction du planning (date -> horaire)
  4. Comparaison avec la dernière version et notification Discord + Email
  5. Synchronisation avec Google Agenda

À lancer via cron (voir README).
"""

import json
import logging
import os
import re
import smtplib
import sys
from datetime import datetime, time as dtime, timedelta
from email.mime.text import MIMEText
from pathlib import Path
from urllib.parse import urljoin, urlparse, unquote

import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

SCRIPT_DIR = Path(__file__).resolve().parent
load_dotenv(SCRIPT_DIR / ".env")

BASE_URL = os.getenv("SITE_URL", "https://sitedupersonnel-surete-nantes.hubsafe.eu").rstrip("/")
LOGIN_PAGE = os.getenv("LOGIN_PAGE", "/")
PLANNING_PAGE = os.getenv("PLANNING_PAGE", "")

SITE_LOGIN = os.getenv("SITE_LOGIN", "")
SITE_PASSWORD = os.getenv("SITE_PASSWORD", "")

# Webhook du channel "alertes planning"
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL", "")
# Webhook du channel dédié aux documents (peut être le même)
DISCORD_DOCS_WEBHOOK_URL = os.getenv("DISCORD_DOCS_WEBHOOK_URL", DISCORD_WEBHOOK_URL)
# Ping Discord : ton ID utilisateur (mode dev > clic droit sur ton pseudo >
# Copier l'identifiant), ou "@here" / "@everyone"
DISCORD_PING = os.getenv("DISCORD_PING", "").strip()
# true = ping seulement pour les changements urgents (DEC_48h, annotation
# retirée, jour passé) ; false = ping à chaque changement
DISCORD_PING_URGENT_ONLY = os.getenv("DISCORD_PING_URGENT_ONLY", "false").lower() == "true"

SMTP_HOST = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
EMAIL_TO = os.getenv("EMAIL_TO", "")

# --- Google Agenda ---
GCAL_ENABLED = os.getenv("GCAL_ENABLED", "false").lower() == "true"
GCAL_CALENDAR_ID = os.getenv("GCAL_CALENDAR_ID", "")
GCAL_SERVICE_ACCOUNT_FILE = os.getenv(
    "GCAL_SERVICE_ACCOUNT_FILE", str(SCRIPT_DIR / "service_account.json")
)
GCAL_TIMEZONE = os.getenv("GCAL_TIMEZONE", "Europe/Paris")
GCAL_EVENT_PREFIX = os.getenv("GCAL_EVENT_PREFIX", "🛡️ Travail")
GCAL_SYNC_CP = os.getenv("GCAL_SYNC_CP", "true").lower() == "true"

STATE_FILE = SCRIPT_DIR / "planning_state.json"
PENDING_ACK_FILE = SCRIPT_DIR / "pending_ack.txt"
LOG_FILE = SCRIPT_DIR / "planning_watch.log"
# Historique des changements rétroactifs (pour vérifier la fiche de paie)
RETRO_CSV = SCRIPT_DIR / "historique_retroactif.csv"

NOTIFY_NEW_WEEKS = os.getenv("NOTIFY_NEW_WEEKS", "true").lower() == "true"

# Documents bloquants : false = notification, true = validation automatique
AUTO_ACKNOWLEDGE = os.getenv("AUTO_ACKNOWLEDGE", "false").lower() == "true"

ACK_PATTERNS = re.compile(r"prendre\s+en\s+compte|pris\s+connaissance|j'accepte|valider", re.I)

# --- Notes / documents internes (page "mes-notes") ---
NOTES_ENABLED = os.getenv("NOTES_ENABLED", "true").lower() == "true"
NOTES_LIST_URL = BASE_URL + "/personnel/v2/ajax/afficherNotes.php"
NOTES_MARKLU_URL = BASE_URL + "/personnel/v2/ajax/noteLue.php"
NOTES_DOC_URL = os.getenv(
    "NOTES_DOC_URL",
    "https://surete-nantes.hubsafe.eu/module/site_personnels/espace_entreprise/document/",
)
NOTES_REFERER = BASE_URL + "/mes-informations-a-voir/mes-notes"
# Valider automatiquement "Pris en compte" (true) ou seulement notifier (false)
NOTES_AUTO_MARKLU = os.getenv("NOTES_AUTO_MARKLU", "false").lower() == "true"
# Fichier mémoire des notes déjà traitées (anti-doublon)
NOTES_SEEN_FILE = SCRIPT_DIR / "notes_seen.json"
DOC_LINK_PATTERNS = re.compile(r"\.(pdf|docx?|xlsx?|pptx?|png|jpe?g)(\?|$)|t[ée]l[ée]charg|download|/document", re.I)
UNAVAILABLE_MARKER = "planning non disponible"
DATE_RE = re.compile(r"\b(\d{2}/\d{2}/\d{4})\b")
TIME_RANGE_RE = re.compile(r"(\d{1,2})[:hH](\d{2})\s*-\s*(\d{1,2})[:hH](\d{2})")

MAX_DISCORD_FILE_SIZE = 24 * 1024 * 1024  # limite Discord ~25 Mo

# --- Annotations du planning (H+, DEC_48h, PARIFS, ...) ---
# Éditables dans annotations.json : ajoute simplement une entrée pour
# chaque nouveau code que tu découvres, pas besoin de toucher au script.
ANNOTATIONS_FILE = SCRIPT_DIR / "annotations.json"
DEFAULT_ANNOTATIONS = {
    "H+": {"emoji": "⏰", "label": "Heure(s) supplémentaire(s)", "short": "Heures supp", "urgent": False},
    "DEC_48h": {"emoji": "🚨", "label": "Changement de planning sous 48h", "short": "Changé <48h", "urgent": True},
    "PARIFS": {"emoji": "🚗", "label": "Affecté(e) au PARIF", "short": "PARIF", "urgent": False},
}


def load_annotations() -> dict:
    if ANNOTATIONS_FILE.exists():
        try:
            return json.loads(ANNOTATIONS_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return DEFAULT_ANNOTATIONS


ANNOTATIONS = load_annotations()


def find_annotations(value: str) -> list[tuple[str, dict]]:
    """Retourne les annotations présentes dans un horaire, ex: '14:00-23:00 DEC_48h H+'."""
    found = []
    for code, info in ANNOTATIONS.items():
        if re.search(re.escape(code), value, re.I):
            found.append((code, info))
    return found


def explain(value: str) -> str:
    """Ajoute entre parenthèses la signification des annotations trouvées."""
    found = find_annotations(value)
    if not found:
        return value
    labels = ", ".join(f"{info.get('emoji', '')} {info['label']}".strip() for _, info in found)
    return f"{value} ({labels})"


def is_urgent(value: str) -> bool:
    return any(info.get("urgent") for _, info in find_annotations(value))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("planning_watch")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux armv7l) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    )
}


class InterstitialBlocked(Exception):
    """Un document bloque l'accès au planning (notification déjà envoyée)."""


# ---------------------------------------------------------------------------
# Connexion
# ---------------------------------------------------------------------------


def login(session: requests.Session) -> None:
    """Se connecte au site en détectant automatiquement le formulaire de login."""
    url = urljoin(BASE_URL + "/", LOGIN_PAGE.lstrip("/"))
    resp = session.get(url, timeout=30)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    form = None
    for f in soup.find_all("form"):
        if f.find("input", {"type": "password"}):
            form = f
            break
    if form is None:
        raise RuntimeError("Formulaire de connexion introuvable sur la page de login.")

    payload = {}
    login_field_done = False
    for inp in form.find_all("input"):
        name = inp.get("name")
        if not name:
            continue
        itype = (inp.get("type") or "text").lower()
        if itype == "password":
            payload[name] = SITE_PASSWORD
        elif itype in ("text", "email") and not login_field_done:
            payload[name] = SITE_LOGIN
            login_field_done = True
        elif itype in ("hidden", "submit"):
            payload[name] = inp.get("value", "")

    action = form.get("action") or url
    post_url = urljoin(resp.url, action)

    resp = session.post(post_url, data=payload, timeout=30)
    resp.raise_for_status()

    if "Déconnexion" not in resp.text and "Deconnexion" not in resp.text:
        raise RuntimeError(
            "La connexion semble avoir échoué (pas de lien 'Déconnexion' après le POST). "
            "Vérifie SITE_LOGIN / SITE_PASSWORD dans le fichier .env"
        )
    log.info("Connexion réussie.")


# ---------------------------------------------------------------------------
# Documents bloquants ("prendre en compte")
# ---------------------------------------------------------------------------


def find_interstitial(html: str) -> dict | None:
    """
    Détecte une page 'document à prendre en compte'.

    Retourne None si la page contient bien le planning, sinon un dict avec
    le titre, le formulaire de validation et les liens vers les documents.
    """
    if len(DATE_RE.findall(html)) >= 5:
        return None  # la page contient déjà le planning

    soup = BeautifulSoup(html, "html.parser")

    for form in soup.find_all("form"):
        button = None
        for b in form.find_all(["button", "input"]):
            label = b.get_text(" ", strip=True) if b.name == "button" else (b.get("value") or "")
            if ACK_PATTERNS.search(label):
                button = b
                break
        if button is None:
            continue

        payload = {}
        for inp in form.find_all("input"):
            name = inp.get("name")
            if name and (inp.get("type") or "").lower() != "password":
                payload[name] = inp.get("value", "")
        if button.name == "button" and button.get("name"):
            payload[button["name"]] = button.get("value", "")

        title_tag = soup.find(["h1", "h2", "h3"])
        title = title_tag.get_text(" ", strip=True) if title_tag else "Document sans titre"

        # Liens vers le(s) document(s) joint(s) sur la page
        doc_links = []
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if DOC_LINK_PATTERNS.search(href):
                doc_links.append(urljoin(BASE_URL + "/", href))
        for tag in soup.find_all(["iframe", "embed", "object"]):
            src = tag.get("src") or tag.get("data")
            if src and DOC_LINK_PATTERNS.search(src):
                doc_links.append(urljoin(BASE_URL + "/", src))

        return {
            "title": title,
            "action": urljoin(BASE_URL + "/", form.get("action") or ""),
            "payload": payload,
            "doc_links": list(dict.fromkeys(doc_links)),  # dédoublonnés, ordre conservé
        }

    return None


def filename_from_response(url: str, resp: requests.Response) -> str:
    """Devine un nom de fichier depuis l'en-tête ou l'URL."""
    cd = resp.headers.get("Content-Disposition", "")
    m = re.search(r'filename\*?=(?:UTF-8\'\')?"?([^";]+)"?', cd)
    if m:
        return unquote(m.group(1)).strip()
    name = Path(urlparse(url).path).name
    if name:
        return unquote(name)
    ctype = resp.headers.get("Content-Type", "")
    ext = ".pdf" if "pdf" in ctype else ".bin"
    return f"document{ext}"


def send_documents_to_discord(session: requests.Session, title: str, doc_links: list[str]) -> int:
    """Télécharge les documents et les poste dans le channel Discord dédié."""
    if not DISCORD_DOCS_WEBHOOK_URL or not doc_links:
        return 0
    sent = 0
    for link in doc_links[:10]:
        try:
            resp = session.get(link, timeout=60)
            resp.raise_for_status()
            content = resp.content
            # On ignore les réponses HTML (pages, pas des fichiers)
            if b"<html" in content[:500].lower():
                continue
            if len(content) > MAX_DISCORD_FILE_SIZE:
                notify_discord(
                    f"📎 Document « {title} » trop volumineux pour Discord "
                    f"({len(content)//1024//1024} Mo) : {link}",
                    webhook=DISCORD_DOCS_WEBHOOK_URL,
                )
                continue
            fname = filename_from_response(link, resp)
            r = requests.post(
                DISCORD_DOCS_WEBHOOK_URL,
                data={"content": f"📄 **{title}**"},
                files={"file": (fname, content)},
                timeout=60,
            )
            r.raise_for_status()
            sent += 1
            log.info("Document envoyé sur Discord : %s", fname)
        except Exception as e:
            log.error("Échec envoi document %s : %s", link, e)
    return sent


def handle_interstitial(session: requests.Session, interstitial: dict) -> None:
    """Gère une page de blocage : docs -> Discord, notification ou validation auto."""
    title = interstitial["title"]
    log.warning("Accès au planning bloqué par : %s", title)

    already_notified = (
        PENDING_ACK_FILE.exists()
        and PENDING_ACK_FILE.read_text(encoding="utf-8") == title
    )

    if not already_notified:
        nb_docs = send_documents_to_discord(session, title, interstitial["doc_links"])
        docs_info = f"\n📎 {nb_docs} document(s) envoyé(s) dans le channel documents." if nb_docs else ""
        if AUTO_ACKNOWLEDGE:
            msg = (
                f"📄 Document validé automatiquement : « {title} ».{docs_info}\n"
                "Pense quand même à le lire !"
            )
        else:
            msg = (
                f"📄 Un document/notification attend ta validation sur le site : « {title} ».{docs_info}\n"
                "Le planning est inaccessible tant que tu ne l'as pas pris en compte.\n"
                f"➡️ Connecte-toi ici pour le valider : {BASE_URL}"
            )
        if DISCORD_PING and not AUTO_ACKNOWLEDGE:
            msg = f"{ping_prefix()}\n{msg}"
        notify_discord(msg)
        notify_email("📄 Document sur le site du personnel", msg)
        PENDING_ACK_FILE.write_text(title, encoding="utf-8")
    else:
        log.info("Déjà notifié pour ce document.")

    if AUTO_ACKNOWLEDGE:
        log.info("AUTO_ACKNOWLEDGE actif : validation automatique de « %s »", title)
        ack = session.post(interstitial["action"], data=interstitial["payload"], timeout=30)
        ack.raise_for_status()
    else:
        raise InterstitialBlocked(title)


# ---------------------------------------------------------------------------
# Notes / documents internes
# ---------------------------------------------------------------------------

# Extrait chaque note : open_file('<fichier>', '<idNote>', <lu>, <flag>, '<verif>')
NOTE_RE = re.compile(
    r"open_file\('([^']+)',\s*'([^']+)',\s*(\d+),\s*(\d+),\s*'([^']*)'\)"
)
# Titre associé : le <h1> qui suit dans la même ligne de tableau
NOTE_TITLE_RE = re.compile(r"<h1>(.*?)</h1>", re.I | re.S)


def fetch_notes(session: requests.Session) -> list[dict]:
    """Récupère la liste des notes non lues via afficherNotes.php."""
    r = session.post(
        NOTES_LIST_URL,
        data={"nonLu": ""},
        headers={"X-Requested-With": "XMLHttpRequest", "Referer": NOTES_REFERER},
        timeout=30,
    )
    r.raise_for_status()
    html = r.text
    if "pas de notes" in html.lower():
        return []

    notes = []
    titles = NOTE_TITLE_RE.findall(html)
    for idx, m in enumerate(NOTE_RE.finditer(html)):
        fichier, id_note, lu, flag, verif = m.groups()
        titre = normalize(re.sub("<[^>]+>", "", titles[idx])) if idx < len(titles) else fichier
        notes.append({
            "fichier": fichier, "id": id_note, "lu": int(lu),
            "flag": flag, "verif": verif, "titre": titre,
        })
    return notes


def download_note_pdf(session: requests.Session, fichier: str) -> bytes | None:
    try:
        r = session.get(NOTES_DOC_URL + fichier, timeout=60)
        r.raise_for_status()
        if r.content[:4] == b"%PDF":
            log.info("PDF %s téléchargé (%d Ko).", fichier, len(r.content) // 1024)
            return r.content
        log.warning("Le fichier %s ne semble pas être un PDF (status %s, %d octets).",
                    fichier, r.status_code, len(r.content))
    except Exception as e:
        log.error("Téléchargement PDF %s échoué : %s", fichier, e)
    return None


def mark_note_read(session: requests.Session, id_note: str) -> bool:
    """Envoie le 'Pris en compte' (noteLue.php)."""
    try:
        r = session.post(
            NOTES_MARKLU_URL,
            data={"idNote": id_note},
            headers={"X-Requested-With": "XMLHttpRequest", "Referer": NOTES_REFERER},
            timeout=30,
        )
        r.raise_for_status()
        log.info("Note %s marquée comme lue.", id_note)
        return True
    except Exception as e:
        log.error("Validation note %s échouée : %s", id_note, e)
        return False


def send_note(session: requests.Session, note: dict) -> None:
    """Envoie une note sur Discord (+ email) avec le PDF en pièce jointe."""
    pdf = download_note_pdf(session, note["fichier"])
    ping = ping_prefix() if DISCORD_PING else ""
    entete = f"{ping}\n" if ping else ""

    statut_valid = ""
    if NOTES_AUTO_MARKLU:
        ok = mark_note_read(session, note["id"])
        statut_valid = "\n✅ Validé automatiquement (« Pris en compte »)." if ok else \
                       "\n⚠️ Validation automatique échouée, à faire manuellement."
    else:
        statut_valid = f"\n➡️ À valider sur le site : {NOTES_REFERER}"

    msg = f"{entete}📄 **Nouveau document : {note['titre']}**{statut_valid}"

    # Discord : PDF en pièce jointe si dispo, sinon message seul
    if pdf and DISCORD_DOCS_WEBHOOK_URL and len(pdf) <= MAX_DISCORD_FILE_SIZE:
        try:
            payload = {"content": msg}
            if ping:
                payload["allowed_mentions"] = (
                    {"parse": ["everyone"]} if not ping.startswith("<@")
                    else {"users": [ping.strip("<@!>")]}
                )
            requests.post(
                DISCORD_DOCS_WEBHOOK_URL,
                data={"payload_json": json.dumps(payload)},
                files={"file": (note["fichier"], pdf, "application/pdf")},
                timeout=60,
            ).raise_for_status()
            log.info("Note '%s' envoyée sur Discord (PDF joint).", note["titre"])
        except Exception as e:
            log.error("Envoi Discord note échoué : %s", e)
            notify_discord(msg, webhook=DISCORD_DOCS_WEBHOOK_URL)
    else:
        notify_discord(msg + ("\n(PDF trop volumineux ou indisponible)" if not pdf else ""),
                       webhook=DISCORD_DOCS_WEBHOOK_URL)

    # Email avec PDF joint
    send_note_email(note, pdf, statut_valid)


def send_note_email(note: dict, pdf: bytes | None, statut: str) -> None:
    if not (SMTP_USER and SMTP_PASSWORD and EMAIL_TO):
        return
    from email.mime.multipart import MIMEMultipart
    from email.mime.application import MIMEApplication
    try:
        msg = MIMEMultipart()
        msg["Subject"] = f"📄 Nouveau document : {note['titre']}"
        msg["From"] = SMTP_USER
        msg["To"] = EMAIL_TO
        corps = f"Nouveau document sur le site du personnel : {note['titre']}{statut}"
        msg.attach(MIMEText(corps, "plain", "utf-8"))
        if pdf:
            part = MIMEApplication(pdf, _subtype="pdf")
            part.add_header("Content-Disposition", "attachment", filename=note["fichier"])
            msg.attach(part)
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=30) as server:
            server.starttls()
            server.login(SMTP_USER, SMTP_PASSWORD)
            server.send_message(msg)
        log.info("Note '%s' envoyée par email.", note["titre"])
    except Exception as e:
        log.error("Envoi email note échoué : %s", e)


def check_documents(session: requests.Session) -> None:
    """Détecte les nouvelles notes non lues, les envoie et les valide."""
    if not NOTES_ENABLED:
        return
    try:
        notes = fetch_notes(session)
    except Exception as e:
        log.error("Récupération des notes échouée : %s", e)
        return

    if not notes:
        log.info("Aucune note non lue.")
        return

    seen = set()
    if NOTES_SEEN_FILE.exists():
        try:
            seen = set(json.loads(NOTES_SEEN_FILE.read_text(encoding="utf-8")))
        except Exception:
            seen = set()

    nouvelles = [n for n in notes if n["id"] not in seen]
    log.info("%d note(s) non lue(s), %d nouvelle(s).", len(notes), len(nouvelles))

    for note in nouvelles:
        log.info("Traitement note : %s", note["titre"])
        send_note(session, note)
        seen.add(note["id"])

    NOTES_SEEN_FILE.write_text(
        json.dumps(sorted(seen), ensure_ascii=False), encoding="utf-8"
    )


# ---------------------------------------------------------------------------
# Extraction du planning
# ---------------------------------------------------------------------------


def fetch_planning_html(session: requests.Session) -> str:
    if not PLANNING_PAGE:
        raise RuntimeError(
            "PLANNING_PAGE n'est pas défini dans .env. "
            "Connecte-toi au site dans ton navigateur, va sur ton planning, "
            "et copie le chemin de l'URL (tout ce qui suit le nom de domaine)."
        )
    url = urljoin(BASE_URL + "/", PLANNING_PAGE.lstrip("/"))

    for _ in range(5):  # tolère plusieurs documents à valider d'affilée
        resp = session.get(url, timeout=30)
        resp.raise_for_status()
        html = resp.text

        interstitial = find_interstitial(html)
        if interstitial is None:
            PENDING_ACK_FILE.unlink(missing_ok=True)
            return html
        handle_interstitial(session, interstitial)

    raise RuntimeError("Trop de pages de validation successives, abandon.")


def parse_planning(html: str) -> dict:
    """Extrait le planning sous forme {date: horaire}."""
    soup = BeautifulSoup(html, "html.parser")
    planning = {}

    for table in soup.find_all("table"):
        rows = table.find_all("tr")
        i = 0
        while i < len(rows):
            cells = rows[i].find_all(["td", "th"])
            texts = [c.get_text(" ", strip=True) for c in cells]
            dates = [DATE_RE.search(t).group(1) if DATE_RE.search(t) else None for t in texts]

            if sum(1 for d in dates if d) >= 5 and i + 1 < len(rows):
                value_cells = rows[i + 1].find_all(["td", "th"])
                values = [c.get_text(" ", strip=True) for c in value_cells]
                for d, v in zip(dates, values):
                    if d:
                        planning[d] = normalize(v)
                i += 2
            else:
                i += 1

    if not planning:
        raise RuntimeError(
            "Aucune date trouvée dans la page planning. "
            "Vérifie que PLANNING_PAGE pointe bien vers la page du planning."
        )
    return planning


def normalize(value: str) -> str:
    value = re.sub(r"\s+", " ", value).strip()
    # Uniformise les heures : 4:00-23:00 -> 04:00-23:00
    value = re.sub(r"\b(\d):(\d{2})", r"0\1:\2", value)
    return value


JOURS_FR = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]
MOIS_FR = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet",
           "août", "septembre", "octobre", "novembre", "décembre"]


def date_lisible(date_str: str) -> str:
    """05/08/2026 -> 'mercredi 5 août'."""
    d = datetime.strptime(date_str, "%d/%m/%Y").date()
    return f"{JOURS_FR[d.weekday()]} {d.day} {MOIS_FR[d.month - 1]}"


def jours_restants(date_str: str) -> int:
    return (datetime.strptime(date_str, "%d/%m/%Y").date() - datetime.now().date()).days


def echeance(date_str: str) -> str:
    """Texte du type 'dans 3 jours' / 'demain' / 'il y a 5 jours'."""
    n = jours_restants(date_str)
    if n == 0:
        return "aujourd'hui"
    if n == 1:
        return "demain"
    if n == -1:
        return "hier"
    if n > 1:
        return f"dans {n} jours"
    return f"il y a {abs(n)} jours"


def is_unavailable(value: str) -> bool:
    return UNAVAILABLE_MARKER in value.lower()


# ---------------------------------------------------------------------------
# Comparaison
# ---------------------------------------------------------------------------


def sort_key(date_str: str):
    return datetime.strptime(date_str, "%d/%m/%Y")


def compare(old: dict, new: dict) -> list[tuple[str, str | None, str | None]]:
    """Retourne les changements sous forme (date, avant, après)."""
    changes = []
    for date in sorted(set(old) | set(new), key=sort_key):
        before = old.get(date)
        after = new.get(date)
        if before == after:
            continue
        if after is None:
            continue  # date sortie de l'affichage (passé), on ignore
        if before is None and (is_unavailable(after) or not NOTIFY_NEW_WEEKS):
            continue
        if before is not None and is_unavailable(before) and is_unavailable(after):
            continue
        changes.append((date, before, after))
    return changes


def shift_duration_minutes(value: str | None) -> int | None:
    """Durée d'une vacation en minutes, None si pas d'horaire."""
    if not value:
        return None
    m = TIME_RANGE_RE.search(value)
    if not m:
        return None
    h1, m1, h2, m2 = map(int, m.groups())
    start = h1 * 60 + m1
    end = h2 * 60 + m2
    if end <= start:
        end += 24 * 60  # passage minuit
    return end - start


def format_minutes_delta(delta: int) -> str:
    sign = "+" if delta > 0 else "-"
    h, m = divmod(abs(delta), 60)
    return f"{sign}{h}h{m:02d}"


def format_change(date: str, before: str | None, after: str) -> str:
    urgent = "🚨 " if is_urgent(after) else ""

    # Changement rétroactif : le jour est déjà passé
    is_retro = datetime.strptime(date, "%d/%m/%Y").date() < datetime.now().date()
    retro = "🕓 RÉTROACTIF · " if is_retro else ""

    if before is None:
        line = f"{urgent}🆕 {date} : {explain(after)}"
    elif is_unavailable(before):
        line = f"{urgent}📅 {date} : planning publié → {explain(after)}"
    elif is_unavailable(after):
        line = f"⚠️ {date} : {before} → planning retiré"
    else:
        line = f"{urgent}{retro}🔄 {date} : {before} → {explain(after)}"

        # Différence de temps de travail (utile pour vérifier la paie)
        d1, d2 = shift_duration_minutes(before), shift_duration_minutes(after)
        if d1 is not None and d2 is not None and d1 != d2:
            line += f" [{format_minutes_delta(d2 - d1)} de travail]"

    # Annotations retirées (ex : le H+ qui disparaît !)
    if before is not None:
        before_codes = {c for c, _ in find_annotations(before)}
        after_codes = {c for c, _ in find_annotations(after)}
        removed = before_codes - after_codes
        if removed:
            details = ", ".join(
                f"{c} ({ANNOTATIONS[c]['label']})" for c in sorted(removed)
            )
            line += f"\n   ❗ Annotation(s) retirée(s) : {details}"

    return line


# ---------------------------------------------------------------------------
# Mise en forme des notifications
# ---------------------------------------------------------------------------

COLOR_URGENT = 0xE74C3C   # rouge   : DEC_48h / annotation retirée
COLOR_RETRO = 0xE67E22    # orange  : changement sur un jour passé
COLOR_CHANGE = 0x3498DB   # bleu    : modification classique
COLOR_NEW = 0x2ECC71      # vert    : nouveau planning publié
COLOR_REMOVED = 0x95A5A6  # gris    : planning retiré


def describe_change(date: str, before: str | None, after: str) -> dict:
    """Analyse un changement et retourne tous les éléments d'affichage."""
    retro = jours_restants(date) < 0
    removed_codes = []
    if before is not None and not is_unavailable(after):
        removed_codes = sorted(
            {c for c, _ in find_annotations(before)} - {c for c, _ in find_annotations(after)}
        )

    was_off = before is not None and (before.strip().upper() == "OFF" or is_unavailable(before))
    now_off = after.strip().upper() == "OFF" or is_unavailable(after)

    if before is None or was_off:
        kind, titre, color = "nouveau", "Nouveau créneau", COLOR_NEW
        if was_off and before.strip().upper() == "OFF":
            titre = "Journée travaillée ajoutée"
    elif now_off:
        kind, titre, color = "retire", "Créneau retiré", COLOR_REMOVED
        if after.strip().upper() == "OFF":
            titre = "Journée libérée"
    elif retro:
        kind, titre, color = "retro", "Modifié après coup", COLOR_RETRO
    else:
        kind, titre, color = "modifie", "Horaire modifié", COLOR_CHANGE

    if removed_codes and strip_annotations(before) == strip_annotations(after):
        titre = "Annotation retirée"
    if is_urgent(after) or removed_codes:
        color = COLOR_URGENT

    return {
        "date": date, "before": before, "after": after, "kind": kind,
        "titre": titre, "color": color, "retro": retro,
        "removed_codes": removed_codes,
    }


def change_lines(info: dict) -> list[str]:
    """Corps du message pour un changement, en lignes lisibles."""
    before, after, kind = info["before"], info["after"], info["after"]
    lines = []

    if info["kind"] == "nouveau":
        lines.append(f"**{strip_annotations(info['after'])}**")
    elif info["kind"] == "retire":
        lines.append(f"~~{strip_annotations(before)}~~ → *plus de créneau prévu*")
    else:
        h_before, h_after = strip_annotations(before), strip_annotations(info["after"])
        if h_before == h_after:
            lines.append(f"**{h_after}** *(horaire inchangé)*")
        else:
            lines.append(f"~~{h_before}~~ → **{h_after}**")
        d1 = shift_duration_minutes(before)
        d2 = shift_duration_minutes(info["after"])
        if d1 is not None and d2 is not None and d1 != d2:
            delta = d2 - d1
            mot = "de plus" if delta > 0 else "de moins"
            lines.append(f"⏱️ {format_minutes_delta(delta).lstrip('+-')} {mot} qu'annoncé")

    for code, ann in find_annotations(info["after"]):
        lines.append(f"{ann.get('emoji', '•')} {ann['label']}")

    for code in info["removed_codes"]:
        ann = ANNOTATIONS.get(code, {"label": code})
        lines.append(f"❗ **Retiré : {ann['label']}**")

    return lines


def strip_annotations(value: str | None) -> str:
    """Retire les codes du texte pour n'afficher que l'horaire."""
    if not value:
        return ""
    out = value
    for code in ANNOTATIONS:
        out = re.sub(re.escape(code), "", out, flags=re.I)
    return normalize(out).strip("=> ·-").strip() or value


def build_embeds(changes: list[tuple]) -> list[dict]:
    """Construit les embeds Discord : une carte par jour modifié."""
    infos = [describe_change(*c) for c in changes]
    urgents = sum(1 for i in infos if i["color"] == COLOR_URGENT)
    retros = sum(1 for i in infos if i["retro"])

    embeds = []
    # Carte de synthèse
    resume = f"**{len(infos)}** changement(s) sur ton planning"
    extras = []
    if urgents:
        extras.append(f"🚨 {urgents} à vérifier")
    if retros:
        extras.append(f"🕓 {retros} sur des jours passés")
    if extras:
        resume += "\n" + " · ".join(extras)

    embeds.append({
        "title": "📋 Ton planning a changé",
        "description": resume,
        "color": COLOR_URGENT if urgents else COLOR_CHANGE,
        "footer": {"text": f"Vérifié le {datetime.now().strftime('%d/%m à %Hh%M')}"},
    })

    for info in infos[:9]:  # 9 + la synthèse = 10 max par message
        embeds.append({
            "title": f"{date_lisible(info['date'])}",
            "description": "\n".join(change_lines(info)),
            "color": info["color"],
            "footer": {"text": f"{info['titre']} · {echeance(info['date'])}"},
        })
    return embeds


def build_email_body(changes: list[tuple]) -> str:
    """Version texte lisible pour l'email."""
    infos = [describe_change(*c) for c in changes]
    parts = [f"{len(infos)} changement(s) detecte(s) sur ton planning.", ""]
    for info in infos:
        parts.append(f"--- {date_lisible(info['date'])} {info['date']} ({echeance(info['date'])})")
        parts.append(f"    {info['titre']}")
        for line in change_lines(info):
            clean = line.replace("**", "").replace("~~", "").replace("*", "")
            parts.append(f"    {clean}")
        parts.append("")
    parts.append(f"Verifie le {datetime.now().strftime('%d/%m/%Y a %Hh%M')}")
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Notifications
# ---------------------------------------------------------------------------


def notify_discord(message: str, webhook: str | None = None) -> None:
    url = webhook or DISCORD_WEBHOOK_URL
    if not url:
        return
    try:
        for chunk_start in range(0, len(message), 1900):
            chunk = message[chunk_start : chunk_start + 1900]
            r = requests.post(url, json={"content": chunk}, timeout=30)
            r.raise_for_status()
        log.info("Notification Discord envoyée.")
    except Exception as e:
        log.error("Échec notification Discord : %s", e)


def ping_prefix() -> str:
    """Construit la mention Discord à partir de DISCORD_PING."""
    p = DISCORD_PING
    if not p:
        return ""
    if p in ("@here", "@everyone"):
        return p
    if p.startswith("<@") or p.startswith("@"):
        return p if p.startswith("<@") else f"<@{p.lstrip('@')}>"
    if p.isdigit():  # ID utilisateur brut
        return f"<@{p}>"
    return p


def notify_discord_embed(embeds: list[dict], webhook: str | None = None,
                         ping: bool = False) -> None:
    """Envoie des embeds Discord (max 10 par message), avec ping optionnel."""
    url = webhook or DISCORD_WEBHOOK_URL
    if not url:
        return
    try:
        for i in range(0, len(embeds), 10):
            payload = {"embeds": embeds[i : i + 10]}
            if ping and i == 0:  # ping uniquement sur le premier message
                mention = ping_prefix()
                if mention:
                    payload["content"] = mention
                    # Autorise explicitement la mention (sinon Discord peut la muter)
                    if mention.startswith("<@"):
                        payload["allowed_mentions"] = {
                            "users": [mention.strip("<@!>")]
                        }
                    else:
                        payload["allowed_mentions"] = {"parse": ["everyone"]}
            r = requests.post(url, json=payload, timeout=30)
            r.raise_for_status()
        log.info("Notification Discord (embed) envoyée.")
    except Exception as e:
        log.error("Échec notification Discord : %s", e)


def notify_email(subject: str, body: str) -> None:
    if not (SMTP_USER and SMTP_PASSWORD and EMAIL_TO):
        return
    try:
        msg = MIMEText(body, "plain", "utf-8")
        msg["Subject"] = subject
        msg["From"] = SMTP_USER
        msg["To"] = EMAIL_TO
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=30) as server:
            server.starttls()
            server.login(SMTP_USER, SMTP_PASSWORD)
            server.send_message(msg)
        log.info("Notification email envoyée.")
    except Exception as e:
        log.error("Échec notification email : %s", e)


# ---------------------------------------------------------------------------
# Google Agenda
# ---------------------------------------------------------------------------


def gcal_service():
    from google.oauth2 import service_account
    from googleapiclient.discovery import build

    creds = service_account.Credentials.from_service_account_file(
        GCAL_SERVICE_ACCOUNT_FILE,
        scopes=["https://www.googleapis.com/auth/calendar"],
    )
    return build("calendar", "v3", credentials=creds, cache_discovery=False)


def gcal_event_body(date_str: str, value: str) -> dict | None:
    """Construit l'événement Google Agenda pour une journée, ou None (OFF, etc.)."""
    day = datetime.strptime(date_str, "%d/%m/%Y").date()
    props = {"private": {"pw_date": date_str.replace("/", "-")}}

    m = TIME_RANGE_RE.search(value)
    if m:
        h1, m1, h2, m2 = map(int, m.groups())
        start = datetime.combine(day, dtime(h1, m1))
        end = datetime.combine(day, dtime(h2, m2))
        if end <= start:
            end += timedelta(days=1)  # vacation qui passe minuit

        # Titre lisible : annotations connues traduites, reste tel quel
        found = find_annotations(value)
        leftover = normalize(TIME_RANGE_RE.sub("", value))
        for code, _ in found:
            leftover = normalize(re.sub(re.escape(code), "", leftover, flags=re.I))
        leftover = leftover.strip("=> ·-").strip()

        parts = [
            f"{info.get('emoji', '')} {info.get('short', info['label'])}".strip()
            for _, info in found
        ]
        if leftover:
            parts.append(leftover)
        summary = GCAL_EVENT_PREFIX + (f" · {' · '.join(parts)}" if parts else "")

        return {
            "summary": summary,
            "description": f"Vacation : {explain(value)}\n(sync automatique planning_watch)",
            "start": {"dateTime": start.isoformat(), "timeZone": GCAL_TIMEZONE},
            "end": {"dateTime": end.isoformat(), "timeZone": GCAL_TIMEZONE},
            "extendedProperties": props,
        }

    if value.upper().startswith("CP") and GCAL_SYNC_CP:
        return {
            "summary": "🏖️ Congé (CP)",
            "description": f"{value}\n(sync automatique planning_watch)",
            "start": {"date": day.isoformat()},
            "end": {"date": (day + timedelta(days=1)).isoformat()},
            "extendedProperties": props,
        }

    return None  # OFF, planning non disponible, etc. -> pas d'événement


def gcal_sync(dates: list[str], planning: dict) -> None:
    """Crée/met à jour/supprime les événements pour les dates concernées."""
    if not GCAL_ENABLED:
        return
    if not GCAL_CALENDAR_ID or not Path(GCAL_SERVICE_ACCOUNT_FILE).exists():
        log.error("Google Agenda activé mais GCAL_CALENDAR_ID ou le fichier "
                  "service_account.json est manquant.")
        return
    try:
        service = gcal_service()
    except Exception as e:
        log.error("Connexion Google Agenda impossible : %s", e)
        return

    for date_str in dates:
        value = planning.get(date_str)
        if value is None:
            continue
        try:
            existing = (
                service.events()
                .list(
                    calendarId=GCAL_CALENDAR_ID,
                    privateExtendedProperty=f"pw_date={date_str.replace('/', '-')}",
                    maxResults=5,
                    showDeleted=False,
                )
                .execute()
                .get("items", [])
            )
            body = gcal_event_body(date_str, value)

            if body is None:
                for ev in existing:
                    service.events().delete(
                        calendarId=GCAL_CALENDAR_ID, eventId=ev["id"]
                    ).execute()
                    log.info("GCal : événement supprimé pour le %s", date_str)
            elif existing:
                service.events().update(
                    calendarId=GCAL_CALENDAR_ID, eventId=existing[0]["id"], body=body
                ).execute()
                log.info("GCal : événement mis à jour pour le %s", date_str)
                for ev in existing[1:]:  # nettoie d'éventuels doublons
                    service.events().delete(
                        calendarId=GCAL_CALENDAR_ID, eventId=ev["id"]
                    ).execute()
            else:
                service.events().insert(
                    calendarId=GCAL_CALENDAR_ID, body=body
                ).execute()
                log.info("GCal : événement créé pour le %s", date_str)
        except Exception as e:
            log.error("GCal : erreur pour le %s : %s", date_str, e)


# ---------------------------------------------------------------------------
# Historique des changements rétroactifs (vérification fiche de paie)
# ---------------------------------------------------------------------------


def log_retro_changes(changes: list[tuple[str, str | None, str | None]]) -> None:
    """Consigne dans un CSV les changements sur des jours déjà passés."""
    import csv

    today = datetime.now().date()
    retro = [
        (d, b, a)
        for d, b, a in changes
        if b is not None and datetime.strptime(d, "%d/%m/%Y").date() < today
    ]
    if not retro:
        return

    new_file = not RETRO_CSV.exists()
    with RETRO_CSV.open("a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f, delimiter=";")
        if new_file:
            writer.writerow(
                ["detecte_le", "jour_concerne", "avant", "apres",
                 "delta_minutes", "annotations_retirees"]
            )
        for d, b, a in retro:
            d1, d2 = shift_duration_minutes(b), shift_duration_minutes(a)
            delta = (d2 - d1) if (d1 is not None and d2 is not None) else ""
            removed = ", ".join(
                sorted({c for c, _ in find_annotations(b)} - {c for c, _ in find_annotations(a or "")})
            )
            writer.writerow(
                [datetime.now().strftime("%d/%m/%Y %H:%M"), d, b, a, delta, removed]
            )
    log.info("%d changement(s) rétroactif(s) consigné(s) dans %s", len(retro), RETRO_CSV.name)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> int:
    if not SITE_LOGIN or not SITE_PASSWORD:
        log.error("SITE_LOGIN / SITE_PASSWORD manquants dans .env")
        return 1

    session = requests.Session()
    session.headers.update(HEADERS)

    try:
        login(session)
        check_documents(session)
        html = fetch_planning_html(session)
        new_planning = parse_planning(html)
    except InterstitialBlocked:
        return 0  # notification déjà envoyée, on réessaiera au prochain cron
    except Exception as e:
        log.error("Erreur : %s", e)
        return 1

    log.info("%d jours extraits du planning.", len(new_planning))

    first_run = not STATE_FILE.exists()
    if first_run:
        log.info("Première exécution : planning sauvegardé comme référence.")
        changed_dates = list(new_planning.keys())  # sync GCal complète
    else:
        old_planning = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        changes = compare(old_planning, new_planning)
        changed_dates = [c[0] for c in changes]
        if changes:
            log.info("%d changement(s) : %s", len(changes), [c[0] for c in changes])
            urgents = any(describe_change(*c)["color"] == COLOR_URGENT for c in changes)
            do_ping = bool(DISCORD_PING) and (urgents or not DISCORD_PING_URGENT_ONLY)
            notify_discord_embed(build_embeds(changes), ping=do_ping)
            sujet = "🚨 Planning modifié — à vérifier" if urgents else "📋 Ton planning a changé"
            notify_email(sujet, build_email_body(changes))
            log_retro_changes(changes)
        else:
            log.info("Aucun changement.")

    if changed_dates:
        gcal_sync(changed_dates, new_planning)

    STATE_FILE.write_text(
        json.dumps(new_planning, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
