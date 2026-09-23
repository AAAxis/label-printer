# Windows setup — live SUMIT sync

This version reads SUMIT through the working Make webhook. No SUMIT or Make API key is needed on Windows. The included private configuration contains the webhook token and Resend key; keep this package private.

1. Extract the private ZIP into a permanent folder, such as C:\LabelPrinter. Python 3.13 and DYMO Connect must be installed. Preserve the old `state` folder when updating to prevent duplicate printing.
2. Double-click `sync-products.cmd`. Success shows `Catalog refreshed: 609 usable SKUs` (count can change). The database is `products/sumit-products.sqlite3`.
3. For an email test, double-click `run-preview.cmd`. After the listener starts, send SKU lines from dima@holylabs.net to printer@montigate.com. Open `previews/queue.html` after about 30 seconds. This mode queues labels without printing.
4. Stop preview with Ctrl+C. In PowerShell opened in this folder, run `.\run-python.cmd printer.py printers`. Put the exact printer name into config.json.
5. Save a DYMO XML template containing {{SKU}} and {{PRODUCT_NAME}} text, and set printer.template_path in config.json. Set your actual label dimensions; existing dimensions are examples.
6. Double-click `run-printer.cmd`. It prints pending labels, including preview-mode labels, and future emails. Keep the PC awake and the window open.

Products sync on startup and every hour while the listener runs. To refresh manually, use sync-products.cmd. A failed sync preserves the previous database and pauses new email intake until syncing succeeds. Existing queued labels retain their original product names.

Make sends SKU emails after WhatsApp in the order scenario. The separate product webhook only reads SUMIT. Both dima@holylabs.net and sku-bot@montigate.com are allowed senders.

The webhook-to-SQLite sync was verified live. Physical DYMO printing still needs the template and a Windows hardware test.
