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
    return re.sub(r"\s+", " ", value).strip()


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
            lines = [format_change(*c) for c in changes]
            body = f"🔔 Changement(s) détecté(s) sur ton planning ({len(lines)}) :\n\n" + "\n".join(lines)
            log.info(body)
            notify_discord(body)
            notify_email("⚠️ Changement de planning détecté", body)
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
