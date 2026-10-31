#!/usr/bin/env python3
from __future__ import annotations
import json, sys
from datetime import datetime, timezone
from pathlib import Path
from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parent))
# Navegación, paginación y parseo de la Búsqueda avanzada viven en bac_buscar_avanzado.py,
# compartidos con bac_aperturas_scraper.py y bac_recurring_vendor_watch.py -no duplicarlos acá-.
from bac_buscar_avanzado import SITE, UA, ROW_KEYS, parse_row, search  # noqa: E402,F401

BASE = Path(__file__).resolve().parent.parent
OUT = BASE / 'data' / 'bac_pliegos_caidos.json'

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


def now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def read(p, default=None):
    return json.loads(p.read_text(encoding='utf-8')) if p.exists() else default


def write(p, obj):
    p.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding='utf-8')


def scrape_all():
    result = {}
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            page = browser.new_page(user_agent=UA)
            for estado in ESTADOS:
                result[estado] = search(page, estado, RUBRO)
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

    pliegos = [{'estado_buscado': estado, **row} for estado, (rows, _) in scraped.items() for row in rows]
    totales = {estado: total for estado, (_, total) in scraped.items()}
    obtenidos = {estado: len(rows) for estado, (rows, _) in scraped.items()}
    # 'incompleto' (no 'error'): los datos traídos son válidos, pero no son todo lo que BAC
    # reporta -antes este truncado pasaba silencioso y el contador quedaba clavado en ~139-.
    completo = all(totales[e] is None or obtenidos[e] >= totales[e] for e in totales)
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
        'status': 'ok' if completo else 'incompleto',
        'totales_reportados_por_bac': totales,
        'pliegos': pliegos,
    }
    write(OUT, out)
    print(json.dumps({'status': out['status'], 'pliegos': len(pliegos), 'obtenidos': obtenidos,
                      'totales_reportados_por_bac': totales}, ensure_ascii=False))


if __name__ == '__main__':
    main()
