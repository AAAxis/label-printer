#!/usr/bin/env python3
"""Local Resend inbox -> product catalog -> durable DYMO label queue (Python 3.9+)."""
import argparse
import csv
import html
import json
import logging
import os
from pathlib import Path
import re
import sqlite3
import ssl
import time
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
from email.utils import parseaddr
from urllib.parse import urlencode, urlparse, quote
from urllib.request import Request, urlopen
from urllib.error import HTTPError
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parent
LOG = logging.getLogger("labels")
DYMO_LOCK = threading.Lock()

# Original worker-supplied label. Preserve all product-specific artwork and text.
ORIGINAL_COLLAGEN_SKU = "9780201379678"
ORIGINAL_COLLAGEN_XML = '<?xml version="1.0" encoding="utf-8"?>\n<DieCutLabel Version="8.0" Units="twips">\n\t<PaperOrientation>Landscape</PaperOrientation>\n\t<Id>Address</Id>\n\t<PaperName>30252 Address</PaperName>\n\t<DrawCommands>\n\t\t<RoundRectangle X="0" Y="0" Width="1581" Height="5040" Rx="270" Ry="270" />\n\t</DrawCommands>\n\t<ObjectInfo>\n\t\t<BarcodeObject>\n\t\t\t<Name>Barcode</Name>\n\t\t\t<ForeColor Alpha="255" Red="0" Green="0" Blue="0" />\n\t\t\t<BackColor Alpha="0" Red="255" Green="255" Blue="255" />\n\t\t\t<LinkedObjectName></LinkedObjectName>\n\t\t\t<Rotation>Rotation0</Rotation>\n\t\t\t<IsMirrored>False</IsMirrored>\n\t\t\t<IsVariable>True</IsVariable>\n\t\t\t<Text>9780201379678</Text>\n\t\t\t<Type>Code39</Type>\n\t\t\t<Size>Small</Size>\n\t\t\t<TextPosition>Bottom</TextPosition>\n\t\t\t<TextFont Family="Arial" Size="7.3125" Bold="False" Italic="False" Underline="False" Strikeout="False" />\n\t\t\t<CheckSumFont Family="Arial" Size="7.3125" Bold="False" Italic="False" Underline="False" Strikeout="False" />\n\t\t\t<TextEmbedding>None</TextEmbedding>\n\t\t\t<ECLevel>0</ECLevel>\n\t\t\t<HorizontalAlignment>Center</HorizontalAlignment>\n\t\t\t<QuietZonesPadding Left="0" Top="0" Right="0" Bottom="0" />\n\t\t</BarcodeObject>\n\t\t<Bounds X="331" Y="680.31494140625" Width="2939.8798828125" Height="765.708679199219" />\n\t</ObjectInfo>\n\t<ObjectInfo>\n\t\t<TextObject>\n\t\t\t<Name>Text</Name>\n\t\t\t<ForeColor Alpha="255" Red="0" Green="0" Blue="0" />\n\t\t\t<BackColor Alpha="0" Red="255" Green="255" Blue="255" />\n\t\t\t<LinkedObjectName></LinkedObjectName>\n\t\t\t<Rotation>Rotation0</Rotation>\n\t\t\t<IsMirrored>False</IsMirrored>\n\t\t\t<IsVariable>True</IsVariable>\n\t\t\t<HorizontalAlignment>Center</HorizontalAlignment>\n\t\t\t<VerticalAlignment>Top</VerticalAlignment>\n\t\t\t<TextFitMode>ShrinkToFit</TextFitMode>\n\t\t\t<UseFullFontHeight>True</UseFullFontHeight>\n\t\t\t<Verticalized>False</Verticalized>\n\t\t\t<StyledText>\n\t\t\t\t<Element>\n\t\t\t\t\t<String>שרוול קולגן למילוי נקניקיות 28 כשר</String>\n\t\t\t\t\t<Attributes>\n\t\t\t\t\t\t<Font Family="Tahoma" Size="9" Bold="False" Italic="False" Underline="False" Strikeout="False" />\n\t\t\t\t\t\t<ForeColor Alpha="255" Red="0" Green="0" Blue="0" />\n\t\t\t\t\t</Attributes>\n\t\t\t\t</Element>\n\t\t\t</StyledText>\n\t\t</TextObject>\n\t\t<Bounds X="331" Y="163" Width="2890.82543945313" Height="341.566925048828" />\n\t</ObjectInfo>\n\t<ObjectInfo>\n\t\t<TextObject>\n\t\t\t<Name>TEXT</Name>\n\t\t\t<ForeColor Alpha="255" Red="0" Green="0" Blue="0" />\n\t\t\t<BackColor Alpha="0" Red="255" Green="255" Blue="255" />\n\t\t\t<LinkedObjectName></LinkedObjectName>\n\t\t\t<Rotation>Rotation0</Rotation>\n\t\t\t<IsMirrored>False</IsMirrored>\n\t\t\t<IsVariable>False</IsVariable>\n\t\t\t<HorizontalAlignment>Center</HorizontalAlignment>\n\t\t\t<VerticalAlignment>Top</VerticalAlignment>\n\t\t\t<TextFitMode>ShrinkToFit</TextFitMode>\n\t\t\t<UseFullFontHeight>True</UseFullFontHeight>\n\t\t\t<Verticalized>False</Verticalized>\n\t\t\t<StyledText>\n\t\t\t\t<Element>\n\t\t\t\t\t<String>יבואן: אחים הרשברג \nושות\' כימיקליים בע"מ\n 510114580 \nארץ יצור צ\'כיה \n תוקף 05/27</String>\n\t\t\t\t\t<Attributes>\n\t\t\t\t\t\t<Font Family="Tahoma" Size="9" Bold="False" Italic="False" Underline="False" Strikeout="False" />\n\t\t\t\t\t\t<ForeColor Alpha="255" Red="0" Green="0" Blue="0" />\n\t\t\t\t\t</Attributes>\n\t\t\t\t</Element>\n\t\t\t</StyledText>\n\t\t</TextObject>\n\t\t<Bounds X="3388.12622070313" Y="193.172332763672" Width="1446.849609375" Height="1158.37536621094" />\n\t</ObjectInfo>\n\t<ObjectInfo>\n\t\t<ShapeObject>\n\t\t\t<Name>SHAPE</Name>\n\t\t\t<ForeColor Alpha="255" Red="0" Green="0" Blue="0" />\n\t\t\t<BackColor Alpha="0" Red="255" Green="255" Blue="255" />\n\t\t\t<LinkedObjectName></LinkedObjectName>\n\t\t\t<Rotation>Rotation0</Rotation>\n\t\t\t<IsMirrored>False</IsMirrored>\n\t\t\t<IsVariable>False</IsVariable>\n\t\t\t<ShapeType>VerticalLine</ShapeType>\n\t\t\t<LineWidth>15</LineWidth>\n\t\t\t<LineAlignment>Center</LineAlignment>\n\t\t\t<FillColor Alpha="0" Red="255" Green="255" Blue="255" />\n\t\t</ShapeObject>\n\t\t<Bounds X="3281.60375976563" Y="183.72119140625" Width="15" Height="1309.27880859375" />\n\t</ObjectInfo>\n</DieCutLabel>'



def timestamp(value):
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("Email timestamp must include a timezone")
    return result.astimezone(timezone.utc)


def address(value):
    return parseaddr(value)[1].lower()


def parse_skus(text, limit=100):
    """One SKU per line or comma; repeats or SKU*N intentionally produce extra labels."""
    if not isinstance(text, str) or not text.strip():
        raise ValueError("Email must have a plain-text body containing only SKUs")
    if len(text) > 32000:
        raise ValueError("Email body exceeds 32,000 characters")
    values = []
    for item in (s.strip() for s in re.split(r"[\r\n,]+", text) if s.strip()):
        match = re.fullmatch(r"([\w][\w./+\-]{0,127})(?:\s*\*\s*(\d{1,4}))?", item)
        if not match:
            raise ValueError("Invalid SKU line: %r; omit signatures and quoted replies" % item)
        copies = int(match.group(2) or 1)
        if copies < 1:
            raise ValueError("Invalid label count in %r; use SKU*N with N of at least 1" % item)
        values.extend([match.group(1)] * copies)
        if len(values) > limit:
            raise ValueError("Expected 1 to %d labels" % limit)
    if not values:
        raise ValueError("Expected 1 to %d labels" % limit)
    return values


def resolve_path(value, base=ROOT):
    p = Path(value).expanduser()
    return p if p.is_absolute() else base / p


def catalog(config):
    source = config["products"]
    path = resolve_path(source["path"], config["_base"])
    sku_field, name_field = source["sku_field"], source["name_field"]
    if path.suffix.lower() == ".csv":
        with path.open(encoding="utf-8-sig", newline="") as stream:
            rows = list(csv.DictReader(stream))
    elif path.suffix.lower() == ".json":
        rows = json.loads(path.read_text(encoding="utf-8-sig"))
        if source.get("root_key"):
            rows = rows[source["root_key"]]
        if not isinstance(rows, list):
            raise ValueError("JSON products must be an array; configure root_key if nested")
    elif path.suffix.lower() in (".db", ".sqlite", ".sqlite3"):
        ident = lambda s: '"' + s.replace('"', '""') + '"'
        db = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
        try:
            rows = [dict(zip((sku_field, name_field), row)) for row in db.execute(
                "SELECT %s, %s FROM %s" % (ident(sku_field), ident(name_field), ident(source["table"])))]
        finally:
            db.close()
    else:
        raise ValueError("Products path must be CSV, JSON, or SQLite")
    products = {}
    for row in rows:
        sku, name = row[sku_field], row[name_field]
        if not isinstance(sku, str) or not isinstance(name, str) or not sku.strip() or not name.strip():
            raise ValueError("Catalog SKU and name must be nonempty strings (preserve leading zeros)")
        sku, name = sku.strip(), name.strip()
        if sku in products:
            raise ValueError("Duplicate catalog SKU: " + sku)
        products[sku] = name
    return products


def lookup(text, config):
    skus = parse_skus(text, config["max_labels_per_email"])
    products = catalog(config)
    missing = sorted(set(skus) - products.keys())
    if missing:
        raise ValueError("Unknown SKUs; entire email held: " + ", ".join(missing))
    return [{"sku": sku, "name": products[sku]} for sku in skus]


class Resend:
    def __init__(self, key):
        if not key:
            raise ValueError("Set RESEND_API_KEY in .env or environment")
        self.key = key
        self.last_request = 0

    def get(self, path):
        # Stay below the default two requests/second limit; failed polls retry later.
        time.sleep(max(0, 0.6 - (time.monotonic() - self.last_request)))
        self.last_request = time.monotonic()
        request = Request("https://api.resend.com" + path, headers={
            "Authorization": "Bearer " + self.key, "User-Agent": "sku-label-printer/1.0"})
        with urlopen(request, timeout=30) as response:
            return json.load(response)

    def since(self, cutoff):
        after = None
        seen_cursors = set()
        while True:
            params = {"limit": 100}
            if after:
                params["after"] = after
            page = self.get("/emails/receiving?" + urlencode(params))
            rows = page["data"]
            for row in rows:
                if timestamp(row["created_at"]) >= cutoff:
                    yield row
            if not rows or not page.get("has_more") or any(timestamp(r["created_at"]) < cutoff for r in rows):
                return
            after = rows[-1]["id"]
            if after in seen_cursors:
                raise ValueError("Resend pagination cursor did not advance")
            seen_cursors.add(after)

    def email(self, email_id):
        return self.get("/emails/receiving/" + quote(email_id, safe=""))


class Queue:
    def __init__(self, path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS emails (
                id TEXT PRIMARY KEY, created_at TEXT NOT NULL, status TEXT NOT NULL,
                error TEXT NOT NULL DEFAULT '');
            CREATE TABLE IF NOT EXISTS labels (
                id INTEGER PRIMARY KEY, email_id TEXT NOT NULL, position INTEGER NOT NULL,
                sku TEXT NOT NULL, name TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending',
                error TEXT NOT NULL DEFAULT '', UNIQUE(email_id, position));
        """)
        if not self.meta('queue_id'):
            self.set_meta('queue_id', str(uuid.uuid4()))

    def meta(self, key):
        row = self.db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row[0] if row else None

    def set_meta(self, key, value):
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO meta VALUES (?,?)", (key, value))

    def ingest(self, email, config, retry=False):
        existing = self.db.execute("SELECT status FROM emails WHERE id=?", (email["id"],)).fetchone()
        if retry:
            if not existing or existing[0] != "blocked":
                raise ValueError("Email must exist and be blocked")
        elif existing:
            return
        status, error, labels = "ready", "", []
        allowed = {address(x) for x in config["allowed_senders"]}
        recipients = {address(x) for x in email.get("to", [])}
        if address(email.get("from", "")) not in allowed or address(config["recipient"]) not in recipients:
            status = "ignored"
        else:
            try:
                labels = lookup(email.get("text"), config)
            except (ValueError, KeyError, TypeError) as exc:
                status, error = "blocked", str(exc)
        with self.db:
            if retry:
                self.db.execute("DELETE FROM emails WHERE id=?", (email["id"],))
            self.db.execute("INSERT INTO emails VALUES (?,?,?,?)",
                            (email["id"], email["created_at"], status, error))
            for i, label in enumerate(labels):
                self.db.execute("INSERT INTO labels(email_id,position,sku,name) VALUES (?,?,?,?)",
                                (email["id"], i, label["sku"], label["name"]))
        LOG.info("Email %s: %s%s", email["id"], status, ": " + error if error else "")

    def pending(self):
        return self.db.execute("""SELECT labels.* FROM labels JOIN emails ON email_id=emails.id
            WHERE labels.status='pending' ORDER BY emails.created_at, emails.id, position""").fetchall()

    def mark(self, label_id, status, error=""):
        with self.db:
            self.db.execute("UPDATE labels SET status=?,error=? WHERE id=?", (status, error, label_id))
            self.db.execute("""UPDATE emails SET status='submitted' WHERE status='ready'
                AND EXISTS (SELECT 1 FROM labels WHERE email_id=emails.id)
                AND NOT EXISTS (SELECT 1 FROM labels WHERE email_id=emails.id AND status!='submitted')""")

    def recover(self):
        with self.db:
            self.db.execute("""UPDATE labels SET status='uncertain', error='Process stopped during submission'
                WHERE status='submitting'""")


class Dymo:
    """Calls the same local service endpoints as DYMO's JavaScript framework."""
    def __init__(self, config):
        self.config = config["printer"]
        self.base = config["_base"]
        parsed = urlparse(self.config["service_url"])
        if parsed.scheme != "https" or parsed.hostname not in ("localhost", "127.0.0.1", "::1"):
            raise ValueError("DYMO service must be HTTPS on loopback")
        ca = self.config.get("ca_file")
        self.context = ssl.create_default_context(cafile=str(resolve_path(ca, self.base)) if ca else None)

    def call(self, command, params=None):
        request = Request(self.config["service_url"].rstrip("/") + "/" + command,
                          data=urlencode(params).encode() if params is not None else None)
        if params is not None:
            request.add_header("Content-Type", "application/x-www-form-urlencoded")
        try:
            with DYMO_LOCK:
                with urlopen(request, context=self.context, timeout=30) as response:
                    result = response.read().decode("utf-8-sig")
        except HTTPError as exc:
            detail = re.sub(r"<[^>]+>", " ", exc.read(4096).decode("utf-8", errors="replace"))
            detail = " ".join(detail.split())[:500]
            raise RuntimeError("DYMO %s failed: HTTP %s. %s" %
                               (command, exc.code, detail or exc.reason)) from exc
        # DYMO services JSON-encode string responses.
        try:
            return json.loads(result)
        except json.JSONDecodeError:
            return result

    def printers(self):
        root = ET.fromstring(self.call("GetPrinters"))
        return [{"name": p.findtext("Name"), "connected": p.findtext("IsConnected") == "True"}
                for p in root if p.find("Name") is not None]

    def template(self):
        if self.config.get("saved_labels"):
            return None  # Original, product-specific files are selected by SKU.
        path = self.config.get("template_path")
        if not path:
            return None  # Built-in original collagen label needs no separate path.
        text = resolve_path(path, self.base).read_text(encoding="utf-8-sig")
        root = ET.fromstring(text)
        if not any(marker in text for marker in ("{{SKU}}", "{{PRODUCT_NAME}}")):
            if root.find(".//BarcodeObject/Text") is None:
                raise ValueError("Saved label has no product barcode")
            return text
        for marker in ("{{SKU}}", "{{PRODUCT_NAME}}"):
            if marker not in text:
                raise ValueError("Template missing text placeholder " + marker)
        ET.fromstring(text)
        return text

    def render_xml(self, template, label):
        if template is None and not self.config.get("saved_labels"):
            if label["sku"] != ORIGINAL_COLLAGEN_SKU:
                raise MissingSavedLabel("No saved DYMO label registered for SKU " + label["sku"])
            return ORIGINAL_COLLAGEN_XML
        if self.config.get("saved_labels"):
            path = self.config["saved_labels"].get(label["sku"])
            if not path:
                raise MissingSavedLabel("No saved DYMO label registered for SKU " + label["sku"])
            original = resolve_path(path, self.base).read_text(encoding="utf-8-sig")
            root = ET.fromstring(original)
            barcodes = [node.findtext("Text") for node in root.iter("BarcodeObject")]
            if label["sku"] not in barcodes:
                raise ValueError("Saved label barcode does not match SKU " + label["sku"])
            return original
        if "{{SKU}}" not in template and "{{PRODUCT_NAME}}" not in template:
            if label["sku"] not in [node.findtext("Text") for node in ET.fromstring(template).iter("BarcodeObject")]:
                raise MissingSavedLabel("Configured saved label is for a different SKU; requested " + label["sku"])
            return template
        # Single-pass substitution: product values cannot introduce new placeholders.
        values = {"SKU": label["sku"], "PRODUCT_NAME": label["name"]}
        result = re.sub(r"\{\{(SKU|PRODUCT_NAME)\}\}",
                        lambda m: html.escape(values[m[1]], quote=True), template)
        ET.fromstring(result)
        return result

    def preflight(self):
        if not self.config.get("name"):
            raise ValueError("Set printer.name using the printers command")
        if not any(p["name"] == self.config["name"] and p["connected"] for p in self.printers()):
            raise ValueError("Configured DYMO printer is not connected")
        return self.template()

    def submit(self, xml):
        result = self.call("PrintLabel", {
            "printerName": self.config["name"],
            "printParamsXml": "<LabelWriterPrintParams><Copies>1</Copies></LabelWriterPrintParams>",
            "labelXml": xml, "labelSetXml": ""})
        # Do not mistake a service error string or false response for acceptance.
        if result not in (None, "", True, "true"):
            raise RuntimeError("Unexpected DYMO submission response: %r" % result)


class MissingSavedLabel(ValueError):
    pass


def process_queue(queue, printer):
    if queue.db.execute("SELECT 1 FROM labels WHERE status IN ('uncertain','submitting')").fetchone():
        raise ValueError("Printing paused: resolve uncertain labels with the resolve command")
    labels = queue.pending()
    if not labels:
        return
    template = printer.preflight()
    # Validate every XML payload before any physical printing starts.
    rendered = []
    for label in labels:
        try:
            rendered.append((label, printer.render_xml(template, label)))
        except MissingSavedLabel as exc:
            queue.mark(label["id"], "pending", str(exc))
            LOG.warning("Label %s waiting: %s", label["id"], exc)
    for label, xml in rendered:
        queue.mark(label["id"], "submitting")  # Persist BEFORE crossing the printer boundary.
        try:
            printer.submit(xml)
        except Exception as exc:
            queue.mark(label["id"], "uncertain", str(exc))
            raise
        queue.mark(label["id"], "submitted")
        LOG.info("Submitted label %s, SKU %s", label["id"], label["sku"])


def sync_products(config):
    from sumit import fetch_products, fetch_webhook_products, save_catalog
    source = config['products']
    path = resolve_path(source['path'], config['_base'])
    if path.suffix.lower() not in ('.csv', '.db', '.sqlite', '.sqlite3') or source['sku_field'] != 'sku' or source['name_field'] != 'name' or source.get('table', 'products') != 'products':
        raise ValueError('Product sync requires CSV or SQLite with table=products, sku_field=sku, name_field=name')
    if config.get('live_sync', {}).get('provider') in ('supabase', 'cloud'):
        products, skipped = cloud_agent().products(), 0
    elif config.get('live_sync', {}).get('provider') == 'make_webhook':
        products, skipped = fetch_webhook_products(load_key('SUMIT_WEBHOOK_URL'), load_key('SUMIT_WEBHOOK_TOKEN'))
    else:
        products, skipped = fetch_products(load_key('SUMIT_COMPANY_ID'), load_key('SUMIT_API_KEY'))
    save_catalog(path, products)
    LOG.info('Catalog refreshed: %d usable SKUs, %d products without SKUs', len(products), skipped)
    return len(products)


def cloud_agent():
    from supabase_agent import CloudAgent, DEFAULT_AGENT_URL
    return CloudAgent(load_key('PRINTER_AGENT_URL') or DEFAULT_AGENT_URL, load_key('PRINTER_AGENT_TOKEN'))


class CatalogSync:
    """Refresh directly from SUMIT at startup, then periodically while listening."""
    def __init__(self, config, clock=time.monotonic):
        self.config = config
        self.clock = clock
        self.next_due = 0

    def refresh_if_due(self):
        settings = self.config.get('live_sync', {})
        if settings.get('enabled') and self.clock() >= self.next_due:
            sync_products(self.config)
            self.next_due = self.clock() + settings.get('interval_seconds', 3600)


def poll(queue, client, config):
    start = queue.meta("start")
    if start is None:
        start = datetime.now(timezone.utc).isoformat()
        queue.set_meta("start", start)
        LOG.info("Watching emails received from %s onwards", start)
    cutoff = max(timestamp(start), timestamp(queue.meta("watermark") or start) - timedelta(minutes=5))
    # Collect all pages before advancing; stable IDs deduplicate the overlap window.
    rows = sorted(client.since(cutoff), key=lambda e: timestamp(e["created_at"]))
    for summary in rows:
        if queue.db.execute("SELECT 1 FROM emails WHERE id=?", (summary["id"],)).fetchone():
            continue
        allowed = address(summary.get("from", "")) in {address(x) for x in config["allowed_senders"]}
        intended = address(config["recipient"]) in {address(x) for x in summary.get("to", [])}
        email = client.email(summary["id"]) if allowed and intended else summary
        if email["id"] != summary["id"]:
            raise ValueError("Retrieved email ID mismatch")
        queue.ingest(email, config)
    if rows:
        queue.set_meta("watermark", max(timestamp(queue.meta("watermark") or start),
                                        timestamp(rows[-1]["created_at"])).isoformat())


def preview(labels, config, output):
    width, height = (float(config["printer"][k]) for k in ("label_width_mm", "label_height_mm"))
    if not 5 <= width <= 300 or not 5 <= height <= 300:
        raise ValueError("Label dimensions must be between 5 and 300 mm")
    cards = "\n".join('<article><strong dir="auto">%s</strong><small>%s</small></article>' %
                       (html.escape(x["name"]), html.escape(x["sku"])) for x in labels)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("""<!doctype html><html lang="en"><meta charset="utf-8">
<title>Label queue preview</title><style>
body{font:14px Arial,sans-serif;background:#eee;padding:24px}
article{background:white;box-sizing:border-box;width:%smm;height:%smm;padding:3mm;
margin:12px 0;display:flex;flex-direction:column;justify-content:center;gap:2mm;border:1px solid #ccc}
strong{font-size:13px;overflow-wrap:anywhere}small{font:12px monospace}
</style><h1>Label queue preview</h1><p>%d labels. Content preview only; actual printing uses your DYMO template.</p>%s</html>
""" % (width, height, len(labels), cards), encoding="utf-8")
    print(output.resolve())


def load_config(path):
    config = json.loads(path.read_text())
    config["_base"] = path.resolve().parent
    if not 1 <= config["max_labels_per_email"] <= 1000:
        raise ValueError("max_labels_per_email must be 1–1000")
    if config["poll_seconds"] < 5:
        raise ValueError("poll_seconds must be at least 5")
    if config.get('live_sync', {}).get('interval_seconds', 3600) < 60:
        raise ValueError('Catalog sync interval must be at least 60 seconds')
    return config


def load_key(name="RESEND_API_KEY"):
    if not os.environ.get(name) and (ROOT / ".env").exists():
        for line in (ROOT / ".env").read_text().splitlines():
            if line.startswith(name + "="):
                return line.split("=", 1)[1].strip()
    return os.environ.get(name, "")


@contextmanager
def exclusive(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.with_suffix(".lock").open("a+b") as lock:
        if os.name == "nt":
            import msvcrt
            # Windows byte-range locks need a byte to lock, always at offset zero.
            lock.seek(0, os.SEEK_END)
            if lock.tell() == 0:
                lock.write(b"0")
                lock.flush()
            lock.seek(0)
            acquire = lambda: msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
            release = lambda: msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            acquire = lambda: fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            release = lambda: fcntl.flock(lock, fcntl.LOCK_UN)
        try:
            acquire()
        except OSError:
            raise ValueError("Another queue command is running; stop the listener first")
        try:
            yield
        finally:
            lock.seek(0)
            release()


def main():
    os.umask(0o077)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=ROOT / "config.json")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("preview", help="Preview a local SKU text file without email or printing")
    p.add_argument("file", type=Path)
    p.add_argument("--output", type=Path, default=ROOT / "previews/labels.html")
    sub.add_parser("printers", help="List printers from DYMO Connect")
    sub.add_parser("sync-products", help="Download real SUMIT products into the configured local CSV")
    p = sub.add_parser("listen", help="Poll Resend; printing requires --print")
    p.add_argument("--print", action="store_true", dest="live")
    p.add_argument("--once", action="store_true")
    sub.add_parser("status")
    p = sub.add_parser("queue-preview")
    p.add_argument("--output", type=Path, default=ROOT / "previews/queue.html")
    sub.add_parser("print-pending", help="Submit queued labels to DYMO")
    p = sub.add_parser("retry-blocked", help="Refetch a blocked email after fixing catalog/content")
    p.add_argument("email_id")
    p = sub.add_parser("resolve", help="Manually resolve an uncertain label after checking the printer")
    p.add_argument("label_id", type=int)
    p.add_argument("decision", choices=["submitted", "retry"])
    args = parser.parse_args()
    config = load_config(args.config)
    LOG.info("Printer agent 2.2-queue-reporting | Script: %s | Config: %s", Path(__file__).resolve(), args.config.resolve())
    if not config['printer'].get('saved_labels') and not config['printer'].get('template_path'):
        LOG.info("Original collagen label ready for SKU %s (embedded, unchanged)", ORIGINAL_COLLAGEN_SKU)
    if args.command == "sync-products":
        sync_products(config)
        return
    if args.command == "preview":
        preview(lookup(args.file.read_text(encoding="utf-8-sig"), config), config, args.output)
        return
    if args.command == "printers":
        print(json.dumps(Dymo(config).printers(), indent=2))
        return
    state = resolve_path(config["state_path"], config["_base"])
    with exclusive(state):
        queue = Queue(state)
        heartbeat = None
        try:
            queue.recover()
            if args.command == "status":
                for table in ("emails", "labels"):
                    print(table + ":", json.dumps([dict(r) for r in queue.db.execute("SELECT * FROM " + table)], ensure_ascii=False, indent=2))
            elif args.command == "queue-preview":
                preview(queue.pending(), config, args.output)
            elif args.command == "print-pending":
                process_queue(queue, Dymo(config))
            elif args.command == "resolve":
                row = queue.db.execute("SELECT status FROM labels WHERE id=?", (args.label_id,)).fetchone()
                if not row or row[0] != "uncertain":
                    raise ValueError("Label must exist and be uncertain")
                queue.mark(args.label_id, "pending" if args.decision == "retry" else "submitted")
            elif args.command == "retry-blocked":
                email = Resend(load_key()).email(args.email_id)
                if email["id"] != args.email_id:
                    raise ValueError("Retrieved email ID mismatch")
                synchronizer = CatalogSync(config)
                synchronizer.refresh_if_due()
                queue.ingest(email, config, retry=True)
            elif args.command == "listen":
                if not config.get("recipient") or not config.get("allowed_senders"):
                    raise ValueError("Configure recipient and allowed_senders first")
                if "your-email@example.com" in config["allowed_senders"]:
                    raise ValueError("Replace the example allowed sender before listening")
                if config.get('heartbeat', {}).get('enabled'):
                    from supabase_agent import Heartbeat, queue_snapshot
                    heartbeat = Heartbeat(cloud_agent(), config['printer']['name'],
                        'printing' if args.live else 'preview', lambda: Dymo(config).printers(),
                        snapshot=lambda: queue_snapshot(state))
                    heartbeat.start()
                synchronizer = CatalogSync(config)
                synchronizer.refresh_if_due()
                catalog(config)  # Fail on missing product data before establishing inbox start time.
                client = Resend(load_key())
                printer = Dymo(config) if args.live else None
                LOG.info("Listener mode: %s", "PRINT" if args.live else "PREVIEW ONLY")
                while True:
                    try:
                        synchronizer.refresh_if_due()
                        poll(queue, client, config)
                    except Exception as exc:
                        LOG.error("Inbox poll failed: %s", exc)
                        if args.once:
                            raise
                    try:
                        if printer:
                            process_queue(queue, printer)
                        else:
                            preview(queue.pending(), config, ROOT / "previews/queue.html")
                        queue.set_meta('processing_error', '')
                    except Exception as exc:
                        queue.set_meta('processing_error', str(exc)[:500])
                        LOG.error("Queue processing paused: %s", exc)
                        if args.once:
                            raise
                    if args.once:
                        break
                    time.sleep(config["poll_seconds"])
        finally:
            if heartbeat:
                heartbeat.stop()
            queue.db.close()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
    except Exception as exc:
        LOG.error("%s", exc)
        raise SystemExit(1)
