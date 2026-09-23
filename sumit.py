"""Read SUMIT products and atomically refresh the local SKU/name catalog."""
import csv
import json
import os
from pathlib import Path
import tempfile
import logging
import sqlite3
from urllib.request import Request, urlopen
from urllib.parse import urlparse


def fetch_webhook_products(url, token, request_page=None):
    parsed = urlparse(url)
    if parsed.scheme != 'https' or parsed.hostname != 'hook.eu1.make.com' or parsed.username or parsed.password:
        raise ValueError('SUMIT_WEBHOOK_URL must be an HTTPS Make EU1 webhook')
    if not token:
        raise ValueError('Set SUMIT_WEBHOOK_TOKEN')

    def send_page(payload):
        request = Request(url, data=json.dumps(payload).encode(),
                          headers={'Content-Type': 'application/json'})
        try:
            with urlopen(request, timeout=60) as response:
                result = json.load(response)
            if not isinstance(result, dict):
                raise ValueError('Invalid response')
            return result
        except Exception:
            raise ValueError('SUMIT webhook failed; existing catalog preserved. Check Make execution history and webhook settings.') from None

    transport = request_page or send_page
    return collect_products(lambda start, size: transport({'token': token, 'startIndex': start}))


def fetch_products(company_id, api_key, request_page=None):
    if not str(company_id).isdigit() or int(company_id) <= 0:
        raise ValueError('Set SUMIT_COMPANY_ID to the numeric company ID from SUMIT')
    if not api_key:
        raise ValueError('Set SUMIT_API_KEY to the private SUMIT API key')

    def send_page(payload):
        request = Request('https://api.sumit.co.il/accounting/incomeitems/list/',
                          data=json.dumps(payload).encode(),
                          headers={'Content-Type': 'application/json', 'User-Agent': 'sku-label-printer/1.0'})
        with urlopen(request, timeout=30) as response:
            return json.load(response)

    # Kept separate from the real transport for offline pagination/error tests.
    transport = request_page or send_page
    return collect_products(lambda start, size: transport({
        'Credentials': {'CompanyID': int(company_id), 'APIKey': api_key},
        'Paging': {'StartIndex': start, 'PageSize': size}}))


def collect_products(request_page):
    products, skipped, start, ambiguous = {}, 0, 0, set()
    for _ in range(1000):
        response = request_page(start, 1000)
        if response.get('Status') not in (0, 'Success', 'Success (0)'):
            raise ValueError('SUMIT rejected the product request. Check Company ID, private key, and read permissions.')
        data = response.get('Data')
        if not isinstance(data, dict) or not isinstance(data.get('IncomeItems'), list):
            raise ValueError('SUMIT returned an invalid product response; existing catalog preserved')
        items = data['IncomeItems']
        for item in items:
            sku, name = item.get('SKU'), item.get('Name')
            if not sku:
                skipped += 1
                continue
            if not isinstance(sku, str) or not isinstance(name, str) or not sku.strip() or not name.strip():
                raise ValueError('SUMIT product has an invalid SKU or name; existing catalog preserved')
            sku = sku.strip()
            if sku in ambiguous:
                continue
            if sku in products:
                del products[sku]
                ambiguous.add(sku)
                continue
            products[sku] = name.strip()
        if not data.get('HasNextPage'):
            if not products:
                raise ValueError('SUMIT returned no products with SKUs; existing catalog preserved')
            if ambiguous:
                logging.getLogger('labels').warning('Excluded %d ambiguous SKUs: %s', len(ambiguous), ', '.join(sorted(ambiguous)))
            return products, skipped
        if not items:
            raise ValueError('SUMIT pagination did not advance; existing catalog preserved')
        start += len(items)
    raise ValueError('SUMIT pagination limit reached; existing catalog preserved')


def save_catalog(path, products):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.suffix.lower() in ('.db', '.sqlite', '.sqlite3'):
        db = sqlite3.connect(path)
        try:
            with db:
                db.execute('CREATE TABLE IF NOT EXISTS products (sku TEXT PRIMARY KEY, name TEXT NOT NULL)')
                db.execute('DELETE FROM products')
                db.executemany('INSERT INTO products VALUES (?,?)', products.items())
        finally:
            db.close()
        return
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8-sig', newline='',
                                         dir=path.parent, suffix='.tmp', delete=False) as stream:
            temporary = Path(stream.name)
            writer = csv.writer(stream)
            writer.writerow(['sku', 'name'])
            writer.writerows(products.items())
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
    finally:
        if temporary and temporary.exists():
            temporary.unlink()
