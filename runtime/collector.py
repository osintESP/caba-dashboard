#!/usr/bin/env python3
from __future__ import annotations
import json, os, re, sys, time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import requests

BASE=os.getenv('CABA_BO_BASE','http://api-restboletinoficial.buenosaires.gob.ar').rstrip('/')
OUT=Path(__file__).resolve().parent/'output'
TIMEOUT=(10,90)
UA='CABA-Dashboard-NonProd/1.0'
LIC_RE=re.compile(r'licitaci[oó]n|llama a licitaci[oó]n|aprueba (?:los )?pliegos|aprueba el llamado|adjudica|preadjudica|contrataci[oó]n|servicio integral|provisi[oó]n|concesi[oó]n|obra p[uú]blica|adquisici[oó]n|\bLPU\b|\bLP\b|\bOC\b',re.I)

def now_iso(): return datetime.now(timezone.utc).astimezone().isoformat(timespec='seconds')
def client():
 s=requests.Session(); s.headers.update({'User-Agent':UA,'Accept':'application/json, text/plain, */*'}); return s

def request(s,method,path,**kwargs):
 url=f'{BASE}{path}'; errors=[]
 for attempt in range(1,4):
  try:
   r=s.request(method,url,timeout=TIMEOUT,allow_redirects=True,**kwargs)
   try:data=r.json()
   except Exception:data=None
   if r.ok:return {'ok':True,'status':r.status_code,'data':data,'url':url}
   errors.append({'attempt':attempt,'status':r.status_code})
  except requests.RequestException as exc: errors.append({'attempt':attempt,'error':repr(exc)})
  time.sleep(attempt*2)
 return {'ok':False,'url':url,'errors':errors}

def is_norm_record(n):
 if 'id_norma' in n:return bool(n.get('sumario') or n.get('nombre') or n.get('url_norma'))
 if 'id' not in n:return False
 return bool(n.get('sumario') or n.get('numero_norma') is not None or n.get('archivo_norma') or n.get('link_documento_norma') or n.get('link_documento_normas'))

def walk(node,trail=()):
 if isinstance(node,dict):
  if is_norm_record(node): yield trail,node
  for k,v in node.items(): yield from walk(v,trail+(str(k),))
 elif isinstance(node,list):
  for v in node: yield from walk(v,trail)

def belongs(n,b):
 bs=n.get('boletines')
 if not bs:return True
 for item in bs:
  if isinstance(item,(list,tuple)) and item and str(item[0])==str(b):return True
  if isinstance(item,dict) and str(item.get('numero',item.get('numero_boletin','')))==str(b):return True
  if isinstance(item,(str,int)) and str(item)==str(b):return True
 return False

def resolve_url(n,date):
 if n.get('url_norma'):return n['url_norma']
 links=n.get('link_documento_normas')
 if isinstance(links,list) and date:
  for item in links:
   if isinstance(item,(list,tuple)) and len(item)>=2 and str(item[0])==date and item[1]:return item[1]
 raw=n.get('link_documento_norma')
 return raw if isinstance(raw,str) and raw.startswith('http') else None

def norm(trail,n,b,date):
 ident=n.get('id_norma',n.get('id')); txt=' '.join(str(n.get(k,'')) for k in ('nombre','sumario','nombre_tipo','nombre_subtipo','archivo_norma'))
 return {'id_norma':ident,'nombre':n.get('nombre') or n.get('archivo_norma'),'sumario':n.get('sumario'),'url_norma':resolve_url(n,date),'anexos':n.get('anexos') or n.get('link_anexo') or [],'tipo':n.get('nombre_tipo') or (trail[-2] if len(trail)>=2 else None),'subtipo':n.get('nombre_subtipo'),'seccion':n.get('nombre_seccion'),'organismo':n.get('nombre_reparticion') or (trail[-1] if trail else None),'ruta_arbol':list(trail),'numero_boletin':b,'candidato_licitacion_transversal':bool(LIC_RE.search(txt))}

def main():
 OUT.mkdir(parents=True,exist_ok=True); s=client(); live=request(s,'GET','/obtenerBoletin/0/false')
 if not live.get('ok') or not isinstance(live.get('data'),dict) or not live['data'].get('numero'): raise SystemExit('live bulletin identification failed')
 header=live['data']; b=int(header['numero']); date=header.get('fecha_publicacion'); loaded=request(s,'GET',f'/obtenerBoletin/{b}/true'); sections=request(s,'GET',f'/obtenerSeccionesBoletin/{b}'); sources=[]
 if loaded.get('ok'):sources.append(('obtenerBoletin_true',loaded.get('data')))
 secdata=sections.get('data') if sections.get('ok') else []
 if isinstance(secdata,dict):secdata=secdata.get('secciones',secdata.get('data',[]))
 if isinstance(secdata,list):
  for sec in secdata:
   sid=sec.get('superseccion_id') if isinstance(sec,dict) else None
   if sid is None:continue
   res=request(s,'POST','/obtenerNormasSeccion',json={'nro_boletin':b,'superseccion_id':sid})
   if not res.get('ok'):res=request(s,'POST','/obtenerNormasSeccion',data={'nro_boletin':b,'superseccion_id':sid})
   if res.get('ok'):sources.append((f'obtenerNormasSeccion:{sid}',res.get('data')))
 search=request(s,'GET',f'/obtenerResultado/nBoletin={b}&perPage=1000&offset=0')
 if search.get('ok'):sources.append(('obtenerResultado',search.get('data')))
 unique={}; routes={}
 for source,data in sources:
  for trail,raw in walk(data):
   if not belongs(raw,b):continue
   x=norm(trail,raw,b,date); ident=x.get('id_norma')
   if ident is None:continue
   key=str(ident)
   if key not in unique:unique[key]=x
   elif not unique[key].get('url_norma') and x.get('url_norma'):unique[key]['url_norma']=x['url_norma']
   routes.setdefault(key,[]).append(source)
 for k,x in unique.items():x['rutas_recuperacion']=sorted(set(routes[k]))
 norms=list(unique.values()); types=Counter((x.get('tipo') or 'SIN_TIPO') for x in norms); orgs=Counter((x.get('organismo') or 'SIN_ORGANISMO') for x in norms)
 report={'schema_version':2,'collected_at':now_iso(),'boletin':header,'numero_objetivo':b,'TOTAL_API':len(norms),'distribucion_tipo':dict(types),'distribucion_organismo':dict(orgs),'normas':norms}
 (OUT/'latest.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8'); print(json.dumps({'numero':b,'TOTAL_API':len(norms)},ensure_ascii=False))
if __name__=='__main__':main()
