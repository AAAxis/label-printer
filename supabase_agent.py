"""Restricted cloud catalog access and background agent telemetry (stdlib only)."""
import json
import logging
import platform
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import Request, urlopen

LOG = logging.getLogger('labels')


def queue_snapshot(path):
    """Read a consistent snapshot using a separate connection in the heartbeat thread."""
    db = sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True, timeout=3)
    db.row_factory = sqlite3.Row
    try:
        db.execute('BEGIN')
        counts = {r['status']: r['total'] for r in db.execute('SELECT status, count(*) total FROM labels GROUP BY status')}
        jobs = [dict(r) for r in db.execute('''SELECT l.id,l.email_id,l.position,l.sku,
            l.name AS product_name,l.status,substr(l.error,1,500) AS error,e.created_at
            FROM labels l JOIN emails e ON e.id=l.email_id
            ORDER BY CASE WHEN l.status IN ('pending','submitting','uncertain') THEN 0 ELSE 1 END,l.id DESC LIMIT 200''')]
        blocked = [dict(r) for r in db.execute("SELECT id,created_at,substr(error,1,500) error FROM emails WHERE status='blocked' ORDER BY created_at DESC LIMIT 50")]
        blocked_count = db.execute("SELECT count(*) FROM emails WHERE status='blocked'").fetchone()[0]
        meta = dict(db.execute("SELECT key,value FROM meta WHERE key IN ('queue_id','processing_error')"))
        return {'id':meta.get('queue_id'), 'counts':counts, 'jobs':jobs,
                'blocked_emails':blocked, 'blocked_email_count':blocked_count,
                'processing_error':meta.get('processing_error',''),
                'reported_at':datetime.now(timezone.utc).isoformat()}
    finally:
        db.close()


DEFAULT_AGENT_URL = 'https://montigate-print-desk.vercel.app/api/agent'


class CloudAgent:
    """Talks to the dashboard's /api/agent endpoint, which calls the Neon database functions."""
    def __init__(self, url, token):
        parsed = urlparse(url)
        if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError('Set PRINTER_AGENT_URL to the HTTPS dashboard agent URL')
        if len(token) < 32:
            raise ValueError('Set PRINTER_AGENT_TOKEN')
        self.url, self.token = url, token

    def rpc(self, name, payload):
        request = Request(self.url, data=json.dumps(dict(payload, rpc=name)).encode(),
            headers={'Authorization':'Bearer '+self.token,
                     'Content-Type':'application/json', 'User-Agent':'sku-label-printer/2.0'})
        try:
            with urlopen(request, timeout=20) as response:
                return json.load(response)
        except Exception:
            raise ValueError('Cloud agent request failed. Check internet, agent URL and agent token.') from None

    def products(self):
        result, start = {}, 0
        for _ in range(1000):
            rows = self.rpc('print_agent_products', {'p_start':start})
            if not isinstance(rows, list):
                raise ValueError('Invalid cloud product response; cache preserved')
            for row in rows:
                sku, name = row.get('sku'), row.get('name')
                if not isinstance(sku,str) or not sku.strip() or not isinstance(name,str) or not name.strip() or sku in result:
                    raise ValueError('Invalid or duplicate cloud product; cache preserved')
                result[sku] = name
            if len(rows)<1000:
                if not result:
                    raise ValueError('Cloud catalog has no active products; cache preserved')
                return result
            start += len(rows)
        raise ValueError('Cloud catalog pagination limit reached')

    def heartbeat(self, device, status, error=None, telemetry=None):
        return self.rpc('print_agent_heartbeat', {'p_machine':platform.node(),
            'p_device':device,'p_status':status,'p_error':error,'p_telemetry':telemetry or {}})


class Heartbeat:
    def __init__(self, agent, device, mode, probe, interval=30, snapshot=None):
        self.agent, self.device, self.mode, self.probe = agent, device, mode, probe
        self.interval = interval
        self.stop_event = threading.Event()
        self.thread = None
        self.snapshot = snapshot

    def tick(self):
        error = None
        try:
            devices = self.probe()
            ready = any(d['name']==self.device and d['connected'] for d in devices)
            status = self.mode if ready else 'printer_offline'
        except Exception:
            devices, status = [], 'printer_unreachable'
            error = 'DYMO service could not be reached'
        telemetry = {'mode':self.mode,'devices':devices,'version':'2.2-queue-reporting'}
        if self.snapshot:
            try:
                telemetry['queue'] = self.snapshot()
                queue_error = telemetry['queue'].get('processing_error')
                if queue_error and status == self.mode:
                    status, error = 'queue_paused', queue_error
            except Exception as exc:
                telemetry['queue_error'] = 'Could not read local queue'
                LOG.warning('Queue reporting failed: %s', exc)
        self.agent.heartbeat(self.device, status, error, telemetry)

    def run(self):
        while not self.stop_event.is_set():
            try:
                self.tick()
            except Exception as exc:
                LOG.warning('Heartbeat failed: %s', exc)
            self.stop_event.wait(self.interval)

    def start(self):
        self.thread = threading.Thread(target=self.run, daemon=True, name='printer-heartbeat')
        self.thread.start()

    def stop(self):
        self.stop_event.set()
