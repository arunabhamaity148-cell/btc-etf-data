"""
BTC ETF Flow Scraper
====================
Fetches daily BTC ETF flow data from Farside Investors.
Runs via GitHub Actions (not blocked there).
Saves to data/latest.json for KAVACH-09 bot to read via raw.githubusercontent.com
"""
import json
import os
import sys
from datetime import datetime, timezone

import requests
from bs4 import BeautifulSoup

FARSIDE_URL = "https://farside.co.uk/bitcoin-etf-flow-all-data/"
LATEST_PATH = "data/latest.json"
HISTORY_PATH = "data/history.json"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "en-US,en;q=0.9",
}

KNOWN_TICKERS = {
    "IBIT", "FBTC", "BITB", "ARKB", "BTCO",
    "EZBC", "BRRR", "HODL", "DEFI", "GBTC", "BTC",
}


def parse_flow(raw: str) -> float:
    """Parse Farside cell: '123.4' → 123.4 | '(45.2)' → -45.2 | '-' → 0.0"""
    raw = raw.strip().replace(",", "")
    if not raw or raw in ("-", "—", "n/a", "N/A", "*", "–"):
        return 0.0
    negative = raw.startswith("(") and raw.endswith(")")
    raw = raw.strip("()")
    try:
        val = float(raw)
        return -val if negative else val
    except ValueError:
        return 0.0


def parse_date(raw: str) -> str | None:
    from datetime import datetime
    for fmt in ("%d %b %Y", "%b %d, %Y", "%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(raw.strip(), fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None


def scrape() -> dict:
    print(f"Fetching {FARSIDE_URL}...")
    resp = requests.get(FARSIDE_URL, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    print(f"HTTP {resp.status_code} — {len(resp.text)} bytes")

    soup = BeautifulSoup(resp.text, "lxml")
    tables = soup.find_all("table")

    # Pick table with most ETF ticker columns
    best_table = None
    best_score = 0
    for tbl in tables:
        ths = [th.get_text(strip=True).upper() for th in tbl.find_all("th")]
        score = sum(1 for h in ths if h in KNOWN_TICKERS)
        if score > best_score:
            best_score = score
            best_table = tbl

    if not best_table or best_score < 2:
        raise ValueError(f"No ETF table found (best score={best_score})")

    # Parse headers
    header_row = best_table.find("tr")
    headers = [th.get_text(strip=True).upper() for th in header_row.find_all(["th", "td"])]
    print(f"Headers: {headers}")

    # Parse all rows
    rows = []
    for tr in best_table.find_all("tr")[1:]:
        cells = [td.get_text(strip=True) for td in tr.find_all(["td", "th"])]
        if len(cells) < 3:
            continue
        row = {headers[i]: cells[i] for i in range(min(len(headers), len(cells)))}
        rows.append(row)

    print(f"Parsed {len(rows)} rows")

    # Find most recent row with data
    latest_row = None
    for row in reversed(rows):
        date_val = row.get("DATE", "")
        if parse_date(date_val):
            by_issuer = {t: parse_flow(row.get(t, "")) for t in KNOWN_TICKERS}
            if any(v != 0 for v in by_issuer.values()):
                latest_row = row
                break

    if not latest_row:
        latest_row = rows[-1] if rows else {}

    # Extract data
    by_issuer = {}
    for ticker in KNOWN_TICKERS:
        val = parse_flow(latest_row.get(ticker, ""))
        if val != 0:
            by_issuer[ticker] = round(val * 1_000_000, 0)  # $M → USD

    total_raw = latest_row.get("TOTAL", "")
    net_flow = parse_flow(total_raw) * 1_000_000 if total_raw else sum(by_issuer.values())

    # 7-day cumulative
    recent_7 = rows[-7:] if len(rows) >= 7 else rows
    cum_7d = 0.0
    for row in recent_7:
        t = row.get("TOTAL", "")
        cum_7d += parse_flow(t) * 1_000_000 if t else 0.0

    date_str = parse_date(latest_row.get("DATE", "")) or datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # Bias
    if net_flow >= 200_000_000:
        bias = "BULLISH"
    elif net_flow <= -200_000_000:
        bias = "BEARISH"
    else:
        bias = "NEUTRAL"

    result = {
        "date":           date_str,
        "net_flow":       net_flow,
        "by_issuer":      by_issuer,
        "cumulative_7d":  cum_7d,
        "bias":           bias,
        "fetched_at":     datetime.now(timezone.utc).isoformat(),
        "source":         "Farside Investors via GitHub Actions",
    }

    print(f"Latest: {date_str} | net={net_flow/1e6:+.1f}M | bias={bias}")
    return result


def save(data: dict) -> None:
    os.makedirs("data", exist_ok=True)

    # Save latest
    with open(LATEST_PATH, "w") as f:
        json.dump(data, f, indent=2)
    print(f"Saved {LATEST_PATH}")

    # Append to history (keep 365 days)
    history = []
    if os.path.exists(HISTORY_PATH):
        with open(HISTORY_PATH) as f:
            history = json.load(f)
    # Avoid duplicate dates
    history = [h for h in history if h.get("date") != data["date"]]
    history.insert(0, data)
    history = history[:365]
    with open(HISTORY_PATH, "w") as f:
        json.dump(history, f, indent=2)
    print(f"Saved {HISTORY_PATH} ({len(history)} entries)")


if __name__ == "__main__":
    try:
        data = scrape()
        save(data)
        print("✅ Done")
    except Exception as e:
        print(f"❌ Error: {e}")
        sys.exit(1)
