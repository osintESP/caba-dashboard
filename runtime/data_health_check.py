#!/usr/bin/env python3
"""Chequeo de invariantes de los datasets publicados en data/.

Complementa a check-data-freshness.yml: ése pregunta "¿corrió hoy?", éste pregunta "¿lo que
corrió tiene sentido?". Existe porque un scraper puede terminar con status 'ok' y aun así
traer datos rotos sin que nadie lo note -caso real: bac_pliegos_caidos_scraper.py truncó en
100 de 537 resultados y metió filas basura del paginador durante casi dos meses-. Cada chequeo
es determinístico y barato (sólo lee data/*.json, nunca toca las fuentes); los análisis de
tendencia o de criterio de auditoría quedan para la revisión semanal con agente, que propone
cambios por PR en vez de aplicarlos.

Sale con código 1 si hay algún problema e imprime un reporte en Markdown por stdout.
"""
from __future__ import annotations
import json, re, sys
from datetime import datetime, timezone
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
DATA = BASE / 'data'

# Ventana de ediciones recientes donde un número de Boletín faltante cuenta como hueco. No se
# mira todo el histórico: el backfill dejó huecos viejos conocidos que no son una regresión.
RECENT_EDITIONS_WINDOW = 30
# bac_anual.csv se publica con ~2-3 meses de atraso (ver README); más allá de esto la fuente
# probablemente dejó de regenerarse y las señales de auditoría que dependen de ella envejecen.
BAC_COVERAGE_MAX_AGE_DAYS = 150
NUMERO_PROCESO_RE = re.compile(r'^\d+-\d+-[A-Z]+\d+$')


def read(name):
    p = DATA / name
    return json.loads(p.read_text(encoding='utf-8')) if p.exists() else None


def check_editions(editions):
    issues = []
    nums = sorted({int(e['numero_boletin']) for e in editions or [] if e.get('numero_boletin')})
    recent = nums[-RECENT_EDITIONS_WINDOW:]
    if recent:
        missing = sorted(set(range(recent[0], recent[-1] + 1)) - set(recent))
        if missing:
            issues.append(f'editions.json: faltan Boletines entre las últimas {RECENT_EDITIONS_WINDOW} '
                          f'ediciones: {", ".join(map(str, missing))} (re-correr backfill-boletin.yml).')
    empty = [e['numero_boletin'] for e in editions or [] if e.get('numero_boletin') in recent and not e.get('total_normas')]
    if empty:
        issues.append(f'editions.json: ediciones recientes con 0 normas: {", ".join(map(str, empty))}.')
    return issues


def malformed_rows(rows):
    return [r.get('numero_proceso') for r in rows
            if not NUMERO_PROCESO_RE.match(r.get('numero_proceso') or '') or not r.get('fecha_apertura')]


def check_pliegos_caidos(data):
    if data is None:
        return ['bac_pliegos_caidos.json no existe.']
    issues = []
    if data.get('status') != 'ok':
        issues.append(f'bac_pliegos_caidos.json: status={data.get("status")!r}'
                      f'{" — " + data["last_error"] if data.get("last_error") else ""}.')
    pliegos = data.get('pliegos') or []
    malformed = malformed_rows(pliegos)
    if malformed:
        issues.append(f'bac_pliegos_caidos.json: {len(malformed)} filas sin número de proceso válido o sin '
                      f'fecha (¿fila del paginador u otro cambio de HTML?): {malformed[:5]}.')
    keys = [(p.get('estado_buscado'), p.get('numero_proceso')) for p in pliegos]
    if len(keys) != len(set(keys)):
        issues.append(f'bac_pliegos_caidos.json: {len(keys) - len(set(keys))} filas duplicadas '
                      '(¿la paginación leyó la misma página dos veces?).')
    for estado, total in (data.get('totales_reportados_por_bac') or {}).items():
        got = sum(1 for p in pliegos if p.get('estado_buscado') == estado)
        if total is not None and got != total:
            issues.append(f'bac_pliegos_caidos.json: "{estado}" trajo {got} filas, BAC reporta {total}.')
    return issues


def check_aperturas(data):
    # Mismo invariante de filas basura que pliegos caídos: bac_aperturas_scraper.py publicó
    # con status 'ok' la fila del paginador ("1\n2\n...\n10\n...") como si fuera una apertura.
    issues = check_status('bac_aperturas.json', data)
    for key in ('aperturas_recientes', 'aperturas_proximas'):
        malformed = malformed_rows((data or {}).get(key) or [])
        if malformed:
            issues.append(f'bac_aperturas.json: {key} tiene {len(malformed)} filas sin número de proceso '
                          f'válido o sin fecha (¿fila del paginador?): {malformed[:5]}.')
    return issues


def check_status(name, data, ok_states=('ok',)):
    if data is None:
        return [f'{name} no existe.']
    if data.get('status') not in ok_states:
        err = f' — {data["last_error"]}' if data.get('last_error') else ''
        return [f'{name}: status={data.get("status")!r}{err}.']
    return []


def check_bac_coverage(catalog, now):
    date_to = ((catalog or {}).get('coverage') or {}).get('date_to')
    if not date_to:
        return []
    age = (now - datetime.fromisoformat(date_to)).days
    if age > BAC_COVERAGE_MAX_AGE_DAYS:
        return [f'bac_catalog.json: la cobertura de bac_anual.csv termina el {date_to[:10]} ({age} días); '
                'la fuente puede haber dejado de regenerarse.']
    return []


def run_checks(now=None):
    now = now or datetime.now(timezone.utc)
    return {
        'Boletín Oficial': check_editions(read('editions.json')),
        'BAC pliegos caídos': check_pliegos_caidos(read('bac_pliegos_caidos.json')),
        'BAC aperturas': check_aperturas(read('bac_aperturas.json')),
        'BAC proveedor recurrente': check_status('bac_recurring_vendor_watch.json',
                                                 read('bac_recurring_vendor_watch.json')),
        'BAC catálogo': (check_status('bac_catalog.json', read('bac_catalog.json'))
                         + check_bac_coverage(read('bac_catalog.json'), now)),
    }


def main():
    results = run_checks()
    lines = []
    for section, issues in results.items():
        lines.append(f'- **{section}**: ' + ('OK' if not issues else ''))
        lines.extend(f'  - {i}' for i in issues)
    print('\n'.join(lines))
    sys.exit(1 if any(results.values()) else 0)


if __name__ == '__main__':
    main()
