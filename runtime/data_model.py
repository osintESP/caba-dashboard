#!/usr/bin/env python3
from __future__ import annotations
import json,re
from datetime import datetime,timezone
from pathlib import Path
ROOT=Path(__file__).resolve().parent; LATEST=ROOT/'output'/'latest.json'; DATA=ROOT.parent/'data'
POS=re.compile(r'licitaci[oó]n|contrataci[oó]n directa|contrataci[oó]n menor|compra directa|concurso de precios|aprueba (?:los )?pliegos|aprueba el llamado|llama a (?:licitaci[oó]n|contrataci[oó]n)|adjudica|preadjudica|servicio integral|provisi[oó]n|concesi[oó]n|obra p[uú]blica|adquisici[oó]n|\bLPU\b|\bLPR\b|\bCDI\b|\bCME\b|\bOC\b',re.I)
NEG=re.compile(r'contrataci[oó]n de personal|contrata (?:como|a )|jefes?/as? y/o instructores?/as? de residentes|locaci[oó]n de servicios art[ií]sticos|designa|renuncia|licencia (?:con|sin) goce|planta permanente|suplentes? de guardia|comisi[oó]n de servicios',re.I)
def read(p,d): return json.loads(p.read_text(encoding='utf-8')) if p.exists() else d
def write(p,v): p.parent.mkdir(parents=True,exist_ok=True); p.write_text(json.dumps(v,ensure_ascii=False,indent=2),encoding='utf-8')
def now(): return datetime.now(timezone.utc).isoformat(timespec='seconds')
def txt(n): return ' '.join(str(n.get(k,'')) for k in ('nombre','sumario','tipo','subtipo','seccion','organismo'))
def isproc(n):
 t=txt(n); lt=str(n.get('tipo','')).lower(); ls=str(n.get('seccion','')).lower(); formal=any(x in lt or x in ls for x in ('licitación','licitacion','contratación directa','contratacion directa','contratación menor','contratacion menor','obra pública','obra publica','concesión','concesion','compra directa','concurso de precios'))
 return formal or (not NEG.search(t) and bool(POS.search(t)))
def category(n):
 t=txt(n).lower()
 if any(x in t for x in ('software','saas','nutanix','veritas','sistema','plataforma','telecom','ciberseg','digital','datos','informática','informatica','tecnolog','servidor','storage','backup','redes','cctv','control de acceso','identidad','licencia de software')): return 'tecnologia'
 if any(x in t for x in ('obra','constru','reparaci','mantenimiento edilicio','pavimento','edificio','infraestructura','instalación eléctrica','instalacion electrica')): return 'obra_infraestructura'
 if any(x in t for x in ('medic','hospital','salud','insumo','nitrógeno','nitrogeno','reactivo','prótesis','protesis','equipamiento médico','equipamiento medico')): return 'salud'
 if any(x in t for x in ('alimento','comida','catering','víveres','viveres')): return 'alimentos'
 return 'otros'
def main():
 latest=read(LATEST,None)
 if not isinstance(latest,dict): raise SystemExit('latest missing')
 b=latest.get('boletin') or {}; num=b.get('numero') or latest.get('numero_objetivo'); collected=latest.get('collected_at') or now()
 editions=read(DATA/'editions.json',[]); by={str(x.get('numero_boletin')):x for x in editions if x.get('numero_boletin') is not None}; old=by.get(str(num),{}); by[str(num)]={**old,'numero_boletin':num,'fecha_publicacion':b.get('fecha_publicacion'),'url_boletin':b.get('url_boletin'),'separata':b.get('separata') or [],'first_collected_at':old.get('first_collected_at') or collected,'last_collected_at':collected,'total_normas':latest.get('TOTAL_API'),'schema_source':latest.get('schema_version')}; editions=sorted(by.values(),key=lambda x:int(x.get('numero_boletin') or 0)); write(DATA/'editions.json',editions)
 existing=read(DATA/'norms.json',[]); idx={str(x.get('id_norma')):x for x in existing if x.get('id_norma') is not None}
 for n in latest.get('normas') or []:
  i=n.get('id_norma');
  if i is None: continue
  old=idx.get(str(i),{}); idx[str(i)]={**old,'id_norma':i,'numero_boletin':num,'fecha_publicacion':b.get('fecha_publicacion'),'nombre':n.get('nombre'),'sumario':n.get('sumario'),'url_norma':n.get('url_norma'),'anexos':n.get('anexos') or [],'tipo':n.get('tipo'),'subtipo':n.get('subtipo'),'seccion':n.get('seccion'),'organismo':n.get('organismo'),'ruta_arbol':n.get('ruta_arbol') or [],'rutas_recuperacion':n.get('rutas_recuperacion') or [],'first_seen_at':old.get('first_seen_at') or collected,'last_seen_at':collected}
 norms=sorted(idx.values(),key=lambda x:str(x.get('id_norma'))); write(DATA/'norms.json',norms)
 procs=[]
 for n in norms:
  if isproc(n): procs.append({'id_norma':n.get('id_norma'),'numero_boletin':n.get('numero_boletin'),'fecha_publicacion':n.get('fecha_publicacion'),'nombre':n.get('nombre'),'sumario':n.get('sumario'),'url_norma':n.get('url_norma'),'organismo':n.get('organismo'),'tipo':n.get('tipo'),'categoria':category(n),'first_seen_at':n.get('first_seen_at'),'last_seen_at':n.get('last_seen_at'),'estado':'detectada'})
 write(DATA/'procurements.json',procs)
 cats={}
 for p in procs: cats[p['categoria']]=cats.get(p['categoria'],0)+1
 stats={'generated_at':now(),'latest_bulletin':num,'editions':len(editions),'norms':len(norms),'procurements':len(procs),'procurement_categories':dict(sorted(cats.items()))}; write(DATA/'stats.json',stats); print(json.dumps(stats,ensure_ascii=False))
if __name__=='__main__':main()
