"""Sync real SUMIT catalog to the central (Neon) database; run from the repository root."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import psycopg
from printer import load_key
from sumit import fetch_webhook_products, fetch_webhook_suppliers


def sync():
    url, token = load_key('SUMIT_WEBHOOK_URL'), load_key('SUMIT_WEBHOOK_TOKEN')
    products, skipped = fetch_webhook_products(url, token)
    try:
        suppliers = fetch_webhook_suppliers(url, token)
    except ValueError as error:
        suppliers = None  # keep the suppliers already stored; names still refresh
        print(f'Warning: {error}; existing suppliers kept')
    rows = [(sku, name, (suppliers or {}).get(sku), suppliers is not None) for sku, name in products.items()]
    with psycopg.connect(load_key('POSTGRES_URL_NON_POOLING'), connect_timeout=20) as db:
        with db.cursor() as cursor:
            cursor.executemany('''insert into public.print_products(sku,name,supplier,active,synced_at)
                values(%s,%s,%s,true,now()) on conflict(sku) do update
                set name=excluded.name,active=true,synced_at=now(),
                supplier=case when %s then excluded.supplier else print_products.supplier end''', rows)
        db.execute('update public.print_products set active=false where not (sku=any(%s))', (list(products),))
    with_supplier = sum(1 for sku in products if (suppliers or {}).get(sku))
    print(f'Synced {len(products)} usable products to the database ({with_supplier} with supplier); skipped {skipped} without SKUs')

if __name__ == '__main__':
    sync()
