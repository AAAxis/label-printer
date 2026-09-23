"""Back up every table and function in the public schema; run from the repository root.

Writes backups/<timestamp>/ with:
  data.sql       INSERT statements (apply after the schema files) to restore rows
  <table>.json   raw rows per table
  functions.sql  live definitions of public functions
"""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import psycopg
from psycopg import sql
from printer import load_key

# Parents before children so data.sql restores without foreign key errors.
ORDER = ['print_products', 'print_printers', 'print_batches', 'print_jobs', 'print_events']


def literal(value):
    if value is None:
        return 'NULL'
    if isinstance(value, bool):
        return 'true' if value else 'false'
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, (dict, list)):
        value = json.dumps(value, ensure_ascii=False)
    return "'" + str(value).replace("'", "''") + "'"


def backup():
    out = Path('backups') / datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')
    out.mkdir(parents=True)
    with psycopg.connect(load_key('POSTGRES_URL_NON_POOLING'), connect_timeout=20) as db:
        tables = [r[0] for r in db.execute(
            "select table_name from information_schema.tables where table_schema='public' and table_type='BASE TABLE'")]
        tables = [t for t in ORDER if t in tables] + sorted(t for t in tables if t not in ORDER)
        lines = ['begin;']
        for table in tables:
            cursor = db.execute(sql.SQL('select * from public.{}').format(sql.Identifier(table)))
            columns = [c.name for c in cursor.description]
            rows = cursor.fetchall()
            (out / f'{table}.json').write_text(json.dumps(
                [dict(zip(columns, r)) for r in rows], ensure_ascii=False, indent=1, default=str), encoding='utf-8')
            overriding = ' overriding system value' if table == 'print_events' else ''
            for row in rows:
                lines.append(f'insert into public.{table}({",".join(columns)}){overriding} values('
                             + ','.join(literal(v) for v in row) + ');')
            print(f'{table}: {len(rows)} rows')
        if 'print_events' in tables:
            lines.append("select setval('public.print_events_id_seq', coalesce((select max(id) from public.print_events),1));")
        lines.append('commit;')
        (out / 'data.sql').write_text('\n'.join(lines) + '\n', encoding='utf-8')
        functions = [r[0] for r in db.execute('''select pg_get_functiondef(p.oid) from pg_proc p
            join pg_namespace n on n.oid=p.pronamespace where n.nspname='public' and p.prokind='f' ''')]
        (out / 'functions.sql').write_text(';\n\n'.join(functions) + ';\n', encoding='utf-8')
        print(f'functions: {len(functions)}')
    print(f'Backup saved to {out}')


if __name__ == '__main__':
    backup()
