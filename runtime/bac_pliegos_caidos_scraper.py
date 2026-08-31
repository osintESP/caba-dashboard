#!/usr/bin/env python3
from __future__ import annotations
import json, sys
from datetime import datetime, timezone
from pathlib import Path
from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parent))
# split_unidad_ejecutora/parse_fecha_apertura son funciones puras de parseo de texto -
# reusarlas, no duplicarlas (mismo formato de columna que devuelve BAC en ambos listados).
from bac_aperturas_scraper import split_unidad_ejecutora, parse_fecha_apertura  # noqa: E402

BASE = Path(__file__).resolve().parent.parent
OUT = BASE / 'data' / 'bac_pliegos_caidos.json'
SITE = 'https://www.buenosairescompras.gob.ar'
UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/120.0 Safari/537.36')

# Mismo sitio transaccional y misma restricción de navegación que bac_aperturas_scraper.py
# (ASP.NET WebForms con ViewState de sesión: goto() directo a BuscarAvanzado.aspx no sirve,
# hay que clickear como un usuario real desde Default.aspx).
#
# Por qué este scraper existe además de bac_catalog_collector.py: bac_anual.csv (y los otros
# 7 archivos del mismo dataset masivo — tender.csv, award.csv, contracts.csv, etc.) comparten
# el mismo Last-Modified (verificado vía HEAD contra el CDN), congelado desde el 1° de junio
# -BA Data sólo regenera el export completo esporádicamente, no es un problema de qué archivo
# elegimos dentro del paquete-. La Búsqueda avanzada de BAC (BuscarAvanzado.aspx) en cambio
# pega contra la base transaccional real: confirmado un pliego "Desierto" con fecha de
# apertura de agosto 2026, muy posterior al corte del export masivo. Filtra Estado de Proceso
# y Rubro NATIVAMENTE del lado del servidor -Rubro=Informática es más preciso que reclasificar
# por regex acá (ver classify_technology en bac_catalog_collector.py, que igual no aplica:
# nunca se llama en este archivo, se confía en el filtro server-side)-.
ESTADOS = ('Desierto', 'Dejado Sin Efecto')
RUBRO = 'Informática'
MAX_PAGES = 20  # tope de seguridad -el universo real (Estado+Rubro) es chico, 2-3 páginas-

ROW_KEYS = ('numero_proceso', 'nombre_proceso', 'tipo_proceso', 'fecha_apertura_raw', 'estado', 'unidad_ejecutora_raw')


def now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def read(p, default=None):
    return json.loads(p.read_text(encoding='utf-8')) if p.exists() else default


def write(p, obj):
    p.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding='utf-8')


def parse_row(cells):
    row = dict(zip(ROW_KEYS, [c.strip() for c in cells]))
    codigo, organismo = split_unidad_ejecutora(row.pop('unidad_ejecutora_raw', None))
    fecha_raw = row.pop('fecha_apertura_raw', None)
    row['unidad_ejecutora_codigo'] = codigo
    row['organismo'] = organismo
    row['fecha_apertura'] = parse_fecha_apertura(fecha_raw)
    return row


def read_grid_rows(page):
    # tr.pagination-gv es la fila del paginador (números de página en una tabla anidada);
    # se excluye acá en vez de filtrarse después para no confundirla con una fila de datos.
    return page.eval_on_selector_all(
        '#ctl00_CPH1_GridListaPliegos tr:not(.pagination-gv)',
        "rows => rows.map(r => Array.from(r.querySelectorAll('td')).map(td => td.innerText.trim()))",
    )


def click_next_page(page, current_page):
    # DevExpress renderiza la página actual como <span>, las demás como <a> con
    # __doPostBack(...,'Page$N') -se busca puntualmente el link de la página siguiente en
    # vez de "cualquier link del paginador" para no volver atrás cuando páginas ya vistas
    # vuelven a aparecer como link.
    target = str(current_page + 1)
    links = page.locator(f'tr.pagination-gv a:text-is("{target}")')
    if links.count() == 0:
        return False
    links.first.click()
    page.wait_for_timeout(1200)
    return True


def scrape_estado(page, estado):
    page.select_option('#ctl00_CPH1_ddlEstadoProceso', label=estado)
    page.select_option('#ctl00_CPH1_ddlRubro', label=RUBRO)
    page.click('#ctl00_CPH1_btnListarPliegoAvanzado')
    page.wait_for_selector('#ctl00_CPH1_GridListaPliegos', timeout=15000)
    page.wait_for_timeout(1500)

    rows = []
    current_page = 1
    while True:
        raw_rows = read_grid_rows(page)
        data_rows = [r for r in raw_rows if r and r[0] != 'Número proceso' and len(r) >= len(ROW_KEYS)]
        rows.extend(parse_row(cells) for cells in data_rows)
        if current_page >= MAX_PAGES or not click_next_page(page, current_page):
            break
        current_page += 1
    return rows


def scrape_all():
    result = {}
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            page = browser.new_page(user_agent=UA)
            for estado in ESTADOS:
                page.goto(f'{SITE}/Default.aspx', timeout=30000, wait_until='networkidle')
                with page.expect_navigation(timeout=30000):
                    page.click('a[href*="BuscarAvanzado"]')
                page.wait_for_selector('#ctl00_CPH1_ddlEstadoProceso', timeout=15000)
                result[estado] = scrape_estado(page, estado)
        finally:
            browser.close()
    return result


def main():
    try:
        scraped = scrape_all()
    except Exception as e:
        # Mismo criterio de degradación que bac_aperturas_scraper.py: conservar el último
        # dataset bueno conocido ante una falla puntual del sitio, no pisarlo con listas vacías.
        existing = read(OUT)
        if existing:
            existing['status'] = 'error'
            existing['last_check_status_at'] = now()
            existing['last_error'] = repr(e)
            write(OUT, existing)
        else:
            write(OUT, {
                'schema_version': 1, 'collected_at': now(), 'source': f'{SITE}/BuscarAvanzado.aspx',
                'estados_monitoreados': list(ESTADOS), 'rubro_filtro': RUBRO,
                'status': 'error', 'last_error': repr(e), 'pliegos': [],
            })
        print(json.dumps({'status': 'error', 'error': repr(e)}, ensure_ascii=False))
        return

    pliegos = [{'estado_buscado': estado, **row} for estado, rows in scraped.items() for row in rows]
    pliegos.sort(key=lambda r: r.get('fecha_apertura') or '', reverse=True)
    out = {
        'schema_version': 1,
        'collected_at': now(),
        'source': f'{SITE}/BuscarAvanzado.aspx',
        'estados_monitoreados': list(ESTADOS),
        'rubro_filtro': RUBRO,
        'note': ('Pliegos de tecnología (rubro "Informática", filtrado del lado del servidor '
                 'por BAC) declarados Desierto o Dejado Sin Efecto, scrapeados en vivo del '
                 'sitio transaccional de Buenos Aires Compras -no del export masivo trimestral '
                 '(bac_anual.csv), que puede tener varios meses de atraso-. No implica '
                 'irregularidad: un pliego puede caer por motivos operativos legítimos, pero '
                 'suele forzar una re-licitación y trabar la operación del área usuaria.'),
        'status': 'ok',
        'pliegos': pliegos,
    }
    write(OUT, out)
    print(json.dumps({'status': 'ok', 'pliegos': len(pliegos)}, ensure_ascii=False))


if __name__ == '__main__':
    main()
