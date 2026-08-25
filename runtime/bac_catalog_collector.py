import csv, io, json, re, unicodedata
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import requests

BASE = Path(__file__).resolve().parent.parent
OUT = BASE / 'data' / 'bac_catalog.json'
STATE = BASE / 'data' / 'bac_sync_state.json'
API = 'https://data.buenosaires.gob.ar/api/3/action/package_show'
PACKAGE = 'buenos-aires-compras'
TOP_N_VENDORS = 20
ACTIVE_STATUSES = (None, '', 'active')

# Subir en cada cambio a la lógica de parseo/señales (csv_row_to_release, process_releases,
# build_audit_signals): la fuente (bac_anual.csv) sólo cambia cada 2-3 meses, así que sin esto
# un fix al collector queda "mudo" -unchanged() sigue devolviendo True por ETag- hasta que la
# fuente externa decida re-publicar, en vez de aplicarse en la próxima corrida.
COLLECTOR_VERSION = 2

# "redes" en sentido genérico (eléctricas, de agua, viales, etc.) NO es tecnología;
# sólo cuenta si está calificada como red de datos/informática (mismo criterio que
# ya se aplicó en runtime/data_model.py para evitar el falso positivo "redes eléctricas").
TECH_INCLUDE = [
    r'\bsoftware\b', r'\bhardware\b', r'\binform[aá]tic\w*', r'\btecnol[oó]gic\w*',
    r'\bciberseguridad\b', r'\bservidor(es)?\b', r'\bdata\s*center\b', r'\bcentro\s+de\s+datos\b',
    r'\bnube\b', r'\bcloud\b', r'\bnotebook(s)?\b', r'\bcomputador\w*', r'\bfirewall\b',
    r'\blicencia(s)?\s+de\s+software\b', r'\bequipamiento\s+inform[aá]tico\b',
    r'\btelecomunicaciones\b', r'\bredes?\s+(de\s+)?(datos|inform[aá]tic\w*|c[oó]mputo)\b',
    r'\bconectividad\b', r'\bsistemas?\s+inform[aá]tic\w*',
]
TECH_EXCLUDE = [
    r'\bredes?\s+el[eé]ctric\w*', r'\bredes?\s+de\s+agua\b', r'\bredes?\s+cloacal\w*',
    r'\bredes?\s+vial\w*', r'\bredes?\s+de\s+gas\b', r'\bredes?\s+de\s+alumbrado\b',
    r'\bredes?\s+de\s+riego\b', r'\bredes?\s+de\s+incendio\b', r'\bredes?\s+pluvial\w*',
]
_TECH_INCLUDE_RE = [re.compile(p) for p in TECH_INCLUDE]
_TECH_EXCLUDE_RE = [re.compile(p) for p in TECH_EXCLUDE]


def read(p, default=None):
    return json.loads(p.read_text(encoding='utf-8')) if p.exists() else default


def write(p, obj):
    p.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding='utf-8')


def normalize(text):
    text = unicodedata.normalize('NFKD', text or '')
    return ''.join(c for c in text if not unicodedata.combining(c)).lower()


def now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def fetch_package():
    r = requests.get(API, params={'id': PACKAGE}, timeout=30)
    r.raise_for_status()
    p = r.json()
    if not p.get('success'):
        raise RuntimeError('BA Data package_show success=false')
    return p['result']


def find_resource(result):
    # El nombre visible en CKAN ("Buenos Aires Compras") no identifica el archivo de forma
    # confiable; el filename real (bac_anual.csv) está en la URL del recurso, no en 'name'.
    # Se usa el CSV y no el JSON homónimo: se verificó contra el CDN real que bac_anual.json
    # sólo contiene datos de 2022 (obsoleto, nunca actualiza su contenido pese a resubirse),
    # mientras que bac_anual.csv tiene datos reales con ~2-3 meses de atraso.
    candidates = [res for res in (result.get('resources') or [])
                  if (res.get('url') or '').lower().endswith('bac_anual.csv')
                  and (res.get('format') or '').lower() == 'csv']
    if not candidates:
        return None
    candidates.sort(key=lambda r: r.get('last_modified') or '', reverse=True)
    return candidates[0]


def head_fingerprint(url):
    try:
        r = requests.head(url, timeout=30, allow_redirects=True)
        r.raise_for_status()
        return {'etag': r.headers.get('ETag'), 'last_modified': r.headers.get('Last-Modified'),
                'content_length': r.headers.get('Content-Length')}
    except requests.RequestException:
        return None


def unchanged(prev_state, url, fp):
    if not prev_state or fp is None or prev_state.get('url') != url:
        return False
    if prev_state.get('collector_version') != COLLECTOR_VERSION:
        return False
    for key in ('etag', 'last_modified', 'content_length'):
        prev_v, new_v = prev_state.get(key), fp.get(key)
        if prev_v and new_v:
            return prev_v == new_v
    return False


def download(url):
    r = requests.get(url, timeout=180)
    r.raise_for_status()
    r.encoding = 'utf-8'
    return r.text


def _to_float(v):
    try:
        return float(v) if v not in (None, '') else None
    except ValueError:
        return None


def _to_bool(v):
    if v is None:
        return None
    s = str(v).strip().lower()
    if s in ('true', '1', 'yes', 'si', 'sí'):
        return True
    if s in ('false', '0', 'no'):
        return False
    return None


def csv_row_to_release(row):
    # bac_anual.csv es OCDS aplanado: sólo trae el índice 0 de cada array (awards/0/...,
    # parties/0/...) — si un award tuviera más de un proveedor (co-adjudicación), acá sólo
    # se ve el primero. Limitación real del formato, documentada también en el README.
    amount = _to_float(row.get('awards/0/value/amount'))
    supplier_name = row.get('awards/0/suppliers/0/name') or None
    supplier_id = row.get('awards/0/suppliers/0/id') or None
    party_id = row.get('parties/0/id') or None
    return {
        'date': row.get('date') or None,
        'tender': {
            'title': row.get('tender/title'),
            'description': row.get('tender/description'),
            'mainProcurementCategory': row.get('tender/mainProcurementCategory'),
            'procuringEntity': {'name': row.get('tender/procuringEntity/name')},
            'procurementMethod': row.get('tender/procurementMethod') or None,
            'competitive': _to_bool(row.get('tender/competitive')),
        },
        'parties': ([{'id': party_id, 'name': row.get('parties/0/name')}] if party_id else []),
        'awards': [{
            'status': row.get('awards/0/status') or None,
            'value': ({'amount': amount, 'currency': row.get('awards/0/value/currency') or 'ARS'}
                      if amount is not None else None),
            'suppliers': ([{'id': supplier_id, 'name': supplier_name}]
                          if (supplier_id or supplier_name) else []),
        }],
    }


def parse_csv_releases(csv_text):
    reader = csv.DictReader(io.StringIO(csv_text))
    if not reader.fieldnames or 'date' not in reader.fieldnames or 'tender/title' not in reader.fieldnames:
        raise RuntimeError('el CSV de BAC no tiene las columnas esperadas (schema inesperado)')
    return [csv_row_to_release(row) for row in reader]


def index_parties(release):
    return {p.get('id'): p for p in (release.get('parties') or []) if p.get('id')}


def resolve_supplier_names(award, parties_by_id):
    names = []
    for s in award.get('suppliers') or []:
        name = s.get('name')
        if not name and s.get('id'):
            party = parties_by_id.get(s['id'])
            name = party.get('name') if party else None
        names.append(name or s.get('id') or 'Proveedor sin identificar')
    return names


def is_valid_amount(value_block):
    if not value_block:
        return False
    amount = value_block.get('amount')
    return isinstance(amount, (int, float)) and amount > 0


def classify_technology(tender):
    text = normalize(' '.join(filter(None, [
        tender.get('title'), tender.get('description'), tender.get('mainProcurementCategory')])))
    if any(rx.search(text) for rx in _TECH_EXCLUDE_RE):
        return False
    return any(rx.search(text) for rx in _TECH_INCLUDE_RE)


def _parse_date(s):
    try:
        return datetime.fromisoformat(s[:10]).date() if s else None
    except ValueError:
        return None


def process_releases(releases):
    vendor_totals, vendor_tech_totals, vendor_awards = {}, {}, {}
    total_ars = tech_ars = 0.0
    awards_counted = 0
    currencies = Counter()
    date_from = date_to = None
    tech_method_amounts = {}
    tech_noncompetitive_open = {'count': 0, 'amount': 0.0}
    org_vendor_tech, org_tech_totals = {}, {}
    direct_awards_by_pair = {}
    for rel in releases:
        rel_date = rel.get('date')
        if rel_date:
            date_from = rel_date if date_from is None else min(date_from, rel_date)
            date_to = rel_date if date_to is None else max(date_to, rel_date)
        parties = index_parties(rel)
        tender = rel.get('tender') or {}
        is_tech = classify_technology(tender)
        # BAC no publica numberOfTenderers/tenderers (verificado: 0 ocurrencias en todo el
        # dataset); 'competitive' es la única señal de competencia real que el propio
        # publicador expone, y vale la pena mirarla: la mayoría de los procesos "open"
        # (licitación pública, nominalmente competitivos) igual vienen con competitive=false.
        method = tender.get('procurementMethod')
        competitive = tender.get('competitive')
        organismo = ((tender.get('procuringEntity') or {}).get('name') or '').strip() or 'Organismo sin identificar'
        for award in rel.get('awards') or []:
            status = award.get('status')
            if status not in ACTIVE_STATUSES:
                continue
            value = award.get('value')
            if not is_valid_amount(value):
                continue
            currency = value.get('currency') or 'ARS'
            currencies[currency] += 1
            if currency != 'ARS':
                continue
            amount = value['amount']
            names = resolve_supplier_names(award, parties)
            if not names:
                continue
            share = amount / len(names)
            for name in names:
                vendor_totals[name] = vendor_totals.get(name, 0) + share
                vendor_awards[name] = vendor_awards.get(name, 0) + 1
                if is_tech:
                    vendor_tech_totals[name] = vendor_tech_totals.get(name, 0) + share
            total_ars += amount
            awards_counted += 1
            if is_tech:
                tech_ars += amount
                tech_method_amounts[method] = tech_method_amounts.get(method, 0) + amount
                if method == 'open' and competitive is False:
                    tech_noncompetitive_open['count'] += 1
                    tech_noncompetitive_open['amount'] += amount
                org_tech_totals[organismo] = org_tech_totals.get(organismo, 0) + amount
                bucket = org_vendor_tech.setdefault(organismo, {})
                for name in names:
                    bucket[name] = bucket.get(name, 0) + share
                if method in ('direct', 'limited'):
                    parsed_date = _parse_date(rel_date)
                    for name in names:
                        direct_awards_by_pair.setdefault((organismo, name), []).append(
                            {'date': parsed_date, 'amount': share})
    return {
        'vendor_totals': vendor_totals, 'vendor_tech_totals': vendor_tech_totals,
        'vendor_awards': vendor_awards, 'total_ars': total_ars, 'tech_ars': tech_ars,
        'awards_counted': awards_counted, 'currencies': dict(currencies),
        'date_from': date_from, 'date_to': date_to,
        'tech_method_amounts': tech_method_amounts, 'tech_noncompetitive_open': tech_noncompetitive_open,
        'org_vendor_tech': org_vendor_tech, 'org_tech_totals': org_tech_totals,
        'direct_awards_by_pair': direct_awards_by_pair,
    }


# Umbrales de las señales de auditoría, deliberadamente documentados por ser arbitrarios:
# - CONCENTRATION_MIN_AMOUNT: evita marcar como "concentración" a un organismo que sólo
#   hizo una compra chica y única (100% de un solo vendor es trivial si el volumen es bajo).
# - CONCENTRATION_HIGH_PCT: umbral de "un proveedor se lleva casi todo" para destacar en el
#   ranking; no implica irregularidad por sí solo, es un disparador para revisión manual.
CONCENTRATION_MIN_AMOUNT = 1_000_000
CONCENTRATION_HIGH_PCT = 60

# - FRACTIONATION_MIN_AWARDS / FRACTIONATION_WINDOW_DAYS: no conocemos el umbral legal
#   exacto de contratación directa/menor de CABA desde este dataset, así que en vez de
#   inventar un monto, la señal marca un patrón de REPETICIÓN (varias adjudicaciones
#   directas/limitadas al mismo proveedor por el mismo organismo, agrupadas en una ventana
#   corta) — disparador de revisión manual de posible fraccionamiento, no una acusación.
FRACTIONATION_MIN_AWARDS = 3
FRACTIONATION_WINDOW_DAYS = 90


def build_fractionation_flags(direct_awards_by_pair):
    flags = []
    for (organismo, vendor), awards in direct_awards_by_pair.items():
        dates = [a['date'] for a in awards if a['date']]
        if len(awards) < FRACTIONATION_MIN_AWARDS or len(dates) < FRACTIONATION_MIN_AWARDS:
            continue
        span_days = (max(dates) - min(dates)).days
        if span_days > FRACTIONATION_WINDOW_DAYS:
            continue
        flags.append({
            'organismo': organismo, 'vendor': vendor,
            'awards_count': len(awards),
            'total_amount_ars': round(sum(a['amount'] for a in awards), 2),
            'date_from': min(dates).isoformat(), 'date_to': max(dates).isoformat(),
            'window_days': span_days,
        })
    flags.sort(key=lambda f: f['total_amount_ars'], reverse=True)
    return flags


def build_audit_signals(stats):
    tech_ars = stats.get('tech_ars', 0) or 0
    method_amounts = stats.get('tech_method_amounts', {})
    direct_or_limited = sum(v for k, v in method_amounts.items() if k in ('direct', 'limited'))
    noncompetitive_open = stats.get('tech_noncompetitive_open', {'count': 0, 'amount': 0.0})

    concentration = []
    for organismo, vendors in stats.get('org_vendor_tech', {}).items():
        org_total = stats.get('org_tech_totals', {}).get(organismo, 0)
        if org_total < CONCENTRATION_MIN_AMOUNT or not vendors:
            continue
        top_vendor, top_amount = max(vendors.items(), key=lambda kv: kv[1])
        share_pct = round(top_amount / org_total * 100, 2)
        concentration.append({
            'organismo': organismo, 'top_vendor': top_vendor,
            'top_vendor_amount_ars': round(top_amount, 2),
            'organismo_tech_amount_ars': round(org_total, 2),
            'top_vendor_share_pct': share_pct,
            'vendors_count': len(vendors),
            'high_concentration': share_pct >= CONCENTRATION_HIGH_PCT,
        })
    concentration.sort(key=lambda c: c['top_vendor_share_pct'], reverse=True)

    return {
        'method_breakdown_ars': {k: round(v, 2) for k, v in method_amounts.items()},
        'direct_or_limited_share_pct': (round(direct_or_limited / tech_ars * 100, 2) if tech_ars else 0),
        'non_competitive_open_tenders': {
            'count': noncompetitive_open['count'],
            'amount_ars': round(noncompetitive_open['amount'], 2),
            'share_pct_of_tech': (round(noncompetitive_open['amount'] / tech_ars * 100, 2) if tech_ars else 0),
            'note': ('Procesos formalmente públicos (procurementMethod=open) que BAC marca '
                     'como sin competencia real (competitive=false), en compras de tecnología. '
                     'BAC no publica numberOfTenderers/tenderers; esta es la única señal de '
                     'competencia real que expone el propio publicador.'),
        },
        # Sin tope: el frontend necesita poder buscar CUALQUIER organismo del ranking
        # del Boletín acá (cruce por nombre), no sólo los de mayor concentración.
        'vendor_concentration_by_organismo': concentration,
        'possible_fractionation': {
            'awards': build_fractionation_flags(stats.get('direct_awards_by_pair', {})),
            'note': (f'Organismo + proveedor con {FRACTIONATION_MIN_AWARDS} o más adjudicaciones '
                     f'directas o de contratación limitada en un lapso de hasta '
                     f'{FRACTIONATION_WINDOW_DAYS} días, en compras de tecnología. No implica que '
                     'se haya superado un umbral legal específico (ese dato no está en el '
                     'dataset) — es un disparador para revisión manual de posible fraccionamiento.'),
        },
    }


def build_output(result, resource, stats, status, releases_count):
    ranking = sorted(stats['vendor_totals'].items(), key=lambda kv: kv[1], reverse=True)[:TOP_N_VENDORS]
    vendor_ranking = [{
        'name': name, 'amount_ars': round(amount, 2),
        'tech_amount_ars': round(stats['vendor_tech_totals'].get(name, 0), 2),
        'awards': stats['vendor_awards'].get(name, 0),
    } for name, amount in ranking]
    return {
        'schema_version': 2,
        'collected_at': now(),
        'source': {'owner': 'Gobierno de la Ciudad Autónoma de Buenos Aires',
                   'api': 'https://data.buenosaires.gob.ar/api/3/action',
                   'dataset': PACKAGE, 'standard': 'OCDS 1.1 (flattened CSV)'},
        'dataset': ({'id': result.get('id'), 'title': result.get('title'),
                     'metadata_modified': result.get('metadata_modified'),
                     'license_id': result.get('license_id'),
                     'organization': (result.get('organization') or {}).get('title')}
                    if result else None),
        'resource': ({'id': resource.get('id'), 'name': resource.get('name'),
                      'format': resource.get('format'), 'url': resource.get('url'),
                      'last_modified': resource.get('last_modified')} if resource else None),
        'coverage': {
            'releases_processed': releases_count,
            'date_from': stats.get('date_from'), 'date_to': stats.get('date_to'),
            'note': ('Ventana de cobertura de bac_anual.csv no confirmada oficialmente por el '
                     'publicador; inferida del rango real de fechas de los releases procesados. '
                     'La fuente suele tener ~2-3 meses de atraso respecto a la fecha actual.'),
        },
        'summary': {
            'total_awarded_ars': round(stats.get('total_ars', 0), 2),
            'tech_awarded_ars': round(stats.get('tech_ars', 0), 2),
            'tech_share_pct': (round(stats['tech_ars'] / stats['total_ars'] * 100, 2)
                                if stats.get('total_ars') else 0),
            'awards_counted': stats.get('awards_counted', 0),
            'vendors_counted': len(stats.get('vendor_totals', {})),
            'currencies': stats.get('currencies', {}),
        },
        'vendor_ranking': vendor_ranking,
        'audit_signals': build_audit_signals(stats),
        'status': status,
        'generated_by': 'bac_catalog_collector.py',
    }


def _mark_stale(result, resource, status, error=None):
    # Si ya existe un bac_catalog.json de una corrida exitosa previa, sólo se actualiza su
    # 'status' (y se agrega el error) sin tocar vendor_ranking/audit_signals: sirve el último
    # dato bueno conocido, pero el status deja de mentir si las fallas se repiten corrida
    # tras corrida (antes quedaba congelado en 'ok' para siempre porque sólo se escribía
    # OUT cuando el archivo no existía todavía).
    existing = read(OUT)
    if existing:
        existing['status'] = status
        existing['last_check_status_at'] = now()
        if error:
            existing['last_error'] = error
        write(OUT, existing)
    elif not OUT.exists():
        write(OUT, build_output(result, resource, process_releases([]), status, 0))


def main():
    prev_state = read(STATE, {}) or {}
    result = fetch_package()
    resource = find_resource(result)

    if not resource:
        write(STATE, {**prev_state, 'checked_at': now(), 'status': 'resource_not_found'})
        _mark_stale(result, None, 'resource_not_found')
        print(json.dumps({'status': 'resource_not_found'}))
        return

    url = resource['url']
    fp = head_fingerprint(url)
    if unchanged(prev_state, url, fp):
        write(STATE, {**prev_state, **(fp or {}), 'url': url, 'checked_at': now(), 'status': 'unchanged'})
        print(json.dumps({'status': 'unchanged'}))
        return

    try:
        csv_text = download(url)
        releases = parse_csv_releases(csv_text)
    except (requests.RequestException, RuntimeError, csv.Error) as e:
        write(STATE, {**prev_state, 'url': url, 'checked_at': now(),
                       'status': 'download_error', 'error': str(e)})
        _mark_stale(result, resource, 'download_error', str(e))
        print(json.dumps({'status': 'download_error', 'error': str(e)}))
        return

    stats = process_releases(releases)
    out = build_output(result, resource, stats, 'ok', len(releases))
    write(OUT, out)
    write(STATE, {**(fp or {}), 'url': url, 'checked_at': now(), 'downloaded_at': now(), 'status': 'ok',
                  'collector_version': COLLECTOR_VERSION})
    print(json.dumps({'status': 'ok', 'releases': len(releases),
                       'vendors': len(stats['vendor_totals']), 'awards': stats['awards_counted']},
                      ensure_ascii=False))


if __name__ == '__main__':
    main()
