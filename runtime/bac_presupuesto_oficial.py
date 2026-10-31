#!/usr/bin/env python3
"""Presupuesto oficial de las licitaciones de tecnología abiertas + señal "presupuesto entre los más altos".

La grilla de la Búsqueda avanzada de BAC (bac_aperturas.json) no trae montos. La página de
detalle de cada proceso (VistaPreviaPliegoCiudadano.aspx) sí, en el bloque "Monto y duración del
contrato" (Monto, Moneda, Duración). Verificado el 31/10/2026 con los dos procesos del SPRS: el
monto coincide con el presupuesto oficial de la resolución publicada en el Boletín.

Carga sobre BAC: cada proceso se visita UNA vez (caché en data/bac_presupuestos.json) y sólo se
re-visita mientras sigue Publicado, cada REFRESH_DAYS, por si una circular modificatoria cambia el
monto. Tope MAX_VISITS_PER_RUN por corrida. La navegación sigue la regla de CLAUDE.md: home ->
click en Búsqueda avanzada -> buscar por número -> click en el link del proceso (nunca goto()).

La señal (definida con el agente de auditoría, ver README): se compara cada presupuesto contra
los de las demás licitaciones de tecnología abiertas, en USD (ARS convertidos a la cotización
BCRA de la fecha de publicación), en escala logarítmica con z robusto (mediana/MAD). Umbrales
arbitrarios y documentados como tales. Un presupuesto alto NO indica sobreprecio: puede reflejar
alcance o un contrato plurianual; es un disparador para revisar alcance, renglones y competencia.

Privacidad: la página de detalle incluye nombres y DNI de supervisores y evaluadores. Acá sólo se
extraen monto, moneda, duración y fecha de publicación; el texto completo nunca se guarda.
"""
from __future__ import annotations
import json, math, re, statistics, sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fx_bcra  # noqa: E402
from bac_buscar_avanzado import SITE, UA, GRID, open_search  # noqa: E402

BASE = Path(__file__).resolve().parent.parent
APERTURAS = BASE / 'data' / 'bac_aperturas.json'
OUT = BASE / 'data' / 'bac_presupuestos.json'
SCHEMA_VERSION = 1

MAX_VISITS_PER_RUN = 40
REFRESH_DAYS = 7
# Mismo criterio que el frontend (EN_APERTURA_STALE_DAYS en app.js): procesos "En Apertura" con
# apertura de hace más de un año son registros no cerrados en BAC, no licitaciones vigentes.
EN_APERTURA_STALE_DAYS = 365

# Umbrales de la señal (arbitrarios, documentados como tales, igual que CONCENTRATION_* en
# bac_catalog_collector.py):
# - OUTLIER_MIN_PEERS: con menos procesos comparables el z no es estable; se muestra el puesto
#   pero no se rotula "entre los más altos".
# - OUTLIER_Z: z robusto en log10(USD). 2,0 ≈ el 2-3% superior si fuera normal en log.
OUTLIER_MIN_PEERS = 15
OUTLIER_Z = 2.0
# Nivel "contexto" (issue #14, agente ux-specialist): no califica el monto, describe una posición
# ("1º de 17 · 26× la mediana") para destacar en "Para revisar hoy" los presupuestos que se apartan
# mucho aunque no lleguen a z >= OUTLIER_Z. Arbitrario y documentado como el resto. Grupos con menos
# de CONTEXT_MIN_PEERS procesos quedan fuera (en contrataciones menores, 5 procesos, era ruido).
CONTEXT_MAX_RANK = 3
CONTEXT_MIN_PEERS = 10
CONTEXT_MIN_RATIO = 5.0

MONTO_RE = re.compile(r'Monto y duraci[oó]n del contrato\s+Monto\s+(?P<monto>[^\n]+)', re.I)
MONEDA_RE = re.compile(r'Monto y duraci[oó]n del contrato.*?\bMoneda\s+(?P<moneda>[A-Z]{3})\b', re.I | re.S)
DURACION_RE = re.compile(r'Duraci[oó]n del contrato\s+(?P<n>\d+)\s*(?P<unidad>Mes(?:es)?|D[ií]as?|A[nñ]os?)', re.I)
PUBLICACION_RE = re.compile(r'Fecha y hora estimada de publicaci[oó]n en el portal\s+(?P<d>\d{1,2})/(?P<m>\d{1,2})/(?P<y>\d{4})', re.I)


def now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def read(p, default=None):
    return json.loads(p.read_text(encoding='utf-8')) if p.exists() else default


def write(p, obj):
    p.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding='utf-8')


def parse_monto(raw):
    """'$ 18.424.665.000,00' -> 18424665000.0 (formato es-AR)."""
    digits = re.sub(r'[^\d,]', '', raw or '')
    if not digits:
        return None
    try:
        return float(digits.replace(',', '.')) if digits.count(',') <= 1 else None
    except ValueError:
        return None


def parse_detail(text):
    """Extrae sólo los campos del presupuesto del texto de VistaPreviaPliegoCiudadano.aspx."""
    out = {'monto': None, 'moneda': None, 'duracion_meses': None, 'fecha_publicacion': None}
    m = MONTO_RE.search(text or '')
    if m:
        out['monto'] = parse_monto(m.group('monto'))
    m = MONEDA_RE.search(text or '')
    if m:
        out['moneda'] = m.group('moneda').upper()
    m = DURACION_RE.search(text or '')
    if m:
        n, unidad = int(m.group('n')), m.group('unidad').lower()
        out['duracion_meses'] = n if unidad.startswith('mes') else round(n / 30.4, 1) if unidad.startswith('d') else n * 12
    m = PUBLICACION_RE.search(text or '')
    if m:
        out['fecha_publicacion'] = f"{m.group('y')}-{int(m.group('m')):02d}-{int(m.group('d')):02d}"
    return out


def is_current(row, today):
    if row.get('estado_buscado') == 'Publicado':
        return True
    f = row.get('fecha_apertura')
    return bool(f) and (today - date.fromisoformat(f[:10])).days <= EN_APERTURA_STALE_DAYS


def needs_visit(row, cached, today):
    if not cached or cached.get('monto') is None:
        return True
    if row.get('estado_buscado') != 'Publicado':
        return False  # ya abrió: el presupuesto del pliego no cambia más
    scraped = cached.get('scraped_at')
    return not scraped or (today - date.fromisoformat(scraped[:10])).days >= REFRESH_DAYS


def open_detail_text(page, numero, estado=None):
    # El número de proceso pertenece a la "Búsqueda rápida" de BuscarAvanzado.aspx, que tiene su
    # propio botón (btnListarPliegoNumero): con el botón de la búsqueda avanzada el número se
    # ignora y la grilla trae todo el estado (verificado el 31/10/2026). Devuelve 1 resultado.
    open_search(page)
    page.fill('#ctl00_CPH1_txtNumeroProceso', numero)
    page.click('#ctl00_CPH1_btnListarPliegoNumero')
    page.wait_for_selector(GRID, timeout=20000)
    page.wait_for_timeout(1000)
    link = page.locator(f'{GRID} a:text-is("{numero}")')
    if link.count() == 0:
        return None
    with page.expect_navigation(timeout=30000):
        link.first.click()
    page.wait_for_selector('text=Monto y duración del contrato', timeout=20000)
    return page.inner_text('body')


# Si fallan varias visitas seguidas, el problema es de navegación o del sitio, no de un proceso:
# se corta la corrida en vez de seguir cargando BAC con búsquedas que van a fallar igual.
MAX_CONSECUTIVE_ERRORS = 3


def scrape(targets):
    """targets: lista de (numero_proceso, estado_buscado)."""
    from playwright.sync_api import sync_playwright
    found, errors = {}, {}
    consecutive = 0
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            page = browser.new_page(user_agent=UA)
            for numero, estado in targets:
                if consecutive >= MAX_CONSECUTIVE_ERRORS:
                    errors['_corte'] = f'{MAX_CONSECUTIVE_ERRORS} fallas seguidas: se cortó la corrida'
                    break
                try:
                    text = open_detail_text(page, numero, estado)
                    if text is None:
                        errors[numero] = 'proceso no encontrado en la búsqueda'
                        consecutive += 1
                        continue
                    found[numero] = {**parse_detail(text), 'scraped_at': now()}
                    consecutive = 0
                except Exception as e:  # una falla puntual no frena al resto
                    errors[numero] = repr(e)[:300]
                    consecutive += 1
        finally:
            browser.close()
    return found, errors


def to_usd(entry, rate_for):
    if entry.get('monto') is None:
        return None
    if entry.get('moneda') == 'USD':
        return entry['monto']
    if entry.get('moneda') == 'ARS' and rate_for:
        rate = rate_for(entry.get('fecha_publicacion') or entry.get('scraped_at'))
        return entry['monto'] / rate if rate else None
    return None


def score(items):
    """items: {numero: monto_usd}. Devuelve {numero: {z, rank, n, ratio_siguiente, ratio_mediana, flag}}."""
    vals = {k: v for k, v in items.items() if v and v > 0}
    n = len(vals)
    if n == 0:
        return {}
    logs = {k: math.log10(v) for k, v in vals.items()}
    med = statistics.median(logs.values())
    mad = statistics.median(abs(x - med) for x in logs.values())
    ordered = sorted(vals, key=vals.get, reverse=True)
    med_usd = statistics.median(vals.values())
    out = {}
    for i, k in enumerate(ordered):
        z = (logs[k] - med) / (1.4826 * mad) if mad else None
        nxt = vals[ordered[i + 1]] if i + 1 < n else None
        out[k] = {
            'rank': i + 1, 'n': n,
            'z': round(z, 2) if z is not None else None,
            'ratio_siguiente': round(vals[k] / nxt, 2) if nxt else None,
            'ratio_mediana': round(vals[k] / med_usd, 2) if med_usd else None,
            'flag': bool(z is not None and n >= OUTLIER_MIN_PEERS and z >= OUTLIER_Z),
            'destacado_contexto': bool(i + 1 <= CONTEXT_MAX_RANK and n >= CONTEXT_MIN_PEERS and med_usd
                                       and vals[k] / med_usd >= CONTEXT_MIN_RATIO),
        }
    return out


def build(aperturas, cache, rate_for, today):
    rows = [r for r in (aperturas.get('licitaciones_abiertas') or []) if is_current(r, today)]
    procesos = {}
    for r in rows:
        c = cache.get(r['numero_proceso']) or {}
        usd = to_usd(c, rate_for)
        meses = c.get('duracion_meses')
        procesos[r['numero_proceso']] = {
            'nombre_proceso': r.get('nombre_proceso'), 'organismo': r.get('organismo'),
            'tipo_proceso': r.get('tipo_proceso'), 'estado_buscado': r.get('estado_buscado'),
            'monto': c.get('monto'), 'moneda': c.get('moneda'), 'duracion_meses': meses,
            'fecha_publicacion': c.get('fecha_publicacion'), 'scraped_at': c.get('scraped_at'),
            'monto_usd': round(usd, 2) if usd else None,
            # Anualizado sólo para contratos de más de un año: escalar uno corto a 12 meses inflaría el monto.
            'monto_usd_anual': round(usd / (meses / 12), 2) if usd and meses and meses > 12 else (round(usd, 2) if usd else None),
        }
    # Se compara dentro de la misma familia de procedimiento (recomendación del agente de
    # auditoría): una licitación pública contra contrataciones menores de pocos miles de USD
    # estira la dispersión y vuelve la comparación poco defendible.
    for grupo in {peer_group(p) for p in procesos.values()}:
        miembros = {k: v['monto_usd'] for k, v in procesos.items() if peer_group(v) == grupo}
        for k, sc in score(miembros).items():
            procesos[k].update(sc, grupo_comparacion=grupo)
    return procesos


def peer_group(p):
    return 'Licitaciones públicas' if (p.get('tipo_proceso') or '').startswith('Licitación Pública') else 'Contrataciones menores, directas y otras'


def main():
    aperturas = read(APERTURAS)
    if not aperturas or not isinstance(aperturas.get('licitaciones_abiertas'), list):
        print(json.dumps({'status': 'skipped', 'reason': 'bac_aperturas.json sin licitaciones_abiertas'}))
        return
    previous = read(OUT, {}) or {}
    cache = previous.get('cache') or {}
    today = datetime.now(timezone.utc).date()
    current = [r for r in aperturas['licitaciones_abiertas'] if is_current(r, today)]
    pending = [(r['numero_proceso'], r['estado_buscado']) for r in current if needs_visit(r, cache.get(r['numero_proceso']), today)]
    to_visit = pending[:MAX_VISITS_PER_RUN]
    errors = {}
    if to_visit:
        try:
            found, errors = scrape(to_visit)
            cache.update(found)
        except Exception as e:
            errors = {'_run': repr(e)[:300]}

    pubs = sorted(c['fecha_publicacion'] for c in cache.values() if c.get('fecha_publicacion'))
    rate_for = None
    if pubs:
        rate_for = fx_bcra.make_converter(fx_bcra.load_rates(date.fromisoformat(pubs[0]), min(date.fromisoformat(pubs[-1]), today)))
    procesos = build(aperturas, cache, rate_for, today)
    con_monto = sum(1 for p in procesos.values() if p['monto_usd'])
    # Se poda la caché a los procesos vigentes para que el archivo no crezca indefinidamente.
    cache = {k: v for k, v in cache.items() if k in procesos}
    status = 'ok' if not errors and con_monto == len(procesos) else 'incompleto'
    out = {
        'schema_version': SCHEMA_VERSION, 'collected_at': now(), 'status': status,
        'source': f'{SITE}/ (detalle de cada proceso: "Monto y duración del contrato")',
        'fx_source': fx_bcra.SOURCE,
        'thresholds': {'outlier_z': OUTLIER_Z, 'outlier_min_peers': OUTLIER_MIN_PEERS,
                       'contexto_max_rank': CONTEXT_MAX_RANK, 'contexto_min_peers': CONTEXT_MIN_PEERS,
                       'contexto_min_ratio_mediana': CONTEXT_MIN_RATIO,
                       'en_apertura_stale_days': EN_APERTURA_STALE_DAYS},
        'note': ('Presupuesto oficial (monto estimado del pliego, no un precio adjudicado) de las '
                 'licitaciones de tecnología abiertas, comparado en USD contra las demás del mismo tipo '
                 '(licitaciones públicas entre sí; contrataciones menores y directas entre sí) en escala '
                 'logarítmica (z robusto con mediana/MAD). "Entre los más altos" = z >= '
                 f'{OUTLIER_Z} con al menos {OUTLIER_MIN_PEERS} procesos comparables; umbral '
                 'arbitrario. Un monto alto no indica sobreprecio: puede reflejar alcance o un '
                 'contrato plurianual. Es un disparador para revisar alcance vs. renglones, '
                 'cantidad de ofertas y precios por renglón.'),
        'procesos_vigentes': len(procesos), 'procesos_con_monto': con_monto,
        'pendientes': max(0, len(pending) - len(to_visit)),
        'errores': errors,
        'procesos': procesos,
        'cache': cache,
    }
    write(OUT, out)
    flagged = [k for k, p in procesos.items() if p.get('flag')]
    print(json.dumps({'status': status, 'vigentes': len(procesos), 'con_monto': con_monto,
                      'visitados': len(to_visit), 'errores': len(errors), 'marcados': flagged}, ensure_ascii=False))


if __name__ == '__main__':
    main()
