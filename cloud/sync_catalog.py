"""Sync real SUMIT catalog to the central (Neon) database; run from the repository root."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import psycopg
from printer import load_key
from sumit import fetch_webhook_products


def sync():
    products, skipped = fetch_webhook_products(load_key('SUMIT_WEBHOOK_URL'), load_key('SUMIT_WEBHOOK_TOKEN'))
    with psycopg.connect(load_key('POSTGRES_URL_NON_POOLING'), connect_timeout=20) as db:
        with db.cursor() as cursor:
            cursor.executemany('''insert into public.print_products(sku,name,active,synced_at)
                values(%s,%s,true,now()) on conflict(sku) do update
                set name=excluded.name,active=true,synced_at=now()''', products.items())
        db.execute('update public.print_products set active=false where not (sku=any(%s))', (list(products),))
    print(f'Synced {len(products)} usable products to the database; skipped {skipped} without SKUs')

if __name__ == '__main__':
    sync()
