#!/usr/bin/env python3
from __future__ import annotations
import json, re, sys
from datetime import datetime, timezone
from pathlib import Path
from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parent))
from bac_catalog_collector import classify_technology  # noqa: E402 — mismo clasificador que bac_catalog.json, no duplicar reglas

BASE = Path(__file__).resolve().parent.parent
OUT = BASE / 'data' / 'bac_aperturas.json'
SITE = 'https://www.buenosairescompras.gob.ar'
UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/120.0 Safari/537.36')

# El sitio transaccional de BAC (no el portal de datos abiertos) es ASP.NET WebForms con
# sesión/token anti-CSRF. Pegarle directo a la URL de listado -incluso con un navegador
# headless real- redirige a Default.aspx (confirmado). Sólo funciona navegando "como
# usuario real": cargar la home y CLICKEAR el link real de cada listado, para que el
# postback/ViewState se genere con el estado de sesión correcto. requests puro replicando
# un HAR capturado del navegador ya se probó y se descartó (ver README, limitaciones
# conocidas) — esto es lo único que funcionó.
PAGES = (
    ('aperturas_recientes', 'ListarAperturaUltimos30Dias'),
    ('aperturas_proximas', 'ListarAperturaProxima'),
)

ROW_KEYS = ('numero_proceso', 'nombre_proceso', 'tipo_proceso', 'fecha_apertura_raw', 'estado', 'unidad_ejecutora_raw')
UNIDAD_RE = re.compile(r'^\s*(\d+)\s*-\s*(.+?)\s*$')
FECHA_RE = re.compile(r'(\d{2})/(\d{2})/(\d{4})\s+(\d{2}):(\d{2})')
# Filas del paginador de la grilla: sólo números de página y "..." (p. ej. "1\n2\n...\n10\n...").
PAGER_CELL_RE = re.compile(r'^[\d.\s]*$')


def now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def read(p, default=None):
    return json.loads(p.read_text(encoding='utf-8')) if p.exists() else default


def write(p, obj):
    p.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding='utf-8')


def split_unidad_ejecutora(raw):
    # BAC muestra "9625 - DIRECCIÓN GENERAL MUSEO DE ARTE MODERO..." — el código numérico
    # es el mismo esquema que usa bac_anual.csv (tender/procuringEntity/id), separado acá
    # del nombre para que sea comparable/cruzable sin parsear el string de nuevo aguas abajo.
    m = UNIDAD_RE.match(raw or '')
    if not m:
        return None, (raw or '').strip() or None
    return m.group(1), m.group(2)


def parse_fecha_apertura(raw):
    m = FECHA_RE.search(raw or '')
    if not m:
        return None
    d, mo, y, h, mi = m.groups()
    return f'{y}-{mo}-{d}T{h}:{mi}:00-03:00'


def parse_row(cells):
    row = dict(zip(ROW_KEYS, [c.strip() for c in cells]))
    codigo, organismo = split_unidad_ejecutora(row.pop('unidad_ejecutora_raw', None))
    fecha_raw = row.pop('fecha_apertura_raw', None)
    row['unidad_ejecutora_codigo'] = codigo
    row['organismo'] = organismo
    row['fecha_apertura'] = parse_fecha_apertura(fecha_raw)
    # El objeto del dashboard es auditar contrataciones de TECNOLOGÍA, no todas las compras
    # de la Ciudad (la mayoría de las aperturas son insumos médicos, obra, etc.). Se guarda
    # el flag acá en vez de descartar la fila, para no perder el conteo total como contexto
    # (mismo criterio que bac_catalog_collector.py con is_tech); el frontend filtra sobre esto.
    row['is_technology'] = classify_technology({'title': row.get('nombre_proceso')})
    return row


def is_pager_row(cells):
    return any((c or '').strip() for c in cells) and all(PAGER_CELL_RE.match(c or '') for c in cells)


def split_grid_rows(raw_rows):
    # 'table tbody tr' también matchea la fila del paginador y el <tr> de su tabla anidada:
    # se colaban como aperturas basura (numero_proceso "1\n2\n...", sin fecha) -mismo bug que
    # tuvo bac_pliegos_caidos_scraper.py-. Que haya paginador significa además que hay más
    # páginas que la primera, que es la única que se lee hoy: se devuelve para no truncar en
    # silencio. Se exige más de un número de página por si BAC renderiza el paginador ("1")
    # aun cuando el listado entra en una sola página.
    pager = [cells for cells in raw_rows if is_pager_row(cells)]
    data = [cells for cells in raw_rows if not is_pager_row(cells) and len(cells) >= len(ROW_KEYS)]
    return data, any(len(re.findall(r'\d+', ' '.join(cells))) > 1 for cells in pager)


def scrape_page(page, link_href_fragment):
    with page.expect_navigation(timeout=30000):
        page.click(f'a[href*="{link_href_fragment}"]')
    page.wait_for_selector('table tbody', timeout=15000)
    raw_rows = page.eval_on_selector_all(
        'table tbody tr',
        "rows => rows.map(r => Array.from(r.querySelectorAll('td')).map(td => td.innerText.trim()))",
    )
    data_rows, has_more_pages = split_grid_rows(raw_rows)
    return [parse_row(cells) for cells in data_rows], has_more_pages


def scrape_all():
    result = {}
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            page = browser.new_page(user_agent=UA)
            for key, fragment in PAGES:
                page.goto(f'{SITE}/Default.aspx', timeout=30000, wait_until='networkidle')
                result[key] = scrape_page(page, fragment)
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
        if existing:
            existing['status'] = 'error'
            existing['last_check_status_at'] = now()
            existing['last_error'] = repr(e)
            write(OUT, existing)
        else:
            write(OUT, {
                'schema_version': 1, 'collected_at': now(), 'source': f'{SITE}/',
                'status': 'error', 'last_error': repr(e),
                'aperturas_recientes': [], 'aperturas_proximas': [],
            })
        print(json.dumps({'status': 'error', 'error': repr(e)}, ensure_ascii=False))
        return

    out = {
        'schema_version': 1, 'collected_at': now(), 'source': f'{SITE}/', 'status': 'ok',
        **{key: rows for key, (rows, _) in scraped.items()},
    }
    truncados = [key for key, (_, has_more_pages) in scraped.items() if has_more_pages]
    if truncados:
        # 'incompleto' (no 'error'), mismo criterio que bac_pliegos_caidos_scraper.py: lo traído
        # es válido pero es sólo la primera página -p. ej. "últimos 30 días" quedaba cubriendo
        # apenas el último día hábil-. Se mantiene hasta que el scraper siga el paginador.
        out['status'] = 'incompleto'
        out['listados_con_paginas_sin_leer'] = truncados
    write(OUT, out)
    print(json.dumps({
        'status': out['status'],
        'aperturas_recientes': len(out.get('aperturas_recientes', [])),
        'aperturas_proximas': len(out.get('aperturas_proximas', [])),
    }, ensure_ascii=False))


if __name__ == '__main__':
    main()
