import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch, Mock
from types import SimpleNamespace
from datetime import datetime, timezone, timedelta
import xml.etree.ElementTree as ET

import printer as p


class FlowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.config = json.loads((p.ROOT / 'config.example.json').read_text())
        self.config['_base'] = self.root
        self.config['products']['path'] = 'products.csv'
        self.config['allowed_senders'] = ['owner@example.com']
        (self.root / 'products.csv').write_text('sku,name\n001,Soap & cream\nABC,קרם ידיים\n')
        self.queue = p.Queue(self.root / 'state.sqlite3')

    def tearDown(self):
        self.queue.db.close()
        self.temp.cleanup()

    def email(self, **changes):
        result = {'id': 'email-1', 'created_at': datetime.now(timezone.utc).isoformat(),
                  'from': 'Owner <owner@example.com>', 'to': ['printer@montigate.com'],
                  'text': '001\nABC\n001'}
        result.update(changes)
        return result

    def status(self):
        return self.queue.db.execute('SELECT status FROM emails').fetchone()[0]

    def test_skus_preserve_zeros_order_and_repeats(self):
        self.assertEqual(p.parse_skus(' 001, ABC\r\n001\n'), ['001', 'ABC', '001'])
        self.assertEqual([v['sku'] for v in p.lookup('001,ABC,001', self.config)], ['001','ABC','001'])

    def test_invalid_body_and_limit(self):
        for body in (None, '', '001\nThanks for your help', '001\n> ABC', '<script>'):
            with self.assertRaises(ValueError):
                p.parse_skus(body)
        with self.assertRaises(ValueError):
            p.parse_skus('001,ABC', 1)

    def test_quantity_suffix_repeats_labels(self):
        self.assertEqual(p.parse_skus('9780201379678*3,ABC\n001 * 2'),
                         ['9780201379678']*3 + ['ABC'] + ['001']*2)
        self.assertEqual([v['sku'] for v in p.lookup('001*2,ABC', self.config)], ['001','001','ABC'])

    def test_quantity_suffix_rejects_bad_counts_and_limit(self):
        for body in ('001*0', '001*', '001*-1', '001*2.5', '001**2', '*3', '001*99999'):
            with self.assertRaises(ValueError):
                p.parse_skus(body)
        self.assertEqual(len(p.parse_skus('001*100')), 100)
        with self.assertRaises(ValueError):
            p.parse_skus('001*100,ABC')

    def test_unknown_sku_blocks_entire_email(self):
        self.queue.ingest(self.email(text='001\nMISSING'), self.config)
        self.assertEqual(self.status(), 'blocked')
        self.assertEqual(len(self.queue.pending()), 0)

    def test_html_only_is_blocked(self):
        self.queue.ingest(self.email(text=None, html='<p>001</p>'), self.config)
        self.assertEqual(self.status(), 'blocked')

    def test_retry_blocked_uses_updated_catalog(self):
        email = self.email(text='NEW')
        self.queue.ingest(email, self.config)
        with (self.root / 'products.csv').open('a') as stream:
            stream.write('NEW,New product\n')
        self.queue.ingest(email, self.config, retry=True)
        self.assertEqual(self.status(), 'ready')
        self.assertEqual([r['name'] for r in self.queue.pending()], ['New product'])
        with self.assertRaises(ValueError):
            self.queue.ingest(email, self.config, retry=True)

    def test_retry_missing_catalog_keeps_blocked_record(self):
        email = self.email(text='NEW')
        self.queue.ingest(email, self.config)
        (self.root / 'products.csv').unlink()
        with self.assertRaises(FileNotFoundError):
            self.queue.ingest(email, self.config, retry=True)
        self.assertEqual(self.status(), 'blocked')

    def test_wrong_sender_and_recipient_are_ignored(self):
        self.queue.ingest(self.email(**{'from': 'attacker@example.com'}), self.config)
        self.queue.ingest(self.email(id='email-2', to=['careers@montigate.com']), self.config)
        self.assertEqual([r[0] for r in self.queue.db.execute('SELECT status FROM emails')], ['ignored','ignored'])
        self.assertEqual(len(self.queue.pending()), 0)

    def test_repeated_delivery_and_restart_do_not_duplicate(self):
        email = self.email()
        self.queue.ingest(email, self.config)
        self.queue.db.close()
        self.queue = p.Queue(self.root / 'state.sqlite3')
        self.queue.ingest(email, self.config)
        self.assertEqual(len(self.queue.pending()), 3)

    def test_catalog_is_snapshotted_for_queue(self):
        self.queue.ingest(self.email(), self.config)
        (self.root / 'products.csv').write_text('sku,name\n001,Changed\nABC,Changed\n')
        self.assertEqual(self.queue.pending()[0]['name'], 'Soap & cream')

    def test_duplicate_catalog_rejected(self):
        (self.root / 'products.csv').write_text('sku,name\n001,First\n001,Second\n')
        with self.assertRaises(ValueError):
            p.catalog(self.config)

    def test_json_and_readonly_sqlite_catalog(self):
        (self.root / 'products.json').write_text(json.dumps({'data': [{'code': '001', 'title':'Soap'}]}))
        self.config['products'].update(path='products.json', root_key='data', sku_field='code', name_field='title')
        self.assertEqual(p.catalog(self.config), {'001':'Soap'})
        db = sqlite3.connect(self.root / 'products.db')
        db.execute('CREATE TABLE products(code TEXT, title TEXT)')
        db.execute("INSERT INTO products VALUES ('001','Soap')")
        db.commit()
        db.close()
        self.config['products']['path'] = 'products.db'
        self.assertEqual(p.catalog(self.config), {'001':'Soap'})

    def test_preview_escapes_html_and_does_not_consume_queue(self):
        self.queue.ingest(self.email(), self.config)
        out = self.root / 'preview.html'
        p.preview(self.queue.pending(), self.config, out)
        self.assertIn('Soap &amp; cream', out.read_text())
        self.assertEqual(len(self.queue.pending()), 3)

    def fake_printer(self, fail_on=None):
        class Fake:
            calls = []
            def preflight(self):
                return 'template'
            def render_xml(self, template, label):
                return label['sku']
            def submit(self, xml):
                self.calls.append(xml)
                if len(self.calls) == fail_on:
                    raise TimeoutError('lost printer response')
        return Fake()

    def test_prints_in_order_and_does_not_resubmit(self):
        self.queue.ingest(self.email(), self.config)
        printer = self.fake_printer()
        p.process_queue(self.queue, printer)
        p.process_queue(self.queue, printer)
        self.assertEqual(printer.calls, ['001', 'ABC', '001'])
        self.assertEqual(self.status(), 'submitted')

    def test_partial_timeout_pauses_without_automatic_retry(self):
        self.queue.ingest(self.email(), self.config)
        printer = self.fake_printer(fail_on=2)
        with self.assertRaises(TimeoutError):
            p.process_queue(self.queue, printer)
        states = [r[0] for r in self.queue.db.execute('SELECT status FROM labels ORDER BY id')]
        self.assertEqual(states, ['submitted', 'uncertain', 'pending'])
        with self.assertRaises(ValueError):
            p.process_queue(self.queue, printer)
        self.assertEqual(len(printer.calls), 2)
        uncertain_id = self.queue.db.execute("SELECT id FROM labels WHERE status='uncertain'").fetchone()[0]
        self.queue.mark(uncertain_id, 'submitted')
        p.process_queue(self.queue, printer)
        self.assertEqual(printer.calls, ['001', 'ABC', '001'])

    def test_crash_during_submission_becomes_uncertain(self):
        self.queue.ingest(self.email(), self.config)
        self.queue.mark(self.queue.pending()[0]['id'], 'submitting')
        self.queue.recover()
        with self.assertRaises(ValueError):
            p.process_queue(self.queue, self.fake_printer())

    def test_printer_preflight_failure_keeps_labels_pending(self):
        self.queue.ingest(self.email(), self.config)
        printer = self.fake_printer()
        with patch.object(printer, 'preflight', side_effect=ConnectionError):
            with self.assertRaises(ConnectionError):
                p.process_queue(self.queue, printer)
        self.assertEqual(len(self.queue.pending()), 3)

    def test_xml_substitution_escapes_without_recursive_replacement(self):
        dymo = p.Dymo(self.config)
        xml = dymo.render_xml('<Label><Text>{{PRODUCT_NAME}}</Text><Text>{{SKU}}</Text></Label>',
                              {'sku':'001', 'name':'A & <B> {{SKU}} קרם'})
        self.assertEqual(ET.fromstring(xml)[0].text, 'A & <B> {{SKU}} קרם')
        self.assertEqual(ET.fromstring(xml)[1].text, '001')

    def test_dymo_payload_one_copy_and_error_response(self):
        dymo = p.Dymo(self.config)
        with patch.object(dymo, 'call', return_value='') as call:
            dymo.submit('<Label/>')
            args = call.call_args.args
            self.assertEqual(args[0], 'PrintLabel')
            self.assertIn('<Copies>1</Copies>', args[1]['printParamsXml'])
        with patch.object(dymo, 'call', return_value=False):
            with self.assertRaises(RuntimeError):
                dymo.submit('<Label/>')

    def test_dymo_rejects_remote_service(self):
        self.config['printer']['service_url'] = 'https://example.com'
        with self.assertRaises(ValueError):
            p.Dymo(self.config)

    def test_paginated_resend_reads(self):
        client = p.Resend('test-key')
        now = datetime.now(timezone.utc)
        pages = [{'data':[self.email(id='new', created_at=now.isoformat())], 'has_more':True},
                 {'data':[self.email(id='old', created_at=(now-timedelta(days=1)).isoformat())], 'has_more':False}]
        with patch.object(client, 'get', side_effect=pages) as get:
            rows = list(client.since(now-timedelta(minutes=5)))
            self.assertEqual([r['id'] for r in rows], ['new'])
            self.assertIn('after=new', get.call_args.args[0])

    def test_poll_deduplicates_and_does_not_advance_on_fetch_failure(self):
        now = datetime.now(timezone.utc)
        self.queue.set_meta('start', (now-timedelta(minutes=1)).isoformat())
        client = p.Resend('test-key')
        email = self.email()
        with patch.object(client, 'since', return_value=[email]), patch.object(client, 'email', side_effect=ConnectionError):
            with self.assertRaises(ConnectionError):
                p.poll(self.queue, client, self.config)
        self.assertIsNone(self.queue.meta('watermark'))
        with patch.object(client, 'since', return_value=[email]), patch.object(client, 'email', return_value=email) as fetch:
            p.poll(self.queue, client, self.config)
            p.poll(self.queue, client, self.config)
            self.assertEqual(fetch.call_count, 1)
        self.assertEqual(len(self.queue.pending()), 3)

    def test_first_poll_starts_now(self):
        client = p.Resend('test-key')
        before = datetime.now(timezone.utc)
        with patch.object(client, 'since', return_value=[]) as since:
            p.poll(self.queue, client, self.config)
        self.assertGreaterEqual(since.call_args.args[0], before)

    def test_exclusive_lock_rejects_second_worker(self):
        path = self.root / 'locking.sqlite3'
        with p.exclusive(path):
            with self.assertRaises(ValueError):
                with p.exclusive(path):
                    self.fail('Second worker acquired lock')
        with p.exclusive(path):
            pass

    def test_windows_byte_lock_acquired_and_released_on_error(self):
        api = SimpleNamespace(locking=Mock(), LK_NBLCK=2, LK_UNLCK=0)
        path = self.root / 'windows.sqlite3'
        with patch.object(p.os, 'name', 'nt'), patch.dict('sys.modules', {'msvcrt': api}):
            with self.assertRaises(RuntimeError):
                with p.exclusive(path):
                    raise RuntimeError('worker stopped')
        self.assertEqual([c.args[1:] for c in api.locking.call_args_list], [(2,1),(0,1)])
        self.assertEqual(path.with_suffix('.lock').read_bytes(), b'0')

    def test_windows_busy_lock_rejects_worker(self):
        api = SimpleNamespace(locking=Mock(side_effect=OSError('busy')), LK_NBLCK=2, LK_UNLCK=0)
        path = self.root / 'windows.sqlite3'
        with patch.object(p.os, 'name', 'nt'), patch.dict('sys.modules', {'msvcrt': api}):
            with self.assertRaises(ValueError):
                with p.exclusive(path):
                    self.fail('Busy Windows lock acquired')
        self.assertEqual(api.locking.call_count, 1)


if __name__ == '__main__':
    unittest.main()
