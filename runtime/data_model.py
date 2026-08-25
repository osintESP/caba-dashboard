#!/usr/bin/env python3
from __future__ import annotations
import json,re
from datetime import datetime,timezone
from pathlib import Path
ROOT=Path(__file__).resolve().parent; LATEST=ROOT/'output'/'latest.json'; DATA=ROOT.parent/'data'
POS=re.compile(r'licitaci[oó]n|contrataci[oó]n directa|contrataci[oó]n menor|compra directa|concurso de precios|aprueba (?:los )?pliegos|aprueba el llamado|llama a (?:licitaci[oó]n|contrataci[oó]n)|adjudica|preadjudica|servicio integral|provisi[oó]n|concesi[oó]n|obra p[uú]blica|adquisici[oó]n|\bLPU\b|\bLPR\b|\bCDI\b|\bCME\b|\bOC\b',re.I)
NEG=re.compile(r'contrataci[oó]n de personal|contrata (?:como|a )|jefes?/as? y/o instructores?/as? de residentes|locaci[oó]n de servicios art[ií]sticos|designa|renuncia|licencia (?:con|sin) goce|planta permanente|suplentes? de guardia|comisi[oó]n de servicios',re.I)
# Identifica el proceso administrativo (ej. "14/IVC/26") citado en el nombre de la norma,
# para poder agrupar circulares/prórrogas/llamados que refieren a la misma licitación.
PROCESS_RE=re.compile(r'n[°º]\s*(\d+/[a-z]+/\d+)',re.I)
# Frases específicas de redes de TI: evita que "redes" en sentido genérico (redes eléctricas,
# redes de agua, etc.) se clasifique como tecnología.
TECH_NETWORK=('redes de datos','redes informáticas','redes informaticas','red de datos','redes inalámbricas','redes inalambricas','red wifi','fibra óptica','fibra optica')
def read(p,d): return json.loads(p.read_text(encoding='utf-8')) if p.exists() else d
def write(p,v): p.parent.mkdir(parents=True,exist_ok=True); p.write_text(json.dumps(v,ensure_ascii=False,indent=2),encoding='utf-8')
def now(): return datetime.now(timezone.utc).isoformat(timespec='seconds')
def txt(n): return ' '.join(str(n.get(k,'')) for k in ('nombre','sumario','tipo','subtipo','seccion','organismo'))
def isproc(n):
 t=txt(n); lt=str(n.get('tipo','')).lower(); ls=str(n.get('seccion','')).lower(); formal=any(x in lt or x in ls for x in ('licitación','licitacion','contratación directa','contratacion directa','contratación menor','contratacion menor','obra pública','obra publica','concesión','concesion','compra directa','concurso de precios'))
 return formal or (not NEG.search(t) and bool(POS.search(t)))
# Sólo exige borde de palabra a la izquierda (no a la derecha, para no romper coincidencias
# de raíz como 'tecnolog' -> 'tecnología'/'tecnológico'): evita falsos positivos de substring
# como 'datos' dentro de 'mandatos', mismo tipo de bug ya corregido para 'redes' en otros lados.
def _has(t,words): return any(re.search(r'\b'+re.escape(w),t) for w in words)
def category(n):
 t=txt(n).lower()
 if _has(t,('software','saas','nutanix','veritas','sistema','plataforma','telecom','ciberseg','digital','datos','informática','informatica','tecnolog','servidor','storage','backup','cctv','control de acceso','identidad','licencia de software')): return 'tecnologia'
 if _has(t,TECH_NETWORK): return 'tecnologia'
 if _has(t,('obra','constru','reparaci','mantenimiento edilicio','pavimento','edificio','infraestructura','instalación eléctrica','instalacion electrica')): return 'obra_infraestructura'
 if _has(t,('medic','hospital','salud','insumo','nitrógeno','nitrogeno','reactivo','prótesis','protesis','equipamiento médico','equipamiento medico')): return 'salud'
 if _has(t,('alimento','comida','catering','víveres','viveres')): return 'alimentos'
 return 'otros'
def _id_sort_key(v):
 try:return(0,int(v))
 except(TypeError,ValueError):return(1,str(v))
def proceso_id(n):
 m=PROCESS_RE.search(n.get('nombre') or '')
 return m.group(1).upper() if m else str(n.get('id_norma'))
def pick(old,new,key):
 v=new.get(key)
 return v if v not in (None,'',[],{}) else old.get(key)
def clean_organismo(name):
 # La API del Boletín a veces devuelve el mismo organismo con basura de formato
 # (guiones/espacios colgantes, ej. "Ministerio de Salud-"), que sin normalizar
 # aparece como una entidad distinta en los rankings por organismo.
 if not name: return None
 name=re.sub(r'\s+',' ',name).strip()
 name=re.sub(r'[\s\-]+$','',name).strip()
 return name or None
def merge_norm(old,n,num,b,collected):
 # clean_organismo se aplica ANTES de pick(): si no, un valor basura no vacío en el nuevo
 # fetch (ej. "-") pasa el chequeo de pick() por no ser falsy, y clean_organismo lo reduce
 # a None después, borrando silenciosamente un organismo válido que ya teníamos guardado.
 organismo=clean_organismo(n.get('organismo')) or old.get('organismo')
 return {**old,
  'id_norma':n.get('id_norma'),'numero_boletin':num,'fecha_publicacion':b.get('fecha_publicacion'),
  'nombre':pick(old,n,'nombre'),'sumario':pick(old,n,'sumario'),'url_norma':pick(old,n,'url_norma'),
  'anexos':pick(old,n,'anexos') or [],'tipo':pick(old,n,'tipo'),'subtipo':pick(old,n,'subtipo'),
  'seccion':pick(old,n,'seccion'),'organismo':organismo,'ruta_arbol':pick(old,n,'ruta_arbol') or [],
  'rutas_recuperacion':sorted(set(old.get('rutas_recuperacion') or [])|set(n.get('rutas_recuperacion') or [])),
  'first_seen_at':old.get('first_seen_at') or collected,'last_seen_at':collected}
def main():
 latest=read(LATEST,None)
 if not isinstance(latest,dict): raise SystemExit('latest missing')
 b=latest.get('boletin') or {}; num=b.get('numero') or latest.get('numero_objetivo'); collected=latest.get('collected_at') or now()
 editions=read(DATA/'editions.json',[]); by={str(x.get('numero_boletin')):x for x in editions if x.get('numero_boletin') is not None}; old=by.get(str(num),{}); by[str(num)]={**old,'numero_boletin':num,'fecha_publicacion':b.get('fecha_publicacion'),'url_boletin':b.get('url_boletin'),'separata':b.get('separata') or [],'first_collected_at':old.get('first_collected_at') or collected,'last_collected_at':collected,'total_normas':latest.get('TOTAL_API'),'schema_source':latest.get('schema_version')}; editions=sorted(by.values(),key=lambda x:int(x.get('numero_boletin') or 0)); write(DATA/'editions.json',editions)
 existing=read(DATA/'norms.json',[])
 # Auto-cura organismos ya guardados con basura de formato (ver clean_organismo): las
 # normas viejas no se vuelven a scrapear, así que sin esto quedarían sucias para siempre.
 for x in existing:
  if x.get('organismo'): x['organismo']=clean_organismo(x['organismo'])
 idx={str(x.get('id_norma')):x for x in existing if x.get('id_norma') is not None}
 for n in latest.get('normas') or []:
  i=n.get('id_norma')
  if i is None: continue
  idx[str(i)]=merge_norm(idx.get(str(i),{}),n,num,b,collected)
 # Orden numérico, no lexicográfico como string (con id_norma acercándose a 7 dígitos,
 # un sort de string ya empieza a intercalar mal, ej. '999999' antes que '1000000').
 norms=sorted(idx.values(),key=lambda x:_id_sort_key(x.get('id_norma'))); write(DATA/'norms.json',norms)
 procs=[]
 for n in norms:
  if isproc(n): procs.append({'id_norma':n.get('id_norma'),'numero_boletin':n.get('numero_boletin'),'fecha_publicacion':n.get('fecha_publicacion'),'nombre':n.get('nombre'),'sumario':n.get('sumario'),'url_norma':n.get('url_norma'),'organismo':n.get('organismo'),'tipo':n.get('tipo'),'categoria':category(n),'proceso_id':proceso_id(n),'first_seen_at':n.get('first_seen_at'),'last_seen_at':n.get('last_seen_at'),'estado':'detectada'})
 write(DATA/'procurements.json',procs)
 # Varios actos (llamado, circulares, prórroga) pueden referirse al mismo proceso de
 # licitación: las métricas de "contrataciones" cuentan procesos únicos, no actos.
 proceso_categoria={}
 for p in procs: proceso_categoria.setdefault(p['proceso_id'],p['categoria'])
 cats={}
 for cat in proceso_categoria.values(): cats[cat]=cats.get(cat,0)+1
 stats={'generated_at':now(),'latest_bulletin':num,'editions':len(editions),'norms':len(norms),'procurement_acts':len(procs),'procurements':len(proceso_categoria),'procurement_categories':dict(sorted(cats.items()))}; write(DATA/'stats.json',stats); print(json.dumps(stats,ensure_ascii=False))
if __name__=='__main__':main()
