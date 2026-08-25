#!/usr/bin/env python3
from __future__ import annotations
import json, re
from datetime import datetime, timezone
from pathlib import Path
from playwright.sync_api import sync_playwright

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
    return row


def scrape_page(page, link_href_fragment):
    with page.expect_navigation(timeout=30000):
        page.click(f'a[href*="{link_href_fragment}"]')
    page.wait_for_selector('table tbody', timeout=15000)
    raw_rows = page.eval_on_selector_all(
        'table tbody tr',
        "rows => rows.map(r => Array.from(r.querySelectorAll('td')).map(td => td.innerText.trim()))",
    )
    return [parse_row(cells) for cells in raw_rows if len(cells) >= len(ROW_KEYS)]


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
        **scraped,
    }
    write(OUT, out)
    print(json.dumps({
        'status': 'ok',
        'aperturas_recientes': len(out.get('aperturas_recientes', [])),
        'aperturas_proximas': len(out.get('aperturas_proximas', [])),
    }, ensure_ascii=False))


if __name__ == '__main__':
    main()
