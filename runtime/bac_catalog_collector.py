import json
from datetime import datetime, timezone
from pathlib import Path

import requests

BASE = Path(__file__).resolve().parent.parent
OUT = BASE / 'data' / 'bac_catalog.json'
API = 'https://datosabiertos-apis.buenosaires.gob.ar/BA_Root/ba_data/api/action/package_show'
PACKAGE = 'buenos-aires-compras'


def main():
    # BA Data exposes CKAN package_show with the dataset identifier as a
    # path parameter. The former ?id= form is rejected by the current API.
    url = f'{API}/{PACKAGE}'
    r = requests.get(url, timeout=30)
    r.raise_for_status()
    p = r.json()

    if not p.get('success'):
        raise RuntimeError('BA Data package_show success=false')

    result = p.get('result') or {}
    resources = []
    for x in result.get('resources') or []:
        resources.append({
            'id': x.get('id'),
            'name': x.get('name'),
            'format': x.get('format'),
            'url': x.get('url'),
            'last_modified': x.get('last_modified') or x.get('modified'),
            'mimetype': x.get('mimetype'),
        })

    out = {
        'schema_version': 1,
        'collected_at': datetime.now(timezone.utc).isoformat(timespec='seconds'),
        'source': {
            'owner': 'Gobierno de la Ciudad Autónoma de Buenos Aires',
            'api': 'https://datosabiertos-apis.buenosaires.gob.ar/BA_Root/ba_data',
            'dataset': PACKAGE,
            'standard': 'OCDS',
        },
        'dataset': {
            'id': result.get('id'),
            'name': result.get('name'),
            'title': result.get('title'),
            'metadata_modified': result.get('metadata_modified'),
            'license_id': result.get('license_id'),
            'organization': (result.get('organization') or {}).get('title'),
        },
        'resources': resources,
    }
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'dataset': out['dataset'], 'resources': len(resources)}, ensure_ascii=False))


if __name__ == '__main__':
    main()
