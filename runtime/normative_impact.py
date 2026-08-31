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
}
_TOPIC_RE = {tag: [re.compile(p, re.I) for p in patterns] for tag, patterns in TOPIC_RULES.items()}


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
            'url_norma': n.get('url_norma'),
        })
    flagged.sort(key=lambda x: int(x.get('id_norma') or 0), reverse=True)
    return flagged


def main():
    norms = json.loads(SRC.read_text(encoding='utf-8'))
    flagged = build_flagged_norms(norms)
    summary = {
        'total_flagged': len(flagged),
        'by_sigla': dict(Counter(f['sigla_unidad'] for f in flagged).most_common()),
        'by_topic': dict(Counter(t for f in flagged for t in f['topics']).most_common()),
    }
    payload = {
        'schema_version': 1,
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
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == '__main__':
    main()
