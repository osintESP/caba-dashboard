#!/usr/bin/env python3
from __future__ import annotations
import json, re, sys, unicodedata
from datetime import datetime, timezone
from pathlib import Path
from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parent))
# read_grid_rows/click_next_page/parse_row/ROW_KEYS son genéricos al patrón de grilla+paginador
# de BuscarAvanzado.aspx, no específicos de "pliegos caídos" -reusarlos, no duplicarlos.
from bac_pliegos_caidos_scraper import read_grid_rows, click_next_page, parse_row, ROW_KEYS  # noqa: E402

BASE = Path(__file__).resolve().parent.parent
CATALOG = BASE / 'data' / 'bac_catalog.json'
OUT = BASE / 'data' / 'bac_recurring_vendor_watch.json'
SITE = 'https://www.buenosairescompras.gob.ar'
UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/120.0 Safari/537.36')

# Enriquecimiento del ítem #1 del roadmap (prórrogas encubiertas / contrataciones directas
# por urgencia): bac_anual.csv SÍ trae proveedor (a diferencia de BuscarAvanzado.aspx, cuya
# grilla de resultados no lo expone), pero está ~3 meses atrasado. Estrategia acotada, no
# bulk: los candidatos (organismo+proveedor con adjudicaciones directas repetidas, SIN ventana
# de tiempo -relación de largo plazo, no ráfaga-) salen de bac_catalog.json.audit_signals.
# recurring_direct_pairs (ya calculado por bac_catalog_collector.py sobre el CSV). Para cada
# candidato -universo chico, ver README- se busca en vivo si hubo una adjudicación MÁS
# RECIENTE que coverage.date_to al mismo organismo, y si la hay, se confirma el proveedor
# visitando el detalle del proceso (VistaPreviaPliegoCiudadano.aspx, tabla "Orden de compra")
# antes de marcarlo — evita el falso positivo de "mismo organismo, proveedor distinto".
TIPO_PROCESO = 'Contratación Directa'
RUBRO = 'Informática'
ESTADO = 'Adjudicado'
MAX_PAGES = 40  # universo real (TipoProceso+Rubro+Estado) verificado en ~10-15 páginas


def now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def read(p, default=None):
    return json.loads(p.read_text(encoding='utf-8')) if p.exists() else default


def write(p, obj):
    p.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding='utf-8')


def normalize_name(name):
    # Mismo criterio que normalizeOrgName en app.js: mayúsculas, sin acentos, sin puntos,
    # espacios colapsados. Se usa tanto para organismo como para proveedor -mismo tipo de
    # comparación "¿es el mismo texto salvo formato?"-. Deliberadamente estricto (no colapsa
    # espacios internos de siglas tipo "S.A." -> "SA"): preferimos un falso NEGATIVO (no
    # confirmar un match real) a un falso POSITIVO (confirmar un match que no es), dado que
    # esta es la lógica que decide qué se muestra como señal de auditoría.
    text = unicodedata.normalize('NFKD', name or '')
    text = ''.join(c for c in text if not unicodedata.combining(c))
    text = text.replace('.', '')
    text = re.sub(r'\s+', ' ', text).strip()
    return text.upper()


def load_candidates():
    catalog = read(CATALOG)
    if not catalog:
        return [], None
    pairs = catalog.get('audit_signals', {}).get('recurring_direct_pairs', {}).get('pairs', [])
    coverage_date_to = catalog.get('coverage', {}).get('date_to')
    return pairs, coverage_date_to


def is_after_cutoff(fecha_apertura, cutoff):
    # Ambos son strings ISO8601 con el mismo offset fijo (-03:00), así que comparación de
    # string alcanza -no hace falta parsear a datetime-.
    if not fecha_apertura or not cutoff:
        return False
    return fecha_apertura > cutoff


def find_candidate_matches(rows, candidates, cutoff):
    # Filtra filas de la búsqueda en vivo cuyo organismo matchea un candidato Y cuya fecha es
    # posterior al corte del CSV -sólo nos interesa lo que el CSV todavía no vio-. Devuelve
    # pares (candidate, row) que necesitan confirmación de proveedor en el detalle del proceso;
    # todavía NO confirma proveedor (eso requiere visitar la página en vivo).
    candidates_by_org = {}
    for c in candidates:
        candidates_by_org.setdefault(normalize_name(c.get('organismo')), []).append(c)
    matches = []
    for row in rows:
        row_org_norm = normalize_name(row.get('organismo'))
        if row_org_norm not in candidates_by_org:
            continue
        if not is_after_cutoff(row.get('fecha_apertura'), cutoff):
            continue
        for candidate in candidates_by_org[row_org_norm]:
            matches.append((candidate, row))
    return matches


def vendor_confirmed(detail_proveedor, candidate_vendor):
    if not detail_proveedor or not candidate_vendor:
        return False
    return normalize_name(detail_proveedor) == normalize_name(candidate_vendor)


def extract_orden_compra(page):
    # Tabla "Orden de compra" en VistaPreviaPliegoCiudadano.aspx: Número/Tipo de Documento/
    # Estado/Nombre proveedor/Identificador tributario/Fecha perfeccionamiento/Monto -se busca
    # por header en vez de por selector de posición, más resiliente a la estructura DevExpress.
    tables = page.eval_on_selector_all('table', """
      els => els.map(t => {
        const rows = Array.from(t.querySelectorAll('tr'));
        if (!rows.length) return null;
        const header = Array.from(rows[0].querySelectorAll('th,td')).map(c => c.innerText.trim());
        const body = rows.slice(1).map(r => Array.from(r.querySelectorAll('td')).map(c => c.innerText.trim()));
        return {header, body};
      }).filter(Boolean)
    """)
    for t in tables:
        header = t['header']
        if 'Nombre proveedor' not in header:
            continue
        idx = {name: i for i, name in enumerate(header)}
        for row in t['body']:
            if len(row) <= idx.get('Nombre proveedor', -1):
                continue
            def cell(col):
                i = idx.get(col)
                return row[i] if i is not None and len(row) > i else None
            return {
                'numero_orden_compra': cell('Número'), 'proveedor': cell('Nombre proveedor'),
                'identificador_tributario': cell('Identificador tributario'),
                'fecha_perfeccionamiento': cell('Fecha perfeccionamiento'), 'monto': cell('Monto'),
            }
    return None


def search_direct_awards(page):
    page.select_option('#ctl00_CPH1_ddlTipoProceso', label=TIPO_PROCESO)
    page.select_option('#ctl00_CPH1_ddlRubro', label=RUBRO)
    page.select_option('#ctl00_CPH1_ddlEstadoProceso', label=ESTADO)
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


def confirm_candidate(page, candidate, row):
    numero_proceso = row.get('numero_proceso')
    link = page.locator(f'#ctl00_CPH1_GridListaPliegos a:text-is("{numero_proceso}")')
    if link.count() == 0:
        return None
    with page.expect_navigation(timeout=30000):
        link.first.click()
    page.wait_for_timeout(1500)
    detail = extract_orden_compra(page)
    page.go_back(timeout=30000, wait_until='networkidle')
    page.wait_for_selector('#ctl00_CPH1_GridListaPliegos', timeout=15000)
    if not detail or not vendor_confirmed(detail.get('proveedor'), candidate.get('vendor')):
        return None
    return {
        'numero_proceso': numero_proceso, 'nombre_proceso': row.get('nombre_proceso'),
        'fecha_apertura': row.get('fecha_apertura'), 'proveedor_confirmado': detail.get('proveedor'),
        'monto': detail.get('monto'), 'fecha_perfeccionamiento': detail.get('fecha_perfeccionamiento'),
    }


def watch_all():
    candidates, cutoff = load_candidates()
    if not candidates:
        return [], cutoff
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            page = browser.new_page(user_agent=UA)
            page.goto(f'{SITE}/Default.aspx', timeout=30000, wait_until='networkidle')
            with page.expect_navigation(timeout=30000):
                page.click('a[href*="BuscarAvanzado"]')
            page.wait_for_selector('#ctl00_CPH1_ddlEstadoProceso', timeout=15000)
            rows = search_direct_awards(page)
            candidate_matches = find_candidate_matches(rows, candidates, cutoff)
            results = []
            for candidate in candidates:
                own_matches = [row for cand, row in candidate_matches if cand is candidate]
                confirmed = None
                for row in own_matches:
                    confirmed = confirm_candidate(page, candidate, row)
                    if confirmed:
                        break
                results.append({
                    'organismo': candidate.get('organismo'), 'vendor': candidate.get('vendor'),
                    'known_awards_count': candidate.get('awards_count'),
                    'known_date_to': candidate.get('date_to'),
                    'confirmed_recent_award': confirmed,
                })
        finally:
            browser.close()
    return results, cutoff


def main():
    try:
        results, cutoff = watch_all()
    except Exception as e:
        existing = read(OUT)
        if existing:
            existing['status'] = 'error'
            existing['last_check_status_at'] = now()
            existing['last_error'] = repr(e)
            write(OUT, existing)
        else:
            write(OUT, {
                'schema_version': 1, 'collected_at': now(), 'source': f'{SITE}/BuscarAvanzado.aspx',
                'status': 'error', 'last_error': repr(e), 'candidates': [],
            })
        print(json.dumps({'status': 'error', 'error': repr(e)}, ensure_ascii=False))
        return

    out = {
        'schema_version': 1,
        'collected_at': now(),
        'source': f'{SITE}/BuscarAvanzado.aspx',
        'candidate_source': 'bac_catalog.json audit_signals.recurring_direct_pairs',
        'coverage_date_to_used_as_cutoff': cutoff,
        'note': ('Candidatos: organismo+proveedor con adjudicaciones directas/limitadas '
                 'repetidas en compras de tecnología, según bac_anual.csv (ver '
                 'recurring_direct_pairs en bac_catalog.json). Para cada uno se buscó en vivo '
                 '-BuscarAvanzado.aspx del sitio transaccional de BAC, no el export masivo- si '
                 'hubo una adjudicación directa más reciente que la cobertura del CSV al mismo '
                 'organismo, y se confirmó la identidad del proveedor visitando el detalle del '
                 'proceso antes de marcarlo. No implica irregularidad: un proveedor puede ganar '
                 'de nuevo legítimamente por capacidad real — es un disparador para revisar si '
                 'el contrato se está renovando informalmente por vía directa en vez de '
                 're-licitar. Universo de candidatos chico hoy porque bac_anual.csv sólo '
                 'cubre unos meses; es esperable "sin resultados" la mayor parte del tiempo, '
                 'mismo criterio que repeat_winner_across_organismos.'),
        'status': 'ok',
        'candidates': results,
    }
    write(OUT, out)
    confirmed_count = sum(1 for r in results if r.get('confirmed_recent_award'))
    print(json.dumps({'status': 'ok', 'candidates': len(results), 'confirmed': confirmed_count},
                      ensure_ascii=False))


if __name__ == '__main__':
    main()
