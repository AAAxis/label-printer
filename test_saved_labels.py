import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import printer
from install_saved_labels import install

ROOT = Path(__file__).resolve().parent

class SavedLabelsTest(unittest.TestCase):
    def test_original_artwork_and_details_are_unchanged(self):
        config = json.loads((ROOT / 'config.example.json').read_text())
        config['_base'] = ROOT
        config['printer']['saved_labels'] = {'9780201379678': 'saved-labels/collagen.label'}
        dymo = printer.Dymo(config)
        original = (ROOT / 'saved-labels/collagen.label').read_text(encoding='utf-8-sig')
        self.assertEqual(dymo.render_xml(None, {'sku': '9780201379678', 'name':'Other catalog name'}), original)
        with self.assertRaises(printer.MissingSavedLabel):
            dymo.render_xml(None, {'sku':'OTHER', 'name':'Other'})
        config['printer']['saved_labels']['OTHER'] = 'saved-labels/collagen.label'
        with self.assertRaisesRegex(ValueError, 'does not match'):
            dymo.render_xml(None, {'sku':'OTHER', 'name':'Other'})

    def test_embedded_label_works_without_configuration(self):
        config = json.loads((ROOT / 'config.example.json').read_text())
        config['_base'] = ROOT
        config['printer']['template_path'] = ''
        dymo = printer.Dymo(config)
        self.assertIsNone(dymo.template())
        original = (ROOT / 'saved-labels/collagen.label').read_text(encoding='utf-8-sig')
        self.assertEqual(dymo.render_xml(None, {'sku':'9780201379678', 'name':'Catalog name'}), original)
        with self.assertRaises(printer.MissingSavedLabel):
            dymo.render_xml(None, {'sku':'OTHER','name':'Other'})

    def test_dymo_500_identifies_endpoint_and_response(self):
        import io
        from urllib.error import HTTPError
        config = json.loads((ROOT / 'config.example.json').read_text())
        config['_base'] = ROOT
        dymo = printer.Dymo(config)
        error = HTTPError('https://localhost/', 500, 'Internal Server Error', {}, io.BytesIO(b'Device unavailable'))
        with patch.object(printer, 'urlopen', side_effect=error):
            with self.assertRaisesRegex(RuntimeError, 'DYMO GetPrinters failed: HTTP 500. Device unavailable'):
                dymo.printers()

    def test_install_preserves_config_credentials_and_queue(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'saved-labels').mkdir()
            (root / 'saved-labels/collagen.label').write_bytes((ROOT / 'saved-labels/collagen.label').read_bytes())
            config = {'printer':{'name':'Existing printer'},'recipient':'existing@example.com'}
            (root / 'config.json').write_text(json.dumps(config))
            (root / '.env').write_text('unchanged-secret')
            (root / 'queue.sqlite3').write_bytes(b'unchanged-queue')
            install(root)
            install(root)
            result = json.loads((root / 'config.json').read_text())
            self.assertEqual(result['recipient'], config['recipient'])
            self.assertEqual(result['printer']['name'], 'Existing printer')
            self.assertEqual(len(result['printer']['saved_labels']), 1)
            self.assertEqual((root / '.env').read_text(), 'unchanged-secret')
            self.assertEqual((root / 'queue.sqlite3').read_bytes(), b'unchanged-queue')

    def test_missing_label_does_not_block_registered_product_or_repeat_it(self):
        with tempfile.TemporaryDirectory() as folder:
            q = printer.Queue(Path(folder) / 'q.db')
            with q.db:
                q.db.execute("INSERT INTO emails(id,created_at,status) VALUES ('e','2026-09-15','ready')")
                q.db.executemany("INSERT INTO labels(email_id,position,sku,name) VALUES ('e',?,?,?)", [(0,'missing','Missing'),(1,'ok','Okay')])
            class Device:
                calls = []
                def preflight(self): return None
                def render_xml(self, template, label):
                    if label['sku'] == 'missing': raise printer.MissingSavedLabel('Missing file')
                    return '<original/>'
                def submit(self, xml): self.calls.append(xml)
            device = Device()
            printer.process_queue(q, device)
            printer.process_queue(q, device)
            self.assertEqual(device.calls, ['<original/>'])
            self.assertEqual(q.pending()[0]['error'], 'Missing file')
            q.db.close()
