#!/usr/bin/env python3
"""verif_paie.json : vérif par MOIS CALENDAIRE (période réelle de décompte des variables).
Le bulletin du mois M paie les variables (nuit/dim/panier) du mois calendaire M-1.
Barèmes CCN annexe VIII (IDCC 1351) : nuit +25% (21h-6h), dimanche +50%, panier 6,87€ (1/jour travaillé).
Un mois dont l'émargement est incomplet est signalé 'partiel' et non compté comme écart."""
import json, re, glob, calendar
from datetime import datetime, timedelta
import pdfplumber

def d(s): return datetime.strptime(s,"%d/%m/%Y").date()
MOIS=["","janvier","février","mars","avril","mai","juin","juillet","août","septembre","octobre","novembre","décembre"]

emarg=json.load(open('emargement.json'))
emarg_dates=sorted(d(x) for x in emarg)
EMARG_MIN, EMARG_MAX = emarg_dates[0], emarg_dates[-1]

def periode_du_bulletin(bk):
    y,m=int(bk[:4]),int(bk[5:])
    pm = m-1 or 12; py = y if m>1 else y-1
    last=calendar.monthrange(py,pm)[1]
    return f"01/{pm:02d}/{py}", f"{last:02d}/{pm:02d}/{py}"

def compte(deb,fin):
    j=d(deb); nuit=dim=0.0; pan=0
    while j<=d(fin):
        info=emarg.get(j.strftime("%d/%m/%Y"))
        if info and info['type']=='travail' and info['reel']:
            rd=int(info['reel'][0][:2])*60+int(info['reel'][0][3:])
            rf=int(info['reel'][1][:2])*60+int(info['reel'][1][3:])
            if rf<=rd: rf+=1440
            pan+=1
            nuit+=sum(1 for mn in range(rd,rf) if (mn//60)%24>=21 or (mn//60)%24<6)/60
            if j.weekday()==6: dim+=(rf-rd)/60
        j+=timedelta(days=1)
    return round(nuit,1),round(dim,1),pan

bull={}
for f in sorted(glob.glob('bulletins/*.pdf')):
    with pdfplumber.open(f) as doc:
        t="\n".join((p.extract_text() or "") for p in doc.pages).replace(' ','')
    k=re.search(r'(\d{4})-(\d{2})',f).group(0)
    def q(l):
        m=re.search(l+r'[^\d]*(\d+[.,]?\d*)',t); return float(m.group(1).replace(',','.')) if m else None
    net=re.search(r'MONTANTNETAPAYER\(enEuros\)(\d+[.,]\d+)',t)
    bull[k]={'nuit':q('MAJORATIONNUIT25%'),'dim':q('MAJORATIONDIMANCHE50%'),
             'panier':q('PANIER'),'net':net.group(1) if net else None}

TOL={'nuit':1.5,'dim':1.5,'paniers':1}
verif={}
for bk in sorted(bull):
    if bull[bk]['nuit'] is None: continue
    deb,fin=periode_du_bulletin(bk)
    # mois couvert par l'émargement ?
    partiel = d(deb) < EMARG_MIN or d(fin) > EMARG_MAX
    cn,cd,cp=compte(deb,fin); b=bull[bk]
    pay_pan=int(b['panier']) if b['panier'] else 0
    def stat(ec,tol): return 'ok' if abs(ec)<=tol else ('plus' if ec>0 else 'moins')
    # ec>0 => payé>calc ; on regarde payé-calc
    en=round(b['nuit']-cn,1); ed=round(b['dim']-cd,1); ep=pay_pan-cp
    pm=int(bk[5:])-1 or 12; py=int(bk[:4]) if int(bk[5:])>1 else int(bk[:4])-1
    verif[bk]={
      'periode':f"{deb} → {fin}",'mois':f"{MOIS[pm]} {py}",
      'mois_paye':f"{MOIS[int(bk[5:])]} {bk[:4]}",
      'net':b['net'],'partiel':partiel,
      'nuit':{'paye':round(b['nuit'],1),'calc':cn,'ecart':en,'etat':'partiel' if partiel else stat(en,TOL['nuit'])},
      'dim':{'paye':round(b['dim'],1),'calc':cd,'ecart':ed,'etat':'partiel' if partiel else stat(ed,TOL['dim'])},
      'paniers':{'paye':pay_pan,'calc':cp,'ecart':ep,'etat':'partiel' if partiel else stat(ep,TOL['paniers'])},
    }
json.dump(verif,open('verif_paie.json','w'),ensure_ascii=False,indent=2)

ok=plus=moins=part=0
for bk,v in verif.items():
    if v['partiel']: part+=1; verdict="◐ données partielles"
    else:
        etats=[v[c]['etat'] for c in ('nuit','dim','paniers')]
        if 'moins' in etats: moins+=1; verdict="⚠️ "+ " ".join(f"{c}:{v[c]['ecart']:+g}" for c in ('nuit','dim','paniers') if v[c]['etat']=='moins')
        elif 'plus' in etats: plus+=1; verdict="▲ "+" ".join(f"{c}:+{v[c]['ecart']:g}" for c in ('nuit','dim','paniers') if v[c]['etat']=='plus')
        else: ok+=1; verdict="✓ conforme"
    print(f"{v['mois']:18} (payé {v['mois_paye']:14}) {verdict}")
print(f"\n→ {ok} conformes · {plus} écart +payé · {moins} à vérifier · {part} partiels (émargement incomplet)")
