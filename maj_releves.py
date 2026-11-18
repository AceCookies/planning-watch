#!/usr/bin/env python3
"""
maj_releves.py — Télécharge les relevés d'activité depuis le site et
régénère les compteurs de paie (paie_mensuelle.json) + l'émargement.

À lancer périodiquement (ex: 1x par semaine via cron) pour garder
les données paie à jour. S'appuie sur planning_watch.py pour la connexion.
"""
import re, glob, json, os
from datetime import datetime
from collections import defaultdict
from pathlib import Path

import requests
import pdfplumber

import planning_watch as pw

SCRIPT_DIR = Path(__file__).resolve().parent
RELEVES_DIR = SCRIPT_DIR / "releves"
PAIE_FILE = SCRIPT_DIR / "paie_mensuelle.json"
EMARG_FILE = SCRIPT_DIR / "emargement.json"

DATE_LINE = re.compile(r'^(\d{2}/\d{2}/\d{4})\s+(.*)$')
HHMM = re.compile(r'(\d{2}):(\d{2})')


def telecharger_releves(session) -> int:
    """Télécharge tous les relevés PDF accessibles (id impairs)."""
    RELEVES_DIR.mkdir(exist_ok=True)
    nb = 0
    # Balayer une plage d'id ; les id valides renvoient un PDF > 5 Ko
    for i in range(150, 260):
        try:
            r = session.get(pw.BASE_URL + f"/personnel/v2/visuRelevev3.php?id={i}", timeout=20)
            c = r.content
            idx = c.find(b'%PDF')
            if idx >= 0 and (len(c) - idx) > 5000:
                (RELEVES_DIR / f"releve_{i}.pdf").write_bytes(c[idx:])
                nb += 1
        except Exception:
            pass
    return nb


def parser_tout():
    """Recalcule paie_mensuelle.json et emargement.json depuis les PDF."""
    mois = defaultdict(lambda: {'h_trav':0.0,'nuit':0.0,'dim':0.0,'paniers':0,
                                'hplus':0,'ml':0,'jours_trav':0,'jours_off':0,'sms_modif':0})
    emarg = {}

    for f in sorted(glob.glob(str(RELEVES_DIR / '*.pdf'))):
        with pdfplumber.open(f) as doc:
            lines = []
            for p in doc.pages:
                lines += (p.extract_text() or "").splitlines()
        m0 = re.search(r'(\d{2})/(\d{2})/(\d{4})\s+AU', "\n".join(lines[:3]))
        if not m0:
            continue
        mkey = f"{m0.group(2)}/{m0.group(3)}"
        i = 0
        while i < len(lines):
            md = DATE_LINE.match(lines[i].strip())
            if not md:
                i += 1; continue
            date = md.group(1)
            if date[3:] != mkey:
                i += 1; continue
            reste = md.group(2)
            if reste.upper().startswith('OFF'):
                mois[mkey]['jours_off'] += 1
                emarg[date] = {'type':'OFF','reel':None,'codes':[]}
                i += 1; continue
            heures = HHMM.findall(lines[i])
            if len(heures) >= 4:
                rd = int(heures[2][0])*60+int(heures[2][1])
                rf = int(heures[3][0])*60+int(heures[3][1])
                if rf <= rd: rf += 24*60
                dur = (rf-rd)/60
                bloc = lines[i] + " " + (lines[i+1] if i+1 < len(lines) else "")
                codes = [c for c in ['H+','ML','SMS_AUTO_MODIF_PLAN','RET_VOL','FOR']
                         if re.search(re.escape(c), bloc)]
                mois[mkey]['jours_trav'] += 1
                mois[mkey]['h_trav'] += dur
                mois[mkey]['paniers'] += 1
                if 'H+' in codes: mois[mkey]['hplus'] += 1
                if 'ML' in codes: mois[mkey]['ml'] += 1
                if 'SMS_AUTO_MODIF_PLAN' in codes: mois[mkey]['sms_modif'] += 1
                nuit = sum(1 for h in range(rd,rf) if (h//60)%24>=21 or (h//60)%24<6)/60
                mois[mkey]['nuit'] += round(nuit,2)
                if datetime.strptime(date,'%d/%m/%Y').weekday()==6:
                    mois[mkey]['dim'] += round(dur,2)
                r_deb = f"{heures[2][0]}:{heures[2][1]}"
                r_fin = f"{heures[3][0]}:{heures[3][1]}"
                emarg[date] = {'type':'travail','reel':[r_deb,r_fin],'codes':codes}
                i += 2
            else:
                i += 1

    out = {}
    for k in sorted(mois, key=lambda x:(x.split('/')[1], x.split('/')[0])):
        out[k] = {kk:(round(v,1) if isinstance(v,float) else v) for kk,v in mois[k].items()}
    PAIE_FILE.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    EMARG_FILE.write_text(json.dumps(emarg, ensure_ascii=False, indent=1), encoding="utf-8")
    return len(out), len(emarg)


def main():
    s = requests.Session(); s.headers.update(pw.HEADERS)
    pw.login(s)
    nb = telecharger_releves(s)
    print(f"{nb} relevés téléchargés.")
    nm, nj = parser_tout()
    print(f"paie_mensuelle.json : {nm} mois | emargement.json : {nj} jours")


if __name__ == "__main__":
    main()
