# Email SKU label printer

A Python 3.9+ script for the **Windows PC connected to your DYMO LabelWriter 450**, with macOS support for development. See [START-WINDOWS.md](START-WINDOWS.md) for the copy-and-run guide.

**Email → Resend inbox → local product lookup → persistent queue → one label per SKU.**

## Current setup

- Receiving address: `printer@montigate.com`. Resend API access and receiving capability for `montigate.com` were checked successfully.
- The Resend key is stored in `.env` with owner-only permissions and excluded from Git.
- Allowed sender: `dima@holylabs.net`.
- Actual SUMIT catalog location on Windows, installed printer name, label size, and DYMO template still need configuration.
- The example catalog contains fictional demo products. It is not your SUMIT data.
- No listener is installed or running automatically. No labels have been physically printed.

## Try the content preview

No Python packages need installing; the script uses Python's standard library. On Windows use `py -3` in place of `python3` below.

```sh
python3 printer.py --config config.example.json preview examples/email.txt
open previews/labels.html
python3 -m unittest -v
```

The HTML checks product names, SKUs, count, and ordering. It is **not** a pixel-accurate DYMO print preview; actual output uses the saved DYMO template. The example 54 × 25 mm dimensions are placeholders until your label size is known. Hebrew text is preserved, with automatic direction in the preview; physical layout and font support must be checked on the printer.

## Configure the product source and sender

If `config.json` does not exist, copy `config.example.json` to `config.json`. Relative paths resolve from the configuration file's directory.

1. Set `allowed_senders` to the exact email addresses that may submit labels.
2. Set `products.path` to your local SUMIT export or database.
3. Map `sku_field` and `name_field` to the actual column names.

Supported sources:

- UTF-8 CSV with a header row.
- JSON array of product objects; set `root_key` for an array inside an object.
- SQLite database opened read-only; set `table` to the product table name.

SKUs and names must be strings. SKU matching is exact and case-sensitive; leading zeros are preserved. Duplicate catalog SKUs cause an error rather than choosing an arbitrary product. The catalog is loaded for each new email; queued labels retain the product name as it was when accepted.

### Download products directly from SUMIT

Set `SUMIT_COMPANY_ID` (numeric company ID) and `SUMIT_API_KEY` (private key) in `.env`. The public key is not used by this read endpoint. Run:

```powershell
py -3 printer.py sync-products
```

This reads `/accounting/incomeitems/list/`, follows pagination, and atomically saves the real `SKU` and `Name` fields to `products.path`. Use a CSV path with `sku_field=sku` and `name_field=name`. Items without SKUs are counted and skipped; duplicate SKUs or failed responses preserve the previous catalog. Repeat the command to refresh the local data. The current config uses the authenticated Make webhook and refreshes SQLite on startup and hourly. Direct SUMIT credentials remain an optional alternative. See START-WINDOWS.md for current setup.

The sender allowlist filters the email's From address; it is not cryptographic sender authentication. Resend API calls verify TLS. No public webhook, port forwarding, or local HTTP server is required.

## Set up DYMO

1. Install DYMO Connect for your operating system, connect the LabelWriter 450, and confirm a manual label prints.
2. Start DYMO's local web service and run `python3 printer.py printers`.
3. Copy the exact returned name into `printer.name`.
4. Design a label in DYMO's software using your actual label stock. Include separate text objects with the literal text `{{PRODUCT_NAME}}` and `{{SKU}}`. Enable shrink-to-fit or choose a suitable font for long names. Save the XML label file and set `printer.template_path` to its path. Keep each placeholder in a single text run in the saved XML.
5. Set preview dimensions to the actual label width and height in millimeters.

The adapter calls the local endpoints used by [DYMO's official JavaScript framework](https://github.com/dymosoftware/dymo-connect-framework): `GetPrinters` and `PrintLabel`. The default service URL is `https://localhost:41951/DYMO/DLS/Printing`; DYMO may choose another port between 41951 and 41960. Set the installed service URL if necessary.

TLS verification remains enabled. If Python does not trust DYMO's local certificate, export the appropriate DYMO CA certificate to a PEM file and set `printer.ca_file` to it. This local-service adapter and your template require a real printer test before unattended use.

## Email format

Send a new plain-text email to `printer@montigate.com`, containing only the SKU list:

```text
001234
ABC-200
HE-300
```

Commas also work. Each occurrence prints one label, so repeating a SKU requests another copy. `SKU*N` prints N copies (for example `ABC-200*5`); N must be a whole number of at least 1, and the email's total label count must stay within the limit. The subject is ignored. Omit email signatures, greetings, and quoted replies. HTML-only messages are held for review. Default limit: 100 labels per email.

If any SKU is unknown, **the entire email is held before any of its labels print**. Other valid emails can proceed. The script does not send acknowledgement or error emails; inspect logs and `status`.

## Listen and print

First run in preview mode:

```sh
python3 printer.py listen
```

This checks Resend every 30 seconds and updates `previews/queue.html`. The first run starts watching from the current time; old inbox messages are not automatically printed. Send your test email after the listener reports its start time. Stop it with Ctrl+C before using other queue commands.

```sh
python3 printer.py status
python3 printer.py queue-preview
open previews/queue.html
```

Once the catalog, queue, and template are correct, this command **physically submits all pending labels**, including those queued in preview mode:

```sh
python3 printer.py print-pending
```

For continuous automatic printing:

```sh
python3 printer.py listen --print
```

The Windows PC must remain awake, online, and connected to the printer. `listen --once` performs one polling cycle. No background service or scheduled task is installed by this project.

## Recovery

- Completed submissions are saved in SQLite so repeated API results and restarts do not print them again. All queue-changing commands take an exclusive process lock.
- Resend API failures retry on the next poll. Pagination and a five-minute overlap avoid losing messages across normal polling boundaries; no network request sends product data back to Resend.
- Unknown SKU or malformed email: fix the source and run `python3 printer.py retry-blocked EMAIL_ID`. For an invalid email body, send a corrected new email instead.
- Printer unavailable before submission: labels stay pending.
- Timeout or crash during submission: that label becomes `uncertain`, and all further printing pauses. Inspect the physical output and printer queue before resolving:

```sh
# The printer accepted it; do not send again:
python3 printer.py resolve LABEL_ID submitted

# It was not accepted; put it back in the queue:
python3 printer.py resolve LABEL_ID retry
```

`submitted` means the DYMO service accepted the request, not that a label physically emerged. Exactly-once physical printing cannot be guaranteed across a lost printer response; uncertain submissions intentionally require inspection. Preserve `state/queue.sqlite3` and its SQLite files; deleting state loses deduplication history and pending work.

## Resend documentation

- [Receiving setup](https://resend.com/docs/dashboard/receiving/introduction)
- [List received emails](https://resend.com/docs/api-reference/emails/list-received-emails)
- [Retrieve received email content](https://resend.com/docs/api-reference/emails/retrieve-received-email)
- [Cursor pagination](https://resend.com/docs/api-reference/pagination)

Tests cover input validation, catalog matching, unknown products, recipient/sender filtering, pagination, polling retries, duplicate delivery, restart recovery, partial print failures, and XML escaping. API credentials and the physical printer are not used by tests.
