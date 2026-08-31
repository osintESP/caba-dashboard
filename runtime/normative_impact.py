import json
import re
from collections import Counter
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
SRC = BASE / 'data' / 'norms.json'
OUT = BASE / 'data' / 'normative_impact.json'

# Unidades de sistemas/transformación digital de la Ciudad, identificadas por la sigla
# embebida en el número de acto (ej. "Disposición N.º 24/DGISIS/26" -> sigla "DGISIS"),
# no por el campo 'organismo' (que para todas estas normas viene genérico como "Jefatura
# de Gabinete de Ministros", sin distinguir la unidad real que la dictó).
#
# Confirmado contra fuentes oficiales (buenosaires.gob.ar, LinkedIn de personal de la
# agencia): SECITD = Secretaría de Innovación y Transformación Digital; ASINF = Agencia de
# Sistemas de Información (ASI), que depende de SECITD; DGISIS = Dirección General de
# Infraestructura, dentro de ASI/SECITD.
# DGIASINF / DGTALINF / DGGEDOC: no se confirmó el desarrollo exacto de la sigla contra una
# fuente oficial, pero SÍ se confirmó (búsqueda de Boletín Oficial real) que son direcciones
# hermanas dictando actos del mismo tipo (aprobación de licitaciones/licencias de software,
# redeterminaciones de precio de contratos de tecnología) — se incluyen con ese respaldo,
# no como una sigla adivinada sin base.
SYSTEMS_SIGLAS = {'SECITD', 'ASINF', 'DGISIS', 'DGIASINF', 'DGTALINF', 'DGGEDOC'}

JGM_ORGANISMO_MARKER = 'jefatura de gabinete de ministros'

SIGLA_RE = re.compile(r'N[°ºo]\s*[\d./]+/([A-ZÑ]{2,15})/\d{2}\b')

# Mismo espíritu que RULES en procurement_intelligence.py (keywords sobre texto libre),
# pero acá 'nombre'+'sumario' son el TÍTULO/resumen que publica el Boletín, no el cuerpo de
# la resolución (considerandos/articulado) -que no se recolecta-, así que la cobertura de
# estos tags es baja a propósito: la mayoría de las normas de estas unidades no va a traer
# ningún tag, y eso es esperable, no una falla de la regla.
TOPIC_RULES = {
    'gestion_documental_gde': [r'\bGDE\b', r'gesti[oó]n documental electr[oó]nica',
                                r'\bTAD\b', r'tr[aá]mites?\s+a\s+distancia'],
    'firma_digital': [r'firma\s+digital', r'firma\s+electr[oó]nica'],
    'ciberseguridad': [r'ciberseguridad', r'seguridad\s+inform[aá]tica', r'protecci[oó]n\s+de\s+datos'],
    'interoperabilidad': [r'interoperabilidad'],
    'plataforma_o_sistema': [r'plataforma', r'sistemas?\s+inform[aá]tic\w*', r'aplicativo'],
    'redeterminacion_precios': [r'actualizaci[oó]n\s+de\s+precios', r'redeterminaci[oó]n\s+de\s+precios'],
}
_TOPIC_RE = {tag: [re.compile(p, re.I) for p in patterns] for tag, patterns in TOPIC_RULES.items()}

# Las disposiciones de actualización de precios de Orden de Compra declaran el ordinal en
# texto plano (ej. "Aprueba la Novena Actualización de Precios de la Orden de Compra N°
# 8056-0106-OCA25" -> 9na actualización de esa OC) — confirmado contra datos reales de
# normative_impact.json. Esto es lo que hace viable esta señal SIN reconstruir un historial
# de montos (que no existe en ningún dataset disponible): el ordinal ya es la cuenta.
# Sólo cubre 1ª-10ª (formas simples); ordinales compuestos (ej. "Décimo Primera") no se
# parsean -son raros y, si aparecen, ya superaron ampliamente el umbral de todos modos-.
ORDINALS = {
    'primera': 1, 'segunda': 2, 'tercera': 3, 'cuarta': 4, 'quinta': 5,
    'sexta': 6, 'septima': 7, 'séptima': 7, 'octava': 8, 'novena': 9,
    'decima': 10, 'décima': 10,
}
PRICE_UPDATE_RE = re.compile(
    # \bla\s+ ancla al determinante justo antes del ordinal: sin esto, un ordinal compuesto
    # como "Décimo Primera" matchea de casualidad como "Primera" (el regex encuentra la
    # primera posición donde SÍ hay "(palabra) Actualización...", saltándose "Décimo").
    r'\bla\s+(\w+)\s+Actualizaci[oó]n\s+de\s+Precios\s+de\s+la\s+Orden\s+de\s+Compra\s+N[°ºo]?\s*([\w.-]+)',
    re.I,
)

# Arbitrario y documentado como tal (mismo criterio que CONCENTRATION_HIGH_PCT en
# bac_catalog_collector.py): 1-2 redeterminaciones en un contrato largo es un mecanismo
# contractual normal (ajuste por inflación/emergencia económica), no una señal de nada.
# 3 o más es el disparador de revisión manual, no una acusación de irregularidad.
REDETERMINATION_MIN_ORDINAL = 3


def extract_price_redetermination(sumario):
    m = PRICE_UPDATE_RE.search(sumario or '')
    if not m:
        return None
    ordinal = ORDINALS.get(m.group(1).lower())
    if ordinal is None:
        return None
    return {'orden_de_compra': m.group(2), 'ordinal': ordinal, 'ordinal_word': m.group(1)}


def extract_sigla(nombre):
    m = SIGLA_RE.search(nombre or '')
    return m.group(1).upper() if m else None


def is_jgm(organismo):
    return JGM_ORGANISMO_MARKER in (organismo or '').lower()


def classify_topics(nombre, sumario):
    text = f"{nombre or ''} {sumario or ''}"
    return [tag for tag, patterns in _TOPIC_RE.items() if any(p.search(text) for p in patterns)]


def build_flagged_norms(norms):
    flagged = []
    for n in norms:
        if not is_jgm(n.get('organismo')):
            continue
        sigla = extract_sigla(n.get('nombre'))
        if sigla not in SYSTEMS_SIGLAS:
            continue
        flagged.append({
            'id_norma': n.get('id_norma'), 'numero_boletin': n.get('numero_boletin'),
            'fecha_publicacion': n.get('fecha_publicacion'), 'nombre': n.get('nombre'),
            'sumario': n.get('sumario'), 'tipo': n.get('tipo'), 'sigla_unidad': sigla,
            'topics': classify_topics(n.get('nombre'), n.get('sumario')),
            'price_redetermination': extract_price_redetermination(n.get('sumario')),
            'url_norma': n.get('url_norma'),
        })
    flagged.sort(key=lambda x: int(x.get('id_norma') or 0), reverse=True)
    return flagged


def build_price_redetermination_flags(flagged):
    flags = [
        {'id_norma': f['id_norma'], 'numero_boletin': f['numero_boletin'],
         'fecha_publicacion': f['fecha_publicacion'], 'nombre': f['nombre'],
         'sigla_unidad': f['sigla_unidad'], 'url_norma': f['url_norma'], **f['price_redetermination']}
        for f in flagged
        if f.get('price_redetermination') and f['price_redetermination']['ordinal'] >= REDETERMINATION_MIN_ORDINAL
    ]
    flags.sort(key=lambda x: x['ordinal'], reverse=True)
    return flags


def main():
    norms = json.loads(SRC.read_text(encoding='utf-8'))
    flagged = build_flagged_norms(norms)
    price_flags = build_price_redetermination_flags(flagged)
    summary = {
        'total_flagged': len(flagged),
        'by_sigla': dict(Counter(f['sigla_unidad'] for f in flagged).most_common()),
        'by_topic': dict(Counter(t for f in flagged for t in f['topics']).most_common()),
        'price_redeterminations_flagged': len(price_flags),
    }
    payload = {
        'schema_version': 2,
        'source': 'norms.json',
        'note': ('Normas de Jefatura de Gabinete de Ministros publicadas por unidades de '
                 'sistemas o transformación digital de la Ciudad (Secretaría de Innovación '
                 'y Transformación Digital / Agencia de Sistemas de Información y '
                 'direcciones asociadas), identificadas por la sigla embebida en el número '
                 'de acto. Es una señal de que la norma PUEDE afectar circuitos digitales '
                 '(GDE, TAD, ciberseguridad, interoperabilidad, sistemas) — no confirma el '
                 'impacto real ni identifica qué procedimiento puntual modifica, porque sólo '
                 'se analiza título y sumario del Boletín, no el cuerpo completo de la '
                 'resolución (considerandos/articulado). Es un disparador para revisión '
                 'manual, no una conclusión automática.'),
        'siglas_monitoreadas': sorted(SYSTEMS_SIGLAS),
        'summary': summary,
        'norms': flagged,
        'price_redeterminations': {
            'flags': price_flags,
            'note': ('Disposiciones de unidades de sistemas que aprueban una actualización de '
                     f'precios de una Orden de Compra con ordinal ≥ {REDETERMINATION_MIN_ORDINAL} '
                     '(ej. "Novena Actualización de Precios" = la 9ª redeterminación de esa '
                     'orden) — el propio texto del Boletín declara el número, no hace falta '
                     'reconstruir el historial de montos, que no existe en ningún dataset '
                     'disponible. Redeterminar precios es un mecanismo contractual legítimo '
                     '(ajuste por inflación, emergencia económica, etc.), así que esto no '
                     'implica irregularidad — pero muchas redeterminaciones seguidas de la '
                     'MISMA orden de compra es un patrón atípico que amerita revisar el '
                     'contrato subyacente.'),
        },
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == '__main__':
    main()
