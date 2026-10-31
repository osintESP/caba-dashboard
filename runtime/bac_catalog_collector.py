import csv, io, json, re, unicodedata
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import requests

import fx_bcra

BASE = Path(__file__).resolve().parent.parent
OUT = BASE / 'data' / 'bac_catalog.json'
STATE = BASE / 'data' / 'bac_sync_state.json'
API = 'https://data.buenosaires.gob.ar/api/3/action/package_show'
PACKAGE = 'buenos-aires-compras'
# El WAF delante de data.buenosaires.gob.ar empezó a rechazar (HTTP 200 "Request Rejected", HTML)
# el User-Agent por defecto de requests ("python-requests/x.y") en septiembre 2026 -> refresh-bac-data.yml
# falló 4 días seguidos en fetch_package(). Un UA de navegador pasa el WAF sin problema.
HEADERS = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                         '(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36'}
TOP_N_VENDORS = 20
ACTIVE_STATUSES = (None, '', 'active')

# Subir en cada cambio a la lógica de parseo/señales (csv_row_to_release, process_releases,
# build_audit_signals): la fuente (bac_anual.csv) sólo cambia cada 2-3 meses, así que sin esto
# un fix al collector queda "mudo" -unchanged() sigue devolviendo True por ETag- hasta que la
# fuente externa decida re-publicar, en vez de aplicarse en la próxima corrida.
COLLECTOR_VERSION = 15

# El campo 'id' de cada fila de bac_anual.csv arranca con el número de proceso de BAC propio
# (ej. "416-1192-LPU26-16-0" -> proceso "416-1192-LPU26", resto = renglón/parte, ver
# dedupe_csv_rows). Es el MISMO esquema que numero_proceso en bac_aperturas.json y que, en
# texto libre, cita el sumario del Boletín además de la sigla/expediente (ver bac_tender_id en
# data_model.py) — es la clave de cruce por EXPEDIENTE pendiente en el README, más precisa que
# el cruce actual sólo por nombre de organismo.
# \d{1,4} (no sólo 3-4) para el código de organismo: ver misma corrección en BAC_TENDER_RE de
# data_model.py (organismos como Consejo de la Magistratura/Lotería usan código de 1 dígito).
BAC_TENDER_ID_RE = re.compile(r'^(\d{1,4}-\d{3,4}-[a-z]{2,4}\d{2})', re.I)

# "redes" en sentido genérico (eléctricas, de agua, viales, etc.) NO es tecnología;
# sólo cuenta si está calificada como red de datos/informática (mismo criterio que
# ya se aplicó en runtime/data_model.py para evitar el falso positivo "redes eléctricas").
TECH_INCLUDE = [
    r'\bsoftware\b', r'\bhardware\b', r'\binform[aá]tic\w*', r'\btecnol[oó]gic\w*',
    r'\bciberseguridad\b', r'\bservidor(es)?\b', r'\bdata\s*center\b', r'\bcentro\s+de\s+datos\b',
    r'\bnube\b', r'\bcloud\b', r'\bnotebook(s)?\b', r'\bcomputador\w*', r'\bcomputaci[oó]n\b',
    r'\bfirewall\b',
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
    r = requests.get(API, params={'id': PACKAGE}, headers=HEADERS, timeout=30)
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
        r = requests.head(url, headers=HEADERS, timeout=30, allow_redirects=True)
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
    r = requests.get(url, headers=HEADERS, timeout=180)
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


def bac_tender_id(row_id):
    m = BAC_TENDER_ID_RE.match(row_id or '')
    return m.group(1).upper() if m else None


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
            'id': bac_tender_id(row.get('id')),
            'title': row.get('tender/title'),
            'description': row.get('tender/description'),
            'mainProcurementCategory': row.get('tender/mainProcurementCategory'),
            'procuringEntity': {'name': row.get('tender/procuringEntity/name')},
            'procurementMethod': row.get('tender/procurementMethod') or None,
            'competitive': _to_bool(row.get('tender/competitive')),
            # Código del catálogo de bienes y servicios de BAC (x_catalogo_bienes_servicios_bac)
            # del ítem de esta fila. Ver TECH_CATALOG_PREFIXES.
            'items': [{'classification': {'id': row.get('tender/items/0/classification/id') or None}}],
        },
        'parties': ([{'id': party_id, 'name': row.get('parties/0/name')}] if party_id else []),
        'awards': [{
            'status': row.get('awards/0/status') or None,
            'value': ({'amount': amount, 'currency': row.get('awards/0/value/currency') or 'ARS'}
                      if amount is not None else None),
            'suppliers': ([{'id': supplier_id, 'name': supplier_name}]
                          if (supplier_id or supplier_name) else []),
            # Para reconstruir el monto por un camino independiente (ver build_tender_index):
            # cantidad total del renglón en el pliego x precio unitario adjudicado.
            'renglon_quantity': _to_float(row.get('tender/items/0/quantity')),
            # Renglón + código de ítem SIN sufijo -N, derivado del id de la fila y NO de la clave
            # de dedupe_csv_rows: el monto reconstruido se cuenta una vez por renglón aunque
            # el dedupe deje pasar filas repetidas, que es justo lo que tiene que detectar.
            'renglon_key': (('-'.join((row.get('id') or '').split('-')[:-1]) or None),
                            re.sub(r'-\d+$', '', row.get('awards/0/items/0/id') or '') or None),
            'unit_price': _to_float(row.get('awards/0/items/0/unit/value/amount')),
        }],
    }


def dedupe_csv_rows(rows):
    # bac_anual.csv NO es una fila = una adjudicación: BAC emite una fila por cada combinación
    # de (renglón/ítem real, parte asociada -comprador/ente contratante/proveedor-). Verificado
    # contra el CSV real completo (no es teórico): sumar awards/0/value/amount fila por fila
    # infla el total ~2.1x. El propio campo 'id' de la fila codifica esa estructura como
    # "{tender_id}-{renglón}-{parte}"; agrupando por (id sin el último segmento, ítem real vía
    # awards/0/items/0/id) se obtienen grupos con monto 100% consistente en todo el dataset real
    # (0 inconsistencias en 14.937 grupos derivados de 24.911 filas) — esa es la clave usada acá.
    #
    # Ambigüedad residual conocida, no resuelta: en órdenes de compra de convenio marco con
    # decenas/cientos de organismos habilitados a comprar del mismo catálogo (mismo monto
    # repetido en todas las filas), esta clave puede seguir sub-dividiendo por variantes del
    # ítem sin que podamos confirmar contra la fuente aplanada si cada una es una compra real
    # distinta o sólo una habilitación de catálogo -award.csv existe pero usa un esquema de
    # ocid completamente distinto, no cruza contra bac_anual.csv-. Ver README, limitaciones
    # conocidas. Este dedup es una mejora validada sobre no deduplicar nada (que es 100%
    # incorrecto, no una aproximación con error conocido), no una reconciliación perfecta.
    #
    # Sufijo -N de awards/0/items/0/id (corregido el 31/10/2026): las variantes "-0", "-1", "-2"
    # del mismo código de ítem dentro de un renglón NO son ítems distintos con su propio monto.
    # awards/0/value/amount es el valor de la ADJUDICACIÓN del renglón, y BAC lo repite en cada
    # variante (entregas o solicitudes parciales de una orden de compra abierta, cada una con su
    # propia cantidad). Verificado contra el CSV real: en 429 grupos el monto repetido es
    # exactamente cantidad total del renglón x precio unitario (ej. SATE911 3202-2041-LPU25:
    # 4.800 x $1.251.200 = $6.005,76M repetido en 3 variantes con cantidades 26,66/200/200; y el
    # mismo caso 101-0066-LPU26 que antes se tomaba como "ítems reales distintos": 48 x $2.167.200
    # repetido en variantes de 4,23/4,25/5,55 unidades). Contarlas por separado triplicaba montos:
    # tecnología daba ARS 46.879M en vez de ~26.020M. Por eso la clave usa el código de ítem SIN
    # el sufijo y el monto: mismo renglón + mismo ítem + mismo monto = la misma adjudicación.
    # Montos distintos entre variantes se siguen contando por separado (adjudicaciones distintas).
    seen = {}
    for idx, row in enumerate(rows):
        rid = row.get('id') or ''
        parts = rid.split('-')
        n_prefix = '-'.join(parts[:-1]) if len(parts) > 1 else rid
        item = row.get('awards/0/items/0/id')
        item_base = re.sub(r'-\d+$', '', item) if item else item
        amount = _to_float(row.get('awards/0/value/amount'))
        key = (n_prefix or f'__row_{idx}__', item_base, round(amount, 2) if amount is not None else None)
        if key not in seen:
            seen[key] = row
    return list(seen.values())


def parse_csv_releases(csv_text):
    reader = csv.DictReader(io.StringIO(csv_text))
    if not reader.fieldnames or 'date' not in reader.fieldnames or 'tender/title' not in reader.fieldnames:
        raise RuntimeError('el CSV de BAC no tiene las columnas esperadas (schema inesperado)')
    rows = dedupe_csv_rows(reader)
    return [csv_row_to_release(row) for row in rows]


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


# Prefijos del catálogo de bienes y servicios de BAC que son tecnología (análisis del agente de
# auditoría, issue #15, 31/10/2026, sobre el bac_anual.csv real): 13.02 / 13.03 y los rubros
# 13.01.010, .011 y .016 del 13.01. Se excluye 13.01.003 (insumos y tóner): mete lotes mixtos de
# otras áreas (ej. 428-2049 "Hemoterapia"). Suma 24 procesos que las palabras clave no detectaban
# (103 -> 127: Datos Abiertos, OpenShift, IBM Z16, SATE911, 2630-0747 "Sist. ... Mesa de Ayuda").
# No se agregó la regex "\bsist\." por eso: sumaba 8 falsos positivos quirúrgicos.
TECH_CATALOG_PREFIXES = ('13.02.', '13.03.', '13.01.010.', '13.01.011.', '13.01.016.')


def catalog_code(tender):
    items = tender.get('items') or []
    return ((items[0] if items else {}).get('classification') or {}).get('id') or None


def tech_source(tender):
    """'palabra_clave', 'catalogo', 'palabra_clave+catalogo' o None. Las dos vías se suman."""
    text = normalize(' '.join(filter(None, [
        tender.get('title'), tender.get('description'), tender.get('mainProcurementCategory')])))
    by_keyword = (not any(rx.search(text) for rx in _TECH_EXCLUDE_RE)
                  and any(rx.search(text) for rx in _TECH_INCLUDE_RE))
    code = catalog_code(tender) or ''
    by_catalog = code.startswith(TECH_CATALOG_PREFIXES)
    if by_keyword and by_catalog:
        return 'palabra_clave+catalogo'
    return 'palabra_clave' if by_keyword else 'catalogo' if by_catalog else None


def classify_technology(tender):
    return tech_source(tender) is not None


def _parse_date(s):
    try:
        return datetime.fromisoformat(s[:10]).date() if s else None
    except ValueError:
        return None


def process_releases(releases, usd_rate=None):
    # usd_rate: fecha -> ARS por USD (fx_bcra.make_converter), o None. Las adjudicaciones en USD
    # se convierten a ARS al tipo de cambio de su fecha para que entren en totales, rankings y
    # señales (antes se descartaban en silencio: más dólares de tecnología que todo lo adjudicado
    # en pesos, casi todo de la Agencia de Sistemas de Información). Sin cotización para esa
    # fecha -o sin usd_rate- se excluyen, pero quedan contadas en fx_excluded para que se vea.
    vendor_totals, vendor_tech_totals, vendor_awards = {}, {}, {}
    total_ars = tech_ars = 0.0
    awards_counted = 0
    currencies = Counter()
    date_from = date_to = None
    tech_method_amounts = {}
    tech_noncompetitive_open = {'count': 0, 'amount': 0.0}
    org_vendor_tech, org_tech_totals = {}, {}
    direct_awards_by_pair = {}
    direct_awards_by_vendor = {}
    tech_tenders_by_id = {}
    fx_converted = {'awards': 0, 'amount_usd': 0.0, 'tech_amount_usd': 0.0}
    fx_excluded = Counter()
    for rel in releases:
        rel_date = rel.get('date')
        if rel_date:
            date_from = rel_date if date_from is None else min(date_from, rel_date)
            date_to = rel_date if date_to is None else max(date_to, rel_date)
        parties = index_parties(rel)
        tender = rel.get('tender') or {}
        source = tech_source(tender)
        is_tech = source is not None
        # BAC no publica numberOfTenderers/tenderers (verificado: 0 ocurrencias en todo el
        # dataset); 'competitive' es la única señal de competencia real que el propio
        # publicador expone, y vale la pena mirarla: la mayoría de los procesos "open"
        # (licitación pública, nominalmente competitivos) igual vienen con competitive=false.
        method = tender.get('procurementMethod')
        competitive = tender.get('competitive')
        organismo = ((tender.get('procuringEntity') or {}).get('name') or '').strip() or 'Organismo sin identificar'
        tender_id = tender.get('id')
        for award in rel.get('awards') or []:
            status = award.get('status')
            if status not in ACTIVE_STATUSES:
                continue
            value = award.get('value')
            if not is_valid_amount(value):
                continue
            currency = value.get('currency') or 'ARS'
            currencies[currency] += 1
            amount_usd = None
            if currency == 'ARS':
                amount = value['amount']
            else:
                rate = usd_rate(rel_date) if (currency == 'USD' and usd_rate) else None
                if rate is None:
                    fx_excluded[currency] += 1
                    continue
                amount_usd = value['amount']
                amount = amount_usd * rate
                fx_converted['awards'] += 1
                fx_converted['amount_usd'] += amount_usd
                if is_tech:
                    fx_converted['tech_amount_usd'] += amount_usd
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
                            {'date': parsed_date, 'amount': share, 'tender_id': tender_id})
                        direct_awards_by_vendor.setdefault(name, []).append(
                            {'organismo': organismo, 'date': parsed_date, 'amount': share,
                             'tender_id': tender_id})
                if tender_id:
                    entry = tech_tenders_by_id.setdefault(tender_id, {
                        'organismo': organismo, 'method': method, 'competitive': competitive,
                        'amount_ars': 0.0, 'amount_usd': 0.0, 'awards_count': 0, 'suppliers': set(),
                        'tech_sources': set(),
                    })
                    entry['tech_sources'].update(source.split('+'))
                    entry['amount_ars'] += amount
                    # Monto reconstruido = cantidad del renglón x precio unitario adjudicado, en la
                    # misma moneda convertida que amount. Es un cálculo independiente del campo
                    # awards/0/value/amount: si una adjudicación se cuenta de más (como las
                    # variantes -N antes del fix del 31/10/2026), amount_ars lo supera.
                    qty, unit = award.get('renglon_quantity'), award.get('unit_price')
                    rkey = award.get('renglon_key')
                    if qty and unit and rkey and rkey[0]:
                        factor = amount / value['amount'] if value.get('amount') else 1.0
                        entry.setdefault('expected_by_renglon', {})[rkey] = qty * unit * factor
                        entry['reconcilable_awards'] = entry.get('reconcilable_awards', 0) + 1
                    if amount_usd is not None:
                        entry['amount_usd'] += amount_usd
                    entry['awards_count'] += 1
                    entry['suppliers'].update(names)
    return {
        'vendor_totals': vendor_totals, 'vendor_tech_totals': vendor_tech_totals,
        'vendor_awards': vendor_awards, 'total_ars': total_ars, 'tech_ars': tech_ars,
        'awards_counted': awards_counted, 'currencies': dict(currencies),
        'date_from': date_from, 'date_to': date_to,
        'tech_method_amounts': tech_method_amounts, 'tech_noncompetitive_open': tech_noncompetitive_open,
        'org_vendor_tech': org_vendor_tech, 'org_tech_totals': org_tech_totals,
        'direct_awards_by_pair': direct_awards_by_pair, 'direct_awards_by_vendor': direct_awards_by_vendor,
        'tech_tenders_by_id': tech_tenders_by_id,
        'fx_converted': fx_converted, 'fx_excluded': dict(fx_excluded),
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

# - REPEAT_WINNER_MIN_ORGANISMOS / REPEAT_WINNER_WINDOW_DAYS: complementa la señal anterior
#   mirando la otra dimensión — no "mismo organismo, mismo proveedor, muchas veces", sino
#   "mismo proveedor, muchos organismos distintos, en poco tiempo", acotado a adjudicaciones
#   directas/limitadas (sin competencia formal). Un proveedor legítimo puede ganar en muchos
#   organismos por capacidad real; el patrón de auditoría clásico es la concentración de
#   adjudicaciones NO competitivas en múltiples compradores distintos en una ventana corta
#   — posible proveedor recurrente/capturado a nivel ciudad, no detectable mirando un solo
#   organismo a la vez (a diferencia de vendor_concentration_by_organismo).
REPEAT_WINNER_MIN_ORGANISMOS = 3
REPEAT_WINNER_WINDOW_DAYS = 90

# - RECURRING_PAIR_MIN_AWARDS: a diferencia de possible_fractionation (ráfaga: 3+ en 90
#   días), esta señal mira una relación de LARGO PLAZO -organismo+proveedor con adjudicaciones
#   directas/limitadas repetidas SIN ventana de tiempo-, para detectar una posible renovación
#   informal de un contrato por vía directa en vez de re-licitar (en vez de un fraccionamiento
#   puntual). El umbral es más bajo (2, no 3) justamente porque no exige que se agrupen en poco
#   tiempo: 2 adjudicaciones separadas por meses ya es un patrón de recurrencia, no una
#   coincidencia. Arbitrario y documentado como tal, mismo criterio que el resto.
RECURRING_PAIR_MIN_AWARDS = 2


def _group_by_tender(awards):
    # direct_awards_by_pair/by_vendor holds one entry per LINE ITEM (bac_anual.csv's own
    # renglón granularity survives dedupe_csv_rows), not one per adjudicación/process — a single
    # tender with 3 renglones awarded directly to the same vendor is 1 contracting decision, not
    # 3. Collapse by tender_id (summing amounts, keeping the earliest date) before counting so
    # the fractionation/recurring-pair thresholds measure distinct processes, matching what
    # their own 'note' text claims ("N adjudicaciones", not "N renglones"). An entry with no
    # resolvable tender_id (BAC_TENDER_ID_RE didn't match its row id) is kept as its own group —
    # we have no basis to assume it shares a process with anything else.
    groups = {}
    for i, a in enumerate(awards):
        key = a.get('tender_id') or f'__no_tender_{i}__'
        g = groups.setdefault(key, {'date': None, 'amount': 0.0})
        g['amount'] += a['amount']
        if a['date'] and (g['date'] is None or a['date'] < g['date']):
            g['date'] = a['date']
    return list(groups.values())


def build_fractionation_flags(direct_awards_by_pair):
    flags = []
    for (organismo, vendor), awards in direct_awards_by_pair.items():
        processes = _group_by_tender(awards)
        dates = [p['date'] for p in processes if p['date']]
        if len(processes) < FRACTIONATION_MIN_AWARDS or len(dates) < FRACTIONATION_MIN_AWARDS:
            continue
        span_days = (max(dates) - min(dates)).days
        if span_days > FRACTIONATION_WINDOW_DAYS:
            continue
        flags.append({
            'organismo': organismo, 'vendor': vendor,
            'awards_count': len(processes),
            'total_amount_ars': round(sum(p['amount'] for p in processes), 2),
            'date_from': min(dates).isoformat(), 'date_to': max(dates).isoformat(),
            'window_days': span_days,
        })
    flags.sort(key=lambda f: f['total_amount_ars'], reverse=True)
    return flags


def build_repeat_winner_flags(direct_awards_by_vendor):
    flags = []
    for vendor, awards in direct_awards_by_vendor.items():
        organismos = sorted({a['organismo'] for a in awards})
        processes = _group_by_tender(awards)
        dates = [p['date'] for p in processes if p['date']]
        if len(organismos) < REPEAT_WINNER_MIN_ORGANISMOS or len(dates) < REPEAT_WINNER_MIN_ORGANISMOS:
            continue
        span_days = (max(dates) - min(dates)).days
        if span_days > REPEAT_WINNER_WINDOW_DAYS:
            continue
        flags.append({
            'vendor': vendor, 'organismos_count': len(organismos), 'organismos': organismos,
            'awards_count': len(processes),
            'total_amount_ars': round(sum(p['amount'] for p in processes), 2),
            'date_from': min(dates).isoformat(), 'date_to': max(dates).isoformat(),
            'window_days': span_days,
        })
    flags.sort(key=lambda f: f['total_amount_ars'], reverse=True)
    return flags


def build_recurring_pairs_flags(direct_awards_by_pair):
    flags = []
    for (organismo, vendor), awards in direct_awards_by_pair.items():
        processes = _group_by_tender(awards)
        if len(processes) < RECURRING_PAIR_MIN_AWARDS:
            continue
        dates = [p['date'] for p in processes if p['date']]
        flags.append({
            'organismo': organismo, 'vendor': vendor,
            'awards_count': len(processes),
            'total_amount_ars': round(sum(p['amount'] for p in processes), 2),
            'date_from': min(dates).isoformat() if dates else None,
            'date_to': max(dates).isoformat() if dates else None,
        })
    flags.sort(key=lambda f: f['awards_count'], reverse=True)
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
        'repeat_winner_across_organismos': {
            'vendors': build_repeat_winner_flags(stats.get('direct_awards_by_vendor', {})),
            'note': (f'Proveedor con adjudicaciones directas o de contratación limitada en '
                     f'{REPEAT_WINNER_MIN_ORGANISMOS} o más organismos distintos en un lapso de '
                     f'hasta {REPEAT_WINNER_WINDOW_DAYS} días, en compras de tecnología. Un '
                     'proveedor legítimo puede ganar en múltiples organismos por capacidad real '
                     '— esto no prueba irregularidad, es un disparador para revisar si hay un '
                     'proveedor recurrente/potencialmente favorecido a nivel ciudad.'),
        },
        'recurring_direct_pairs': {
            'pairs': build_recurring_pairs_flags(stats.get('direct_awards_by_pair', {})),
            'note': (f'Organismo + proveedor con {RECURRING_PAIR_MIN_AWARDS} o más adjudicaciones '
                     'directas o de contratación limitada en compras de tecnología, SIN ventana de '
                     'tiempo (a diferencia de possible_fractionation, que exige que se agrupen en '
                     f'{FRACTIONATION_WINDOW_DAYS} días). Es la lista de candidatos para el chequeo '
                     'en vivo de bac_recurring_vendor_watch.py: una posible renovación informal por '
                     'vía directa en vez de re-licitar, no una ráfaga puntual. No implica '
                     'irregularidad por sí sola — un proveedor puede ganar de nuevo legítimamente.'),
        },
    }


def build_tender_index(stats):
    # Índice por proceso (no por organismo): la clave es el número de proceso propio de BAC
    # (ej. "416-1192-LPU26"), el mismo esquema que numero_proceso en bac_aperturas.json y que
    # el sumario del Boletín suele citar como texto libre (ver bac_tender_id en data_model.py).
    # Acotado a tecnología, igual criterio que el resto de audit_signals, para no listar los
    # ~15.000 procesos del dataset completo. Es la clave de cruce por EXPEDIENTE que faltaba
    # -antes sólo existía el cruce por nombre de organismo (bacOrgIndex en app.js)-.
    return {tender_id: {
        'organismo': entry['organismo'], 'method': entry['method'], 'competitive': entry['competitive'],
        'amount_ars': round(entry['amount_ars'], 2), 'awards_count': entry['awards_count'],
        'suppliers': sorted(entry['suppliers']),
        # Parte del monto adjudicada originalmente en USD (ya incluida en amount_ars, convertida).
        **({'amount_usd': round(entry['amount_usd'], 2)} if entry.get('amount_usd') else {}),
        'tech_source': '+'.join(sorted(entry.get('tech_sources') or [])) or None,
        # Reconciliación (data_health_check.check_bac_reconciliation): sólo si TODAS las
        # adjudicaciones del proceso traen cantidad y precio unitario.
        **({'monto_reconstruido_ars': round(sum(entry['expected_by_renglon'].values()), 2)}
           if entry.get('reconcilable_awards') == entry['awards_count'] else {}),
    } for tender_id, entry in stats.get('tech_tenders_by_id', {}).items()}


def build_output(result, resource, stats, status, releases_count):
    ranking = sorted(stats['vendor_totals'].items(), key=lambda kv: kv[1], reverse=True)[:TOP_N_VENDORS]
    vendor_ranking = [{
        'name': name, 'amount_ars': round(amount, 2),
        'tech_amount_ars': round(stats['vendor_tech_totals'].get(name, 0), 2),
        'awards': stats['vendor_awards'].get(name, 0),
    } for name, amount in ranking]
    # El ranking general (arriba) ordena por monto TOTAL adjudicado, sin importar rubro: los
    # proveedores más grandes de BAC son medicamentos/alimentos/insumos hospitalarios, no
    # tecnología, así que el top-20 general puede no tener NINGÚN proveedor con gasto en
    # tecnología (confirmado en producción). El dashboard audita tecnología específicamente,
    # así que se expone un ranking separado ordenado por vendor_tech_totals para ese propósito.
    tech_ranking = sorted(stats['vendor_tech_totals'].items(), key=lambda kv: kv[1], reverse=True)[:TOP_N_VENDORS]
    vendor_ranking_tech = [{
        'name': name, 'amount_ars': round(amount, 2),
        'awards': stats['vendor_awards'].get(name, 0),
    } for name, amount in tech_ranking]
    return {
        'schema_version': 3,
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
            'fx': {
                'source': fx_bcra.SOURCE,
                'method': ('Adjudicaciones en USD convertidas a ARS a la cotización del día de la '
                           'adjudicación (o del último día hábil previo). Los montos en ARS de este '
                           'archivo las incluyen; amount_usd conserva el monto original.'),
                'usd_awards_converted': (stats.get('fx_converted') or {}).get('awards', 0),
                'usd_amount': round((stats.get('fx_converted') or {}).get('amount_usd', 0), 2),
                'usd_tech_amount': round((stats.get('fx_converted') or {}).get('tech_amount_usd', 0), 2),
                'excluded_awards_by_currency': stats.get('fx_excluded', {}),
            },
        },
        'vendor_ranking': vendor_ranking,
        'vendor_ranking_tech': vendor_ranking_tech,
        'audit_signals': build_audit_signals(stats),
        'tender_index': build_tender_index(stats),
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


def usd_converter(releases):
    usd_dates = sorted(str(r.get('date'))[:10] for r in releases if r.get('date') and any(
        ((a.get('value') or {}).get('currency') == 'USD') for a in r.get('awards') or []))
    if not usd_dates:
        return None
    rates = fx_bcra.load_rates(datetime.fromisoformat(usd_dates[0]).date(), datetime.fromisoformat(usd_dates[-1]).date())
    return fx_bcra.make_converter(rates)


def resolve_source(prev_state, previous_out, fetch=None):
    """Devuelve (result, resource, api_error). Si la API de CKAN no responde JSON, se usa el CSV conocido.

    El 31/10/2026 data.buenosaires.gob.ar empezó a responder la API de CKAN con un desafío
    JavaScript anti-bots (F5 "TSPD"), tanto desde GitHub Actions como desde una IP local: un
    cambio de User-Agent ya no alcanza (ver HEADERS). El CSV en sí se sirve desde otro host
    (cdn.buenosaires.gob.ar) que sigue respondiendo, y su URL ya está en bac_sync_state.json
    desde la última corrida buena. La API sólo se usa para descubrir esa URL y la metadata del
    dataset, así que ante una falla se sigue con la URL y la metadata conocidas, en vez de caerse.
    """
    fetch = fetch or fetch_package
    try:
        result = fetch()
        return result, find_resource(result), None
    except (requests.RequestException, ValueError, RuntimeError) as e:
        url = prev_state.get('url')
        if not url:
            raise
        prev = previous_out or {}
        ds = prev.get('dataset') or {}
        result = ({'id': ds.get('id'), 'title': ds.get('title'), 'metadata_modified': ds.get('metadata_modified'),
                   'license_id': ds.get('license_id'), 'organization': {'title': ds.get('organization')}}
                  if ds else None)
        resource = {**(prev.get('resource') or {}), 'url': url}
        return result, resource, f'API de catálogo no disponible ({type(e).__name__}); se usó la URL conocida del CSV'


def main():
    prev_state = read(STATE, {}) or {}
    result, resource, api_error = resolve_source(prev_state, read(OUT))

    if not resource:
        write(STATE, {**prev_state, 'checked_at': now(), 'status': 'resource_not_found'})
        _mark_stale(result, None, 'resource_not_found')
        print(json.dumps({'status': 'resource_not_found'}))
        return

    url = resource['url']
    fp = head_fingerprint(url)
    if unchanged(prev_state, url, fp):
        write(STATE, {**prev_state, **(fp or {}), 'url': url, 'checked_at': now(), 'status': 'unchanged',
                      'catalog_api_error': api_error})
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

    stats = process_releases(releases, usd_rate=usd_converter(releases))
    out = build_output(result, resource, stats, 'ok', len(releases))
    write(OUT, out)
    write(STATE, {**(fp or {}), 'url': url, 'checked_at': now(), 'downloaded_at': now(), 'status': 'ok',
                  'collector_version': COLLECTOR_VERSION, **({'catalog_api_error': api_error} if api_error else {})})
    print(json.dumps({'status': 'ok', 'releases': len(releases),
                       'vendors': len(stats['vendor_totals']), 'awards': stats['awards_counted']},
                      ensure_ascii=False))


if __name__ == '__main__':
    main()
