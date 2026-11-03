"""Bank side: fetch transactions from an Open Bank Project SANDBOX and convert them to ReconDesk's CSV.

Two steps, so the network part is separate and optional:

    python -m scripts.obp_bank fetch   --out data/private/obp_raw.json
    python -m scripts.obp_bank convert --raw data/private/obp_raw.json --out data/private/bank_obp.csv

`fetch` reads credentials from environment variables only. Use sandbox test accounts, never a real bank login:
    OBP_BASE_URL      default https://apisandbox.openbankproject.com
    OBP_API_VERSION   default v5.1.0
    OBP_USERNAME, OBP_PASSWORD, OBP_CONSUMER_KEY   (DirectLogin)
    OBP_BANK_ID, OBP_ACCOUNT_ID
    OBP_VIEW_ID       default owner

`convert` never rounds or silently drops: rows that do not fit the ReconDesk contract go to a rejects CSV.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from reconcile.ingest import ALLOWED_CURRENCY, MAX_ROWS, REQUIRED_COLUMNS
from reconcile.money import format_minor, parse_amount_minor

DEFAULT_BASE_URL = "https://apisandbox.openbankproject.com"
DEFAULT_API_VERSION = "v5.1.0"
PAGE_SIZE = 500
REFERENCE_SOURCES = ("description", "id", "none")


# --- fetch -------------------------------------------------------------------

class ApiError(SystemExit):
    """An HTTP error from the API, shown with the server's own message (which never echoes credentials)."""


def _request(url: str, headers: dict, data: bytes | None = None) -> dict:
    method = "POST" if data is not None else "GET"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")[:500]
        path = urllib.parse.urlsplit(url).path
        raise ApiError(f"HTTP {exc.code} from {path}: {body}") from None


def login(base_url: str, username: str, password: str, consumer_key: str) -> str:
    """DirectLogin: POST /my/logins/direct, returns a token. The token is never written to disk.

    Values are quoted so commas or spaces in a password do not break the header.
    """
    for name, value in (("username", username), ("password", password), ("consumer_key", consumer_key)):
        if '"' in value:
            raise SystemExit(f"{name} contains a double quote, which DirectLogin headers cannot carry")
    header = f'DirectLogin username="{username}",password="{password}",consumer_key="{consumer_key}"'
    body = _request(f"{base_url}/my/logins/direct",
                    {"Authorization": header, "Content-Type": "application/json"}, data=b"")
    return body["token"]


def fetch_transactions(base_url: str, api_version: str, token: str, bank_id: str, account_id: str,
                       view_id: str) -> list[dict]:
    quote = urllib.parse.quote
    path = (f"{base_url}/obp/{api_version}/banks/{quote(bank_id, safe='')}/accounts/"
            f"{quote(account_id, safe='')}/{quote(view_id, safe='')}/transactions")
    headers = {"Authorization": f"DirectLogin token={token}", "Content-Type": "application/json"}
    seen: set[str] = set()
    collected: list[dict] = []
    offset = 0
    while len(collected) < MAX_ROWS:
        page = _request(f"{path}?limit={PAGE_SIZE}&offset={offset}", headers).get("transactions", [])
        fresh = [t for t in page if t.get("id") not in seen]
        if not fresh:  # empty page, or the server ignored offset and repeated a page
            break
        collected += fresh
        seen.update(t.get("id") for t in fresh)
        offset += len(page)
        if len(page) < PAGE_SIZE:
            break
    if len(collected) > MAX_ROWS:
        print(f"warning: {len(collected)} transactions fetched; keeping the first {MAX_ROWS} (ReconDesk's limit)")
    return collected[:MAX_ROWS]


def _base_url() -> str:
    base_url = _env("OBP_BASE_URL", DEFAULT_BASE_URL).rstrip("/")
    if not base_url.startswith("https://"):
        raise SystemExit("OBP_BASE_URL must use https:// so the DirectLogin password is not sent in cleartext")
    return base_url


def _env(name: str, default: str | None = None) -> str:
    value = os.environ.get(name, default)
    if not value:
        raise SystemExit(f"environment variable {name} is required")
    return value


def list_accounts(base_url: str, api_version: str, token: str) -> list[dict]:
    """GET /my/accounts: the logged-in user's accounts at all banks, with their view IDs."""
    headers = {"Authorization": f"DirectLogin token={token}", "Content-Type": "application/json"}
    return _request(f"{base_url}/obp/{api_version}/my/accounts", headers).get("accounts", [])


def cmd_accounts(args) -> None:
    base_url = _base_url()
    api_version = _env("OBP_API_VERSION", DEFAULT_API_VERSION)
    token = login(base_url, _env("OBP_USERNAME"), _env("OBP_PASSWORD"), _env("OBP_CONSUMER_KEY"))
    accounts = list_accounts(base_url, api_version, token)
    if not accounts:
        print("login worked, but this user has no accounts yet")
        return
    for account in accounts:
        views = ", ".join(v.get("id", "") for v in account.get("views_available") or [])
        print(f"bank_id={account.get('bank_id')}  account_id={account.get('id')}  views={views}")


def cmd_fetch(args) -> None:
    base_url = _base_url()
    api_version = _env("OBP_API_VERSION", DEFAULT_API_VERSION)
    token = login(base_url, _env("OBP_USERNAME"), _env("OBP_PASSWORD"), _env("OBP_CONSUMER_KEY"))
    bank_id, account_id, view_id = _env("OBP_BANK_ID"), _env("OBP_ACCOUNT_ID"), _env("OBP_VIEW_ID", "owner")
    transactions = fetch_transactions(base_url, api_version, token, bank_id, account_id, view_id)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({
        "fetched_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "base_url": base_url, "api_version": api_version,
        "bank_id": bank_id, "account_id": account_id, "view_id": view_id,
        "transactions": transactions,
    }, indent=2), encoding="utf-8")
    print(f"fetched {len(transactions)} transactions to {out}")


# --- convert -----------------------------------------------------------------

def _date(details: dict) -> dt.date:
    text = details.get("completed") or details.get("posted")
    if not text:
        raise ValueError("missing completed/posted date")
    stamp = dt.datetime.fromisoformat(str(text).replace("Z", "+00:00"))
    if stamp.tzinfo is not None:
        stamp = stamp.astimezone(dt.timezone.utc)  # dates are taken in UTC
    return stamp.date()


def convert(transactions: list[dict], reference_from: str = "description",
            flip_sign: bool = False) -> tuple[list[dict], list[dict]]:
    """Map OBP transactions to ReconDesk rows. Returns (rows, rejects); every input lands in exactly one."""
    if reference_from not in REFERENCE_SOURCES:
        raise ValueError(f"reference_from must be one of {REFERENCE_SOURCES}")
    rows, rejects, seen = [], [], set()
    for position, txn in enumerate(transactions, start=1):
        tid = str(txn.get("id") or "").strip()
        details = txn.get("details") or {}
        value = details.get("value") or {}
        try:
            if not tid:
                raise ValueError("missing id")
            if tid in seen:
                raise ValueError("duplicate id")
            currency = str(value.get("currency") or "").strip().upper()
            if currency != ALLOWED_CURRENCY:
                raise ValueError(f"currency {currency or '(missing)'} is not {ALLOWED_CURRENCY}")
            cents = parse_amount_minor(str(value.get("amount", "")))  # no rounding: >2 places is rejected
            day = _date(details)
        except ValueError as exc:
            rejects.append({"position": position, "id": tid, "reason": str(exc)})
            continue
        seen.add(tid)
        description = str(details.get("description") or "").strip()
        reference = {"description": description, "id": tid, "none": ""}[reference_from]
        rows.append({
            "transaction_id": tid, "date": day.isoformat(), "amount": format_minor(-cents if flip_sign else cents),
            "currency": currency, "reference": reference, "description": description,
        })
    return rows, rejects


def write_csv(path: Path, rows: list[dict], columns) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(columns), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def cmd_convert(args) -> None:
    raw = json.loads(Path(args.raw).read_text(encoding="utf-8"))
    transactions = raw["transactions"] if isinstance(raw, dict) else raw
    rows, rejects = convert(transactions, args.reference_from, args.flip_sign)
    out = Path(args.out)
    write_csv(out, rows, REQUIRED_COLUMNS)
    rejects_path = out.with_suffix(".rejects.csv")
    write_csv(rejects_path, rejects, ("position", "id", "reason"))
    print(f"{len(transactions)} transactions in: {len(rows)} written to {out}, {len(rejects)} rejected to {rejects_path}")


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("accounts", help="log in and list your bank_id / account_id / views").set_defaults(func=cmd_accounts)
    fetch = sub.add_parser("fetch", help="download sandbox transactions to a raw JSON file")
    fetch.add_argument("--out", default="data/private/obp_raw.json")
    fetch.set_defaults(func=cmd_fetch)
    conv = sub.add_parser("convert", help="convert raw JSON to a ReconDesk bank CSV")
    conv.add_argument("--raw", default="data/private/obp_raw.json")
    conv.add_argument("--out", default="data/private/bank_obp.csv")
    conv.add_argument("--reference-from", choices=REFERENCE_SOURCES, default="description")
    conv.add_argument("--flip-sign", action="store_true", help="use if the API reports outflows as positive")
    conv.set_defaults(func=cmd_convert)
    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main(sys.argv[1:])
