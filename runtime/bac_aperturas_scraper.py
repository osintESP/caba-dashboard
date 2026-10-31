#!/usr/bin/env python3
from __future__ import annotations
import json, sys
from datetime import datetime, timezone
from pathlib import Path
from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parent))
from bac_catalog_collector import classify_technology  # noqa: E402 — mismo clasificador que bac_catalog.json, no duplicar reglas
# Re-exportados (split_unidad_ejecutora/parse_fecha_apertura) por compatibilidad de imports/tests.
from bac_buscar_avanzado import SITE, UA, split_unidad_ejecutora, parse_fecha_apertura, parse_row, search  # noqa: E402,F401

BASE = Path(__file__).resolve().parent.parent
OUT = BASE / 'data' / 'bac_aperturas.json'
SCHEMA_VERSION = 2
RUBRO = 'Informática'

# Por qué la Búsqueda avanzada y no los bloques de la home ("Aperturas próximas" / "últimos 30
# días", schema_version 1): esos bloques muestran como máximo 12 filas de TODOS los rubros, y
# el 31/10/2026 ninguna de las 12 "próximas" era de tecnología, mientras BuscarAvanzado.aspx
# devolvía 10 pliegos de Informática en estado Publicado y 22 En Apertura. Acá se recorre el
# universo completo y se compara contra el total que informa BAC.
#
# Tres búsquedas (clave = estado|rubro, así queda en totales_reportados_por_bac):
#  - Publicado, todos los rubros: todo lo que hoy admite ofertas (~180). Da el total como
#    contexto y permite detectar tecnología cargada en OTRO rubro por palabra clave.
#  - Publicado + Informática: para marcar las que BAC clasifica como Informática. La grilla no
#    muestra el rubro, así que sólo se sabe filtrando del lado del servidor.
#  - En Apertura + Informática: ya abrieron y esperan evaluación. Sólo Informática por decisión
#    de alcance (todos los rubros son ~750 filas, sin uso en un dashboard de tecnología).
#
# is_technology = rubro Informática O classify_technology. Ninguno alcanza solo: medido el
# 31/10/2026, la palabra clave reconoce 3 de 179 publicados y el rubro deja afuera, por
# ejemplo, licencias Jira o firewalls F5. tech_source dice por cuál entró, para poder revisar.
SEARCHES = (
    ('Publicado', None),
    ('Publicado', RUBRO),
    ('En Apertura', RUBRO),
)


def search_key(estado, rubro):
    return f'{estado}|{rubro}' if rubro else estado


def now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def read(p, default=None):
    return json.loads(p.read_text(encoding='utf-8')) if p.exists() else default


def write(p, obj):
    p.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding='utf-8')


def tech_source(in_rubro, by_keyword):
    if in_rubro and by_keyword:
        return 'rubro+palabra_clave'
    if in_rubro:
        return 'rubro'
    if by_keyword:
        return 'palabra_clave'
    return None


def tag(row, estado_buscado, in_rubro):
    source = tech_source(in_rubro, classify_technology({'title': row.get('nombre_proceso')}))
    return {'estado_buscado': estado_buscado, **row, 'is_technology': source is not None, 'tech_source': source}


def build_licitaciones(scraped):
    """scraped: {search_key: (rows, total)} -> lista de procesos de tecnología, sin duplicados."""
    publicados = scraped.get(search_key('Publicado', None), ([], None))[0]
    rubro_pub = scraped.get(search_key('Publicado', RUBRO), ([], None))[0]
    en_apertura = scraped.get(search_key('En Apertura', RUBRO), ([], None))[0]

    rubro_ids = {r['numero_proceso'] for r in rubro_pub}
    seen = set()
    out = []
    for row in publicados:
        tagged = tag(row, 'Publicado', row['numero_proceso'] in rubro_ids)
        if tagged['is_technology'] and tagged['numero_proceso'] not in seen:
            seen.add(tagged['numero_proceso'])
            out.append(tagged)
    # Un pliego puede publicarse entre la búsqueda general y la de Informática: si BAC lo marca
    # como Informática pero no apareció en la general, igual se incluye.
    for row in rubro_pub:
        if row['numero_proceso'] not in seen:
            seen.add(row['numero_proceso'])
            out.append(tag(row, 'Publicado', True))
    for row in en_apertura:
        if row['numero_proceso'] not in seen:
            seen.add(row['numero_proceso'])
            out.append(tag(row, 'En Apertura', True))
    # Publicado: la apertura más cercana primero. En Apertura: la más vieja primero (lo que más
    # tiempo lleva sin pasar a evaluación queda arriba).
    out.sort(key=lambda r: (r['estado_buscado'] != 'Publicado', r.get('fecha_apertura') or '9999'))
    return out


def scrape_all():
    result = {}
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            page = browser.new_page(user_agent=UA)
            for estado, rubro in SEARCHES:
                result[search_key(estado, rubro)] = search(page, estado, rubro)
        finally:
            browser.close()
    return result


def main():
    try:
        scraped = scrape_all()
    except Exception as e:
        # Degradación como bac_catalog_collector.py: si ya hay un dataset bueno previo,
        # se conserva y sólo se actualiza el status -no se pisa con listas vacías-, para
        # que una falla puntual del sitio transaccional no borre la última foto conocida.
        existing = read(OUT)
        if existing and existing.get('schema_version') == SCHEMA_VERSION:
            existing['status'] = 'error'
            existing['last_check_status_at'] = now()
            existing['last_error'] = repr(e)
            write(OUT, existing)
        else:
            write(OUT, {
                'schema_version': SCHEMA_VERSION, 'collected_at': now(), 'source': f'{SITE}/BuscarAvanzado.aspx',
                'status': 'error', 'last_error': repr(e), 'rubro_filtro': RUBRO,
                'totales_reportados_por_bac': {}, 'obtenidos': {}, 'licitaciones_abiertas': [],
            })
        print(json.dumps({'status': 'error', 'error': repr(e)}, ensure_ascii=False))
        return

    totales = {k: total for k, (_, total) in scraped.items()}
    obtenidos = {k: len(rows) for k, (rows, _) in scraped.items()}
    # 'incompleto' (no 'error'): lo traído es válido pero no es todo lo que BAC reporta.
    completo = all(totales[k] is None or obtenidos[k] >= totales[k] for k in totales)
    licitaciones = build_licitaciones(scraped)
    out = {
        'schema_version': SCHEMA_VERSION,
        'collected_at': now(),
        'source': f'{SITE}/BuscarAvanzado.aspx',
        'rubro_filtro': RUBRO,
        'note': ('Procesos de tecnología abiertos en Buenos Aires Compras, scrapeados en vivo de la '
                 'Búsqueda avanzada del sitio transaccional: estado Publicado (admiten ofertas) y '
                 'En Apertura (abrieron, pendientes de evaluación; sólo rubro Informática). Un '
                 'proceso cuenta como tecnología si BAC lo clasifica en el rubro Informática o si '
                 'su nombre coincide con las reglas de classify_technology (ver tech_source).'),
        'status': 'ok' if completo else 'incompleto',
        'totales_reportados_por_bac': totales,
        'obtenidos': obtenidos,
        'licitaciones_abiertas': licitaciones,
    }
    write(OUT, out)
    print(json.dumps({'status': out['status'], 'licitaciones_abiertas': len(licitaciones),
                      'obtenidos': obtenidos, 'totales_reportados_por_bac': totales}, ensure_ascii=False))


if __name__ == '__main__':
    main()
