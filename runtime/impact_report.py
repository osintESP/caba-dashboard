#!/usr/bin/env python3
"""Informe de impacto en montos entre dos versiones de data/bac_catalog.json (para PRs).

Uso: python runtime/impact_report.py base.json head.json > impacto.md
Sale con código 2 si alguna métrica clave se mueve más que IMPACT_THRESHOLD: el workflow de CI
exige entonces que el PR explique el cambio en una sección "Impacto en montos". Existe porque
dos errores de cálculo (USD descartados, adjudicaciones contadas 3 veces) cambiaron los totales
del dashboard más de un 50% sin que nadie tuviera que justificar el movimiento.
"""
from __future__ import annotations
import json, sys

IMPACT_THRESHOLD = 0.10   # movimiento relativo en montos/conteos
PCT_POINTS_THRESHOLD = 5  # movimiento en puntos porcentuales para métricas que ya son %
TOP_N = 10


def load(path):
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def metrics(d):
    s, a = d.get('summary') or {}, d.get('audit_signals') or {}
    conc = a.get('vendor_concentration_by_organismo') or []
    return {
        'Total adjudicado (ARS)': ('monto', s.get('total_awarded_ars')),
        'Adjudicado en tecnología (ARS)': ('monto', s.get('tech_awarded_ars')),
        'Adjudicaciones contadas': ('monto', s.get('awards_counted')),
        'Procesos de tecnología (tender_index)': ('monto', len(d.get('tender_index') or {}) or None),
        '% directa/limitada en tecnología': ('pct', a.get('direct_or_limited_share_pct')),
        'Organismos con concentración alta': ('monto', sum(1 for c in conc if c.get('high_concentration')) if conc else None),
    }


def fmt(v):
    if v is None:
        return '—'
    return f'{v:,.2f}'.replace(',', 'X').replace('.', ',').replace('X', '.') if isinstance(v, float) and v < 1000 else f'{v:,.0f}'.replace(',', '.')


def report(base, head):
    mb, mh = metrics(base), metrics(head)
    flagged, lines = [], ['## Impacto en montos (data/bac_catalog.json)', '',
                          '| Métrica | Base | PR | Cambio |', '|---|---:|---:|---:|']
    for k, (kind, hv) in mh.items():
        bv = mb[k][1]
        if bv is None or hv is None:
            change, big = '—', False
        elif kind == 'pct':
            change, big = f'{hv - bv:+.1f} pp'.replace('.', ','), abs(hv - bv) > PCT_POINTS_THRESHOLD
        else:
            rel = (hv - bv) / bv if bv else (0 if hv == bv else 1)
            change, big = f'{rel:+.1%}'.replace('.', ','), abs(rel) > IMPACT_THRESHOLD
        if big:
            flagged.append(k)
        lines.append(f'| {k} | {fmt(bv)} | {fmt(hv)} | {change}{" **REVISAR**" if big else ""} |')
    ib, ih = base.get('tender_index') or {}, head.get('tender_index') or {}
    deltas = sorted(((abs((ih.get(k) or {}).get('amount_ars', 0) - (ib.get(k) or {}).get('amount_ars', 0)), k)
                     for k in set(ib) | set(ih)), reverse=True)
    deltas = [(d, k) for d, k in deltas if d > 0][:TOP_N]
    if deltas:
        lines += ['', f'**Procesos de tecnología con mayor cambio de monto (top {TOP_N}):**', '',
                  '| Proceso | Base | PR | Reconstruido por renglón |', '|---|---:|---:|---:|']
        for _, k in deltas:
            b, h = ib.get(k) or {}, ih.get(k) or {}
            lines.append(f'| {k} | {fmt(b.get("amount_ars"))} | {fmt(h.get("amount_ars"))} | {fmt(h.get("monto_reconstruido_ars"))} |')
    if not deltas and not flagged:
        lines += ['', 'Sin cambios en montos.']
    if flagged:
        lines += ['', f'**Se movieron más de lo esperado:** {", ".join(flagged)}. El PR tiene que explicar por qué en una '
                      'sección "Impacto en montos" y reconciliar los procesos que más cambiaron (ver el agente '
                      '`data-integrity-reviewer`).']
    return '\n'.join(lines), bool(flagged)


def main():
    text, flagged = report(load(sys.argv[1]), load(sys.argv[2]))
    print(text)
    sys.exit(2 if flagged else 0)


if __name__ == '__main__':
    main()
