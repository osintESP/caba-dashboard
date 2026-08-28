#!/usr/bin/env python3
"""Rellena ediciones históricas del Boletín Oficial que el pipeline diario nunca
scrapeó (sólo trae la edición vigente en cada corrida). Reusa toda la lógica de
negocio de collector.py/data_model.py — no duplica reglas de merge/clasificación."""
import json, os, time
from pathlib import Path

from collector import client, fetch_header, fetch_bulletin
from data_model import DATA, read, write, now, merge_norm, isproc, category, proceso_id, bac_tender_id

DEFAULT_START = 7275  # 02/01/2026 — coincide con el inicio de cobertura real de BAC
PACE_SECONDS = float(os.getenv('BACKFILL_PACE_SECONDS', '0.4'))


def compute_end(numeros_existentes, override=None):
    if override not in (None, ''):
        return int(override)
    if numeros_existentes:
        return min(numeros_existentes) - 1
    return None


def main():
    s = client()
    editions = read(DATA / 'editions.json', [])
    by_num = {str(e['numero_boletin']): e for e in editions if e.get('numero_boletin') is not None}
    numeros_existentes = [int(n) for n in by_num]

    start = int(os.getenv('BACKFILL_START') or DEFAULT_START)
    end = compute_end(numeros_existentes, os.getenv('BACKFILL_END'))
    if end is None:
        raise SystemExit('no hay ediciones existentes y no se pasó BACKFILL_END: no hay un límite seguro por defecto')
    if end < start:
        print(json.dumps({'status': 'nothing_to_backfill', 'start': start, 'end': end}))
        return

    existing_norms = read(DATA / 'norms.json', [])
    idx = {str(x.get('id_norma')): x for x in existing_norms if x.get('id_norma') is not None}

    total = end - start + 1
    print(json.dumps({'backfill_range': [start, end], 'total_editions': total}))
    processed = 0
    for b in range(start, end + 1):
        header = fetch_header(s, b)
        if not header:
            print(json.dumps({'numero': b, 'status': 'header_not_found'}))
            time.sleep(PACE_SECONDS)
            continue

        report = fetch_bulletin(s, header)
        collected = report['collected_at']
        old_ed = by_num.get(str(b), {})
        by_num[str(b)] = {
            **old_ed, 'numero_boletin': b, 'fecha_publicacion': header.get('fecha_publicacion'),
            'url_boletin': header.get('url_boletin'), 'separata': header.get('separata') or [],
            'first_collected_at': old_ed.get('first_collected_at') or collected,
            'last_collected_at': collected, 'total_normas': report['TOTAL_API'],
            'schema_source': report['schema_version'],
        }
        for n in report.get('normas') or []:
            i = n.get('id_norma')
            if i is None:
                continue
            idx[str(i)] = merge_norm(idx.get(str(i), {}), n, b, header, collected)

        processed += 1
        print(json.dumps({'numero': b, 'fecha': header.get('fecha_publicacion'),
                           'normas': report['TOTAL_API'], 'progreso': f'{processed}/{total}'}, ensure_ascii=False))
        time.sleep(PACE_SECONDS)

    editions_out = sorted(by_num.values(), key=lambda x: int(x['numero_boletin']))
    write(DATA / 'editions.json', editions_out)
    norms = sorted(idx.values(), key=lambda x: str(x.get('id_norma')))
    write(DATA / 'norms.json', norms)

    procs = []
    for n in norms:
        if isproc(n):
            procs.append({
                'id_norma': n.get('id_norma'), 'numero_boletin': n.get('numero_boletin'),
                'fecha_publicacion': n.get('fecha_publicacion'), 'nombre': n.get('nombre'),
                'sumario': n.get('sumario'), 'url_norma': n.get('url_norma'),
                'organismo': n.get('organismo'), 'tipo': n.get('tipo'), 'categoria': category(n),
                'proceso_id': proceso_id(n), 'bac_tender_id': bac_tender_id(n),
                'first_seen_at': n.get('first_seen_at'),
                'last_seen_at': n.get('last_seen_at'), 'estado': 'detectada',
            })
    write(DATA / 'procurements.json', procs)

    proceso_categoria = {}
    for p in procs:
        proceso_categoria.setdefault(p['proceso_id'], p['categoria'])
    cats = {}
    for cat in proceso_categoria.values():
        cats[cat] = cats.get(cat, 0) + 1

    latest_num = max((int(e['numero_boletin']) for e in editions_out), default=None)
    stats = {
        'generated_at': now(), 'latest_bulletin': latest_num, 'editions': len(editions_out),
        'norms': len(norms), 'procurement_acts': len(procs), 'procurements': len(proceso_categoria),
        'procurement_categories': dict(sorted(cats.items())),
    }
    write(DATA / 'stats.json', stats)
    print(json.dumps(stats, ensure_ascii=False))


if __name__ == '__main__':
    main()
