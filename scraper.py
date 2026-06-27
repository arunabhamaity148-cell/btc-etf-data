"""
BTC ETF Flow Scraper
====================
Tries multiple free sources for BTC ETF data.
Sources tried in order:
  1. farside.co.uk/btc/ (shorter URL, sometimes different Cloudflare rules)
  2. Alternative Farside URL patterns
  3. Writes placeholder if all fail (bot shows "unavailable")
"""
import json
import os
import sys
import time
from datetime import datetime, timezone

import requests
from bs4 import BeautifulSoup

KNOWN_TICKERS = {
    "IBIT", "FBTC", "BITB", "ARKB", "BTCO",
    "EZBC", "BRRR", "HODL", "DEFI", "GBTC", "BTC",
}

# Try multiple URLs and User-Agents
TARGETS = [
    ("https://farside.co.uk/btc/", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"),
    ("https://farside.co.uk/bitcoin-etf-flow-all-data/", "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15"),
    ("https://www.farside.co.uk/btc/", "Mozilla/5.0 (X11; Linux x86_64; rv:125.0) Gecko/20100101 Firefox/125.0"),
]


def parse_flow(raw: str) -> float:
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
    for fmt in ("%d %b %Y", "%b %d, %Y", "%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(raw.strip(), fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None


def try_fetch(url: str, ua: str) -> str | None:
    headers = {
        "User-Agent": ua,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate",
        "Cache-Control": "no-cache",
        "Referer": "https://www.google.com/",
    }
    try:
        print(f"Trying: {url}")
        r = requests.get(url, headers=headers, timeout=30)
        print(f"  → HTTP {r.status_code} ({len(r.text)} bytes)")
        if r.status_code == 200 and len(r.text) > 1000:
            return r.text
        return None
    except Exception as e:
        print(f"  → Error: {e}")
        return None


def parse_html(html: str, source_url: str) -> dict | None:
    soup = BeautifulSoup(html, "lxml")
    tables = soup.find_all("table")
    if not tables:
        print("  → No tables found")
        return None

    best_table = None
    best_score = 0
    for tbl in tables:
        ths = [th.get_text(strip=True).upper() for th in tbl.find_all("th")]
        score = sum(1 for h in ths if h in KNOWN_TICKERS)
        if score > best_score:
            best_score = score
            best_table = tbl

    if not best_table or best_score < 2:
        print(f"  → No ETF table (best score={best_score})")
        return None

    header_row = best_table.find("tr")
    headers = [th.get_text(strip=True).upper() for th in header_row.find_all(["th", "td"])]

    rows = []
    for tr in best_table.find_all("tr")[1:]:
        cells = [td.get_text(strip=True) for td in tr.find_all(["td", "th"])]
        if len(cells) < 3:
            continue
        rows.append({headers[i]: cells[i] for i in range(min(len(headers), len(cells)))})

    if not rows:
        return None

    latest_row = None
    for row in reversed(rows):
        date_val = row.get("DATE", "")
        if parse_date(date_val):
            by_issuer = {t: parse_flow(row.get(t, "")) for t in KNOWN_TICKERS}
            if any(v != 0 for v in by_issuer.values()):
                latest_row = row
                break

    if not latest_row:
        latest_row = rows[-1]

    by_issuer = {}
    for ticker in KNOWN_TICKERS:
        val = parse_flow(latest_row.get(ticker, ""))
        if val != 0:
            by_issuer[ticker] = round(val * 1_000_000, 0)

    total_raw = latest_row.get("TOTAL", "")
    net_flow = parse_flow(total_raw) * 1_000_000 if total_raw else sum(by_issuer.values())

    recent_7 = rows[-7:] if len(rows) >= 7 else rows
    cum_7d = sum(parse_flow(r.get("TOTAL", "")) * 1_000_000 for r in recent_7)

    date_str = parse_date(latest_row.get("DATE", "")) or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    bias = "BULLISH" if net_flow >= 200e6 else "BEARISH" if net_flow <= -200e6 else "NEUTRAL"

    print(f"  → Parsed: {date_str} net={net_flow/1e6:+.1f}M bias={bias}")
    return {
        "date":          date_str,
        "net_flow":      net_flow,
        "by_issuer":     by_issuer,
        "cumulative_7d": cum_7d,
        "bias":          bias,
        "fetched_at":    datetime.now(timezone.utc).isoformat(),
        "source":        f"Farside Investors ({source_url}) via GitHub Actions",
    }


def save(data: dict) -> None:
    os.makedirs("data", exist_ok=True)
    with open("data/latest.json", "w") as f:
        json.dump(data, f, indent=2)

    history = []
    if os.path.exists("data/history.json"):
        with open("data/history.json") as f:
            history = json.load(f)
    history = [h for h in history if h.get("date") != data["date"]]
    history.insert(0, data)
    with open("data/history.json", "w") as f:
        json.dump(history[:365], f, indent=2)
    print(f"Saved: {data['date']} net={data['net_flow']/1e6:+.1f}M")


def main():
    # Try all sources
    for url, ua in TARGETS:
        html = try_fetch(url, ua)
        if html:
            result = parse_html(html, url)
            if result:
                save(result)
                print("✅ Done")
                return
        time.sleep(2)

    # All failed — write placeholder so bot knows
    print("❌ All sources failed (Cloudflare blocking GitHub Actions IPs)")
    placeholder = {
        "date":          datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "net_flow":      0.0,
        "by_issuer":     {},
        "cumulative_7d": 0.0,
        "bias":          "NEUTRAL",
        "fetched_at":    datetime.now(timezone.utc).isoformat(),
        "source":        "unavailable — Cloudflare blocked all fetch attempts",
        "error":         True,
    }
    save(placeholder)
    # Exit 0 so workflow doesn't fail — placeholder is still committed
    sys.exit(0)


if __name__ == "__main__":
    main()
