#!/usr/bin/env python3
"""Cotización oficial USD→ARS del BCRA, con caché en data/fx_usd_ars.json.

Existe porque bac_anual.csv trae adjudicaciones en USD (565 filas en 2026, casi todas de la
Agencia de Sistemas de Información) y bac_catalog_collector.py las descartaba en silencio:
quedaban fuera más dólares de tecnología que todo lo adjudicado en pesos. Para poder sumarlas
y compararlas (concentración, fraccionamiento, rankings) se convierten al tipo de cambio del
día de la adjudicación, conservando el monto original en USD.

Fuente: API pública de Estadísticas Cambiarias del BCRA (sin credenciales). Sólo se piden al
BCRA las fechas que faltan en la caché; si la API no responde se usa lo cacheado.
"""
from __future__ import annotations
import bisect, json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
import requests

BASE = Path(__file__).resolve().parent.parent
FX_FILE = BASE / 'data' / 'fx_usd_ars.json'
API = 'https://api.bcra.gob.ar/estadisticascambiarias/v1.0/Cotizaciones/USD'
SOURCE = 'BCRA — API Estadísticas Cambiarias, cotización oficial del dólar estadounidense (USD)'
CHUNK_DAYS = 300  # la API devuelve hasta 1000 filas por consulta; 300 días corridos sobra
# Si la cotización más cercana anterior a la fecha está a más de esto (fines de semana y
# feriados largos entran holgados), no se convierte: mejor excluir y avisar que inventar.
MAX_GAP_DAYS = 10


def _read():
    try:
        return json.loads(FX_FILE.read_text(encoding='utf-8'))
    except (FileNotFoundError, json.JSONDecodeError):
        return {'source': SOURCE, 'rates': {}}


def fetch_range(d_from: date, d_to: date, session=requests) -> dict:
    rates = {}
    start = d_from
    while start <= d_to:
        end = min(start + timedelta(days=CHUNK_DAYS), d_to)
        r = session.get(API, params={'fechadesde': start.isoformat(), 'fechahasta': end.isoformat(), 'limit': 1000}, timeout=30)
        r.raise_for_status()
        for row in r.json().get('results') or []:
            for det in row.get('detalle') or []:
                if det.get('codigoMoneda') == 'USD' and det.get('tipoCotizacion'):
                    rates[row['fecha'][:10]] = float(det['tipoCotizacion'])
        start = end + timedelta(days=1)
    return rates


def load_rates(d_from: date, d_to: date, fetch=fetch_range) -> dict:
    """Devuelve {fecha ISO: ARS por USD} cubriendo [d_from - MAX_GAP_DAYS, d_to], actualizando la caché."""
    cache = _read()
    rates = dict(cache.get('rates') or {})
    want_from = d_from - timedelta(days=MAX_GAP_DAYS)
    # Se guarda el rango YA CONSULTADO, no se infiere de las fechas con cotización: fines de
    # semana y feriados no tienen cotización y harían re-consultar el BCRA en cada corrida.
    cov_from, cov_to = cache.get('covered_from'), cache.get('covered_to')
    ranges = []
    if not (cov_from and cov_to):
        ranges.append((want_from, d_to))
    else:
        first, last = date.fromisoformat(cov_from), date.fromisoformat(cov_to)
        if want_from < first:
            ranges.append((want_from, first - timedelta(days=1)))
        if d_to > last:
            ranges.append((last + timedelta(days=1), d_to))
    done = []
    for a, b in ranges:
        try:
            rates.update(fetch(a, b))
        except (requests.RequestException, ValueError, KeyError):
            continue  # se sigue con lo cacheado; lo que no se pueda convertir queda excluido y reportado
        done.append((a, b))
    if done:
        # Un rango que termina hoy puede no tener todavía la cotización del día: no se marca
        # como cubierto el día de hoy, para volver a pedirlo en la próxima corrida.
        today = datetime.now(timezone.utc).date()
        lo = min([a for a, _ in done] + ([date.fromisoformat(cov_from)] if cov_from else []))
        hi = max([min(b, today - timedelta(days=1)) for _, b in done] + ([date.fromisoformat(cov_to)] if cov_to else []))
        FX_FILE.write_text(json.dumps({'source': SOURCE, 'api': API,
                                       'updated_at': datetime.now(timezone.utc).isoformat(timespec='seconds'),
                                       'covered_from': lo.isoformat(), 'covered_to': hi.isoformat(),
                                       'rates': dict(sorted(rates.items()))}, ensure_ascii=False, indent=1), encoding='utf-8')
    return rates


def make_converter(rates: dict):
    """Función fecha -> cotización del mismo día o del último día hábil previo (o None)."""
    days = sorted(rates)
    def rate_for(when):
        if not when or not days:
            return None
        d = str(when)[:10]
        i = bisect.bisect_right(days, d) - 1
        if i < 0:
            return None
        if (date.fromisoformat(d) - date.fromisoformat(days[i])).days > MAX_GAP_DAYS:
            return None
        return rates[days[i]]
    return rate_for
