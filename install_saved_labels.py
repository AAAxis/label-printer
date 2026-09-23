"""Register existing labels without changing credentials, queue or label artwork."""
import json
import os
from pathlib import Path
import shutil
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parent

def install(root=ROOT):
    path = root / 'config.json'
    if not path.is_file():
        raise ValueError('Extract this update into the existing printer folder containing config.json.')
    config = json.loads(path.read_text(encoding='utf-8-sig'))
    mapping = {}
    for label in sorted((root / 'saved-labels').glob('*.label')):
        xml = ET.fromstring(label.read_text(encoding='utf-8-sig'))
        codes = {n.findtext('Text') for n in xml.iter('BarcodeObject')}
        codes.discard(None)
        if len(codes) != 1 or not next(iter(codes)):
            raise ValueError('Expected one product barcode in ' + label.name)
        sku = next(iter(codes))
        if sku in mapping:
            raise ValueError('Duplicate saved label for SKU ' + sku)
        mapping[sku] = label.relative_to(root).as_posix()
    if not mapping:
        raise ValueError('No .label files found in saved-labels.')
    config['printer'].setdefault('saved_labels', {}).update(mapping)
    config['printer']['label_width_mm'] = 89
    config['printer']['label_height_mm'] = 28
    shutil.copy2(path, root / 'config.before-saved-labels.json')
    temp = root / 'config.saved-labels.tmp'
    temp.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding='utf-8')
    os.replace(temp, path)
    print('Registered existing labels for SKUs: ' + ', '.join(mapping))
    print('Saved label artwork, credentials and queue preserved.')
    print('Double-click run-printer.cmd to print pending items with a registered label.')
    print('Products without a saved label stay pending.')

if __name__ == '__main__':
    try:
        install()
    except Exception as exc:
        print('SETUP ERROR:', exc)
        raise SystemExit(1)
