#!/usr/bin/env python3
"""Helpers compartidos para la Búsqueda avanzada (BuscarAvanzado.aspx) del sitio transaccional de BAC.

Los usan bac_aperturas_scraper.py, bac_pliegos_caidos_scraper.py y bac_recurring_vendor_watch.py.
Viven acá -no en uno de esos scrapers- para que ningún scraper dependa de otro: antes
pliegos_caidos importaba el parseo de aperturas y vendor_watch la paginación de pliegos_caidos.

Restricción de navegación (ver CLAUDE.md): el sitio es ASP.NET WebForms con ViewState de sesión.
goto() directo a BuscarAvanzado.aspx redirige a Default.aspx -hay que cargar la home y CLICKEAR
el link real, para que el postback se genere con una ViewState viva-.
"""
from __future__ import annotations
import re

SITE = 'https://www.buenosairescompras.gob.ar'
UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/120.0 Safari/537.36')
GRID = '#ctl00_CPH1_GridListaPliegos'
# Tope de seguridad, no de negocio: la grilla pagina de a 10 y algunos universos son grandes
# ("Dejado Sin Efecto" + Informática dio 537 resultados el 23/10/2026). El truncado se detecta
# comparando contra el total que reporta BAC (read_reported_total), no confiando en el tope.
MAX_PAGES = 100

ROW_KEYS = ('numero_proceso', 'nombre_proceso', 'tipo_proceso', 'fecha_apertura_raw', 'estado', 'unidad_ejecutora_raw')
UNIDAD_RE = re.compile(r'^\s*(\d+)\s*-\s*(.+?)\s*$')
FECHA_RE = re.compile(r'(\d{2})/(\d{2})/(\d{4})\s+(\d{2}):(\d{2})')


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


def read_grid_rows(page):
    # tr.pagination-gv es la fila del paginador, que contiene una tabla anidada con su propio
    # <tr> de números de página -por eso se toman sólo los <tr> HIJOS DIRECTOS de la grilla:
    # con un selector descendiente ese <tr> anidado se colaba como fila de datos ("1","2",...).
    return page.eval_on_selector_all(
        f'{GRID} > tbody > tr:not(.pagination-gv)',
        "rows => rows.map(r => Array.from(r.querySelectorAll('td')).map(td => td.innerText.trim()))",
    )


def data_rows(raw_rows):
    return [r for r in raw_rows if r and r[0] != 'Número proceso' and len(r) >= len(ROW_KEYS)]


def click_next_page(page, current_page):
    # DevExpress renderiza la página actual como <span>, las demás como <a> con
    # __doPostBack(...,'Page$N') -se busca el link por el argumento 'Page$N' del href, NO por
    # su texto: el paginador muestra de a 10 números y el salto a la página 11 es un link
    # "..." (href 'Page$11'), así que buscar por texto "11" cortaba siempre en la página 10.
    target = current_page + 1
    links = page.locator(f'{GRID} tr.pagination-gv a[href*="\'Page${target}\'"]')
    if links.count() == 0:
        return False
    links.first.click()
    # Esperar a que el paginador marque la página nueva como actual en vez de un sleep fijo:
    # leer la grilla antes de que termine el postback duplicaría la página anterior.
    page.wait_for_selector(f'{GRID} tr.pagination-gv span:text-is("{target}")', timeout=30000)
    return True


def read_reported_total(page):
    # BAC muestra "Se han encontrado (N) resultados para su búsqueda" -se guarda para poder
    # verificar que la paginación trajo todo, en vez de confiar en que el tope alcanza.
    match = re.search(r'Se han encontrado \((\d+)\)', page.inner_text('body'))
    return int(match.group(1)) if match else None


def open_search(page):
    page.goto(f'{SITE}/Default.aspx', timeout=30000, wait_until='networkidle')
    with page.expect_navigation(timeout=30000):
        page.click('a[href*="BuscarAvanzado"]')
    page.wait_for_selector('#ctl00_CPH1_ddlEstadoProceso', timeout=15000)


def search(page, estado, rubro=None, max_pages=MAX_PAGES):
    """Abre una búsqueda nueva (sesión limpia de filtros) y devuelve (filas parseadas, total reportado)."""
    open_search(page)
    page.select_option('#ctl00_CPH1_ddlEstadoProceso', label=estado)
    if rubro:
        page.select_option('#ctl00_CPH1_ddlRubro', label=rubro)
    page.click('#ctl00_CPH1_btnListarPliegoAvanzado')
    try:
        page.wait_for_selector(GRID, timeout=15000)
    except Exception:
        # Sin grilla puede ser una búsqueda legítimamente vacía (ej. ningún pliego de
        # Informática publicado hoy) o una falla del sitio. Sólo se acepta como vacía si BAC
        # informa el total explícitamente; si no, se propaga y el caller conserva el último
        # dataset bueno en vez de publicar una lista vacía falsa.
        total = read_reported_total(page)
        if total is None:
            raise
        return [], total
    page.wait_for_timeout(1500)

    reported_total = read_reported_total(page)
    rows = []
    current_page = 1
    while True:
        rows.extend(parse_row(cells) for cells in data_rows(read_grid_rows(page)))
        if current_page >= max_pages or not click_next_page(page, current_page):
            break
        current_page += 1
    return rows, reported_total
