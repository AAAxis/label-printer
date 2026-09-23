from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock
from sumit import fetch_products, save_catalog


class SumitTests(unittest.TestCase):
    def test_pagination_and_sku_mapping(self):
        request = Mock(side_effect=[
            {'Status': 0, 'Data': {'IncomeItems': [{'SKU':'001','Name':'קרם'}], 'HasNextPage':True}},
            {'Status':'Success', 'Data': {'IncomeItems':[{'SKU':'ABC','Name':'Soap'}, {'SKU':None,'Name':'Service'}], 'HasNextPage':False}}])
        products, skipped = fetch_products('123', 'private-test', request)
        self.assertEqual(products, {'001':'קרם','ABC':'Soap'})
        self.assertEqual(skipped, 1)
        self.assertEqual(request.call_args_list[1].args[0]['Paging']['StartIndex'], 1)
        self.assertEqual(request.call_args.args[0]['Credentials']['CompanyID'], 123)

    def test_webhook_pagination(self):
        from sumit import fetch_webhook_products
        request = Mock(side_effect=[
            {'Status':0,'Data':{'IncomeItems':[{'SKU':'001','Name':'A'}],'HasNextPage':True}},
            {'Status':0,'Data':{'IncomeItems':[{'SKU':'002','Name':'B'}],'HasNextPage':False}}])
        products, _ = fetch_webhook_products('https://hook.eu1.make.com/test', 'secret', request)
        self.assertEqual(products, {'001':'A','002':'B'})
        self.assertEqual(request.call_args.args[0], {'token':'secret','startIndex':1})

    def test_webhook_rejects_wrong_destination(self):
        from sumit import fetch_webhook_products
        request = Mock()
        with self.assertRaises(ValueError):
            fetch_webhook_products('https://example.com/test', 'secret', request)
        request.assert_not_called()

    def test_missing_company_id_fails_before_network(self):
        request = Mock()
        with self.assertRaises(ValueError):
            fetch_products('', 'private-test', request)
        request.assert_not_called()

    def test_api_error_does_not_echo_private_details(self):
        with self.assertRaises(ValueError) as result:
            fetch_products('123', 'private-test', lambda _: {'Status':1, 'TechnicalErrorDetails':'private-test'})
        self.assertNotIn('private-test', str(result.exception))

    def test_ambiguous_skus_excluded_across_pages(self):
        request = Mock(side_effect=[
            {'Status':0,'Data':{'IncomeItems':[{'SKU':'001','Name':'A'}, {'SKU':'OK','Name':'Good'}],'HasNextPage':True}},
            {'Status':0,'Data':{'IncomeItems':[{'SKU':'001','Name':'B'},{'SKU':'001','Name':'C'}],'HasNextPage':False}}])
        products, _ = fetch_products('123','private-test',request)
        self.assertEqual(products, {'OK':'Good'})

    def test_broken_pagination_rejected(self):
        with self.assertRaises(ValueError):
            fetch_products('123', 'private-test', lambda _: {'Status':0,'Data':{'IncomeItems':[],'HasNextPage':True}})

    def test_sqlite_refresh_removes_obsolete_products(self):
        import sqlite3
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'products.sqlite3'
            save_catalog(path, {'OLD':'Old name', '001':'Before'})
            save_catalog(path, {'001':'After'})
            with sqlite3.connect(path) as db:
                self.assertEqual(db.execute('SELECT sku,name FROM products').fetchall(), [('001','After')])

    def test_startup_and_hourly_refresh_without_make(self):
        import printer
        from unittest.mock import patch
        now = [100]
        sync = printer.CatalogSync({'live_sync':{'enabled':True,'interval_seconds':3600}}, clock=lambda:now[0])
        with patch('printer.sync_products') as refresh:
            sync.refresh_if_due()
            now[0] = 3699
            sync.refresh_if_due()
            self.assertEqual(refresh.call_count, 1)
            now[0] = 3700
            sync.refresh_if_due()
            self.assertEqual(refresh.call_count, 2)

    def test_failed_refresh_does_not_mark_catalog_current(self):
        import printer
        from unittest.mock import patch
        sync = printer.CatalogSync({'live_sync':{'enabled':True}}, clock=lambda:100)
        with patch('printer.sync_products', side_effect=ConnectionError):
            with self.assertRaises(ConnectionError):
                sync.refresh_if_due()
        self.assertEqual(sync.next_due, 0)

    def test_csv_preserves_zeros_and_hebrew(self):
        import csv
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'products.csv'
            save_catalog(path, {'001':'קרם, ידיים'})
            with path.open(encoding='utf-8-sig', newline='') as stream:
                self.assertEqual(list(csv.DictReader(stream)), [{'sku':'001','name':'קרם, ידיים'}])


if __name__ == '__main__':
    unittest.main()
