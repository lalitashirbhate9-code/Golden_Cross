"""Build the static NSE golden-cross report from an approved public endpoint.

The provider is deliberately isolated in ``fetch_chart``.  A licensed provider
can replace that function without changing the screen, indicators, or report
format.  This script never logs in to Screener or a brokerage account.
"""

from __future__ import annotations

import csv
import json
import math
import os
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
INPUT = ROOT / "screener_latest_results.csv"
OUTPUT = ROOT / "data" / "nse_golden_cross.json"
CONFIG = ROOT / "config.json"
HISTORY = ROOT / "data" / "history.json"
REPORT = ROOT / "data" / "report.json"
HISTORY_CSV = ROOT / "data" / "stock_history.csv"
HTML_REPORT_DIR = ROOT / "reports"
PERIOD_SECONDS = 86400 * 730

SYMBOL_BY_NAME = {
    "N R Agarwal Inds": "NRAIL",
    "Nexus Select": "NXST",
    "Nitiraj Engineer": "NITIRAJ",
}
STARTER_CANDIDATES = [
    {"name": "N R Agarwal Inds", "symbol": "NRAIL", "source": {"Sector": "Paper & Packaging"}},
    {"name": "Nexus Select", "symbol": "NXST", "source": {"Sector": "Real Estate"}},
    {"name": "Nitiraj Engineer", "symbol": "NITIRAJ", "source": {"Sector": "Electrical Equipment"}},
]
DEFAULT_CONFIG = {
    "screen": {"minMarketCapCrore": 100, "volumeRatioMin": 1.5},
    "indicators": {"smaFast": 50, "smaSlow": 200, "volumeAverageDays": 20,
                   "rsiPeriod": 14, "macdFast": 12, "macdSlow": 26, "macdSignal": 9},
    "history": {"maxRuns": 365},
    "ranking": {"strongMinimum": 80, "moderateMinimum": 60},
}


def number(value, default=None):
    try:
        result = float(str(value or "").replace(",", "").replace("%", "").replace("₹", "").strip())
        return result if math.isfinite(result) else default
    except (TypeError, ValueError):
        return default


def load_config():
    settings = json.loads(json.dumps(DEFAULT_CONFIG))
    if CONFIG.exists():
        with CONFIG.open(encoding="utf-8") as handle:
            supplied = json.load(handle)
        for section, values in supplied.items():
            if isinstance(values, dict):
                settings.setdefault(section, {}).update(values)
    return settings


def normalized_key(value):
    return "".join(character.lower() for character in str(value) if character.isalnum())


def source_value(row, *names):
    wanted = {normalized_key(name) for name in names}
    for key, value in row.items():
        if normalized_key(key) in wanted:
            return (value or "").strip()
    return ""


def candidates():
    rows = []
    if INPUT.exists():
        with INPUT.open(encoding="utf-8-sig", newline="") as handle:
            for row in csv.DictReader(handle):
                name = source_value(row, "Stock", "Name", "Stock Name", "Company")
                symbol = source_value(row, "NSE Symbol", "NSECode", "Symbol", "Code")
                symbol = symbol or SYMBOL_BY_NAME.get(name, "")
                if name and symbol:
                    rows.append({"name": name, "symbol": symbol.upper(), "source": row})
    else:
        rows.extend(STARTER_CANDIDATES)
    if not rows:
        raise RuntimeError("No candidates with an NSE symbol were found.")
    return rows


def fetch_chart(symbol):
    """Fetch daily candles; this is the only market-data provider boundary."""
    now = int(time.time())
    url = (
        "https://query1.finance.yahoo.com/v8/finance/chart/"
        f"{symbol}.NS?period1={now - PERIOD_SECONDS}&period2={now}"
        "&interval=1d&events=history&includeAdjustedClose=true"
    )
    request = Request(url, headers={"User-Agent": "nse-golden-cross-dashboard/1.0"})
    for attempt in range(3):
        try:
            with urlopen(request, timeout=25) as response:
                payload = json.load(response)
            break
        except HTTPError as error:
            if error.code != 429 or attempt == 2:
                raise
            time.sleep(2 ** attempt)
    result = payload["chart"]["result"][0]
    quote = result["indicators"]["quote"][0]
    closes = [float(value) for value in quote["close"] if value is not None]
    volumes = [float(value) if value is not None else 0 for value in quote["volume"]]
    if len(closes) < 200 or len(volumes) < len(closes):
        raise ValueError("fewer than 200 complete daily candles returned")
    return result, closes, volumes


def average(values, size):
    return sum(values[-size:]) / size


def ema(values, period):
    if len(values) < period:
        return None
    result = sum(values[:period]) / period
    multiplier = 2 / (period + 1)
    for value in values[period:]:
        result = (value - result) * multiplier + result
    return result


def rsi(values, period=14):
    if len(values) <= period:
        return None
    gains, losses = [], []
    for before, after in zip(values[-period - 1:-1], values[-period:]):
        change = after - before
        gains.append(max(change, 0))
        losses.append(max(-change, 0))
    average_gain, average_loss = sum(gains) / period, sum(losses) / period
    if average_loss == 0:
        return 100.0 if average_gain else 50.0
    return 100 - (100 / (1 + average_gain / average_loss))


def macd(values, fast=12, slow=26, signal=9):
    if len(values) < slow + signal:
        return None, None, None
    # Recalculate the EMA series so the signal line uses the same smoothing.
    fast_ema, slow_ema, line = None, None, []
    for index in range(slow - 1, len(values)):
        fast_ema = ema(values[:index + 1], fast)
        slow_ema = ema(values[:index + 1], slow)
        line.append(fast_ema - slow_ema)
    signal_value = ema(line, signal)
    return line[-1], signal_value, line[-1] - signal_value if signal_value is not None else None


def signal_for(price_change, volume_change, rules=None):
    rules = rules or DEFAULT_CONFIG.get("signalRules", {})
    key = ("priceUp" if price_change > 0 else "priceDown") + ("VolumeUp" if volume_change > 1 else "VolumeDown")
    fallback = {
        "priceUpVolumeUp": ("Bullish", "BUY", "Price and volume are rising together."),
        "priceUpVolumeDown": ("Caution", "SELL", "Caution - weak hands buying."),
        "priceDownVolumeUp": ("Bearish", "SELL", "Price is falling while volume rises."),
        "priceDownVolumeDown": ("Caution", "SELL", "Caution - weak hands selling."),
    }[key]
    configured = rules.get(key, {})
    return {
        "label": configured.get("label", fallback[0]),
        "recommendation": configured.get("recommendation", fallback[1]),
        "explanation": configured.get("explanation", fallback[2]),
    }


def score_stock(stock, settings):
    score = 30  # The primary fresh-cross condition is already satisfied.
    if stock["price"] > stock["ma200"]:
        score += 15
    if 50 <= stock["rsi"] <= 65:
        score += 15
    if stock["strongVolume"]:
        score += 15
    if stock["macd"] > stock["macdSignal"]:
        score += 10
    if stock["ma50Change"] > 0:
        score += 10
    if stock["marketCap"] > 1000:
        score += 5
    ranking = settings.get("ranking", {})
    strong_min = float(ranking.get("strongMinimum", 80))
    moderate_min = float(ranking.get("moderateMinimum", 60))
    label = "STRONG" if score >= strong_min else "MODERATE" if score >= moderate_min else "WATCHLIST"
    return score, label


def make_stock(candidate, result, closes, volumes=None, settings=None):
    """Calculate indicators and explicitly test the previous/current golden cross."""
    settings = settings or load_config()
    indicators = settings["indicators"]
    fast, slow = int(indicators["smaFast"]), int(indicators["smaSlow"])
    volume_days = int(indicators["volumeAverageDays"])
    if len(closes) <= slow or len(closes) <= volume_days:
        raise ValueError("not enough candles for configured indicators")
    volumes = volumes or [0] * len(closes)
    current, previous = closes[-1], closes[-2]
    ma_fast = average(closes, fast)
    ma_slow = average(closes, slow)
    previous_fast = sum(closes[-fast - 1:-1]) / fast
    previous_slow = sum(closes[-slow - 1:-1]) / slow
    fresh_cross = previous_fast < previous_slow and ma_fast > ma_slow and ma_fast > previous_fast
    current_volume = volumes[-1]
    average_volume = average(volumes[:-1], volume_days)
    ratio = current_volume / average_volume if average_volume else 0
    price_change = (current / previous - 1) * 100 if previous else 0
    indicators_macd = macd(closes, int(indicators["macdFast"]), int(indicators["macdSlow"]),
                           int(indicators["macdSignal"]))
    row = candidate["source"]
    market_cap = number(source_value(row, "Market Cap (Rs.Cr.)", "Market Cap", "Market Capitalisation"))
    if market_cap is None:
        raise ValueError("market cap is missing")
    technical_signal = signal_for(price_change, ratio, settings.get("signalRules"))
    current_rsi = rsi(closes, int(indicators["rsiPeriod"]))
    if current_rsi is None:
        raise ValueError("RSI could not be calculated")
    macd_value, macd_signal, macd_histogram = indicators_macd
    if macd_value is None or macd_signal is None:
        raise ValueError("MACD could not be calculated")
    as_of = datetime.fromtimestamp(result["timestamp"][-1], timezone.utc).date().isoformat()
    return {
        "name": candidate["name"], "symbol": candidate["symbol"],
        "price": round(current, 2), "lastPrice": round(current, 2),
        "recommendationPrice": round(current, 2), "ma50": round(ma_fast, 2),
        "ma200": round(ma_slow, 2), "previousMa50": round(previous_fast, 2),
        "previousMa200": round(previous_slow, 2), "change": round(price_change, 2),
        "marketCap": round(market_cap, 2), "pe": number(source_value(row, "P/E", "PE")),
        "roce": number(source_value(row, "ROCE (%)", "ROCE")),
        "volume": round(current_volume), "average": round(average_volume),
        "volumeRatio": round(ratio, 2), "strongVolume": ratio >= float(settings["screen"]["volumeRatioMin"]),
        "rsi": round(current_rsi, 2),
        "rsiClass": "Weak Momentum" if current_rsi < 50 else "Healthy Momentum" if current_rsi <= 60 else "Strong Momentum" if current_rsi <= 70 else "Overbought",
        "macd": round(macd_value, 4), "macdSignal": round(macd_signal, 4),
        "macdHistogram": round(macd_histogram, 4),
        "macdTrend": "Bullish MACD" if macd_value > macd_signal else "Bearish MACD",
        "ma50Change": round(ma_fast - previous_fast, 4),
        "sector": source_value(row, "Sector") or "Unclassified", "freshCross": fresh_cross,
        "crossed": "fresh cross" if fresh_cross else "trend intact", "asOf": as_of,
        "signal": technical_signal["label"], "recommendation": technical_signal["recommendation"],
        "explanation": technical_signal["explanation"],
        "catalyst": "Fresh 50D/200D golden cross" if fresh_cross else "Waiting for a fresh golden cross",
    }


def read_json(path, fallback):
    try:
        with path.open(encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError, TypeError):
        return fallback


def atomic_write(path, document):
    path.parent.mkdir(exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix="nse-", suffix=".json", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(document, handle, indent=2)
            handle.write("\n")
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def main():
    settings = load_config()
    generated = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    today_ist = datetime.now(ZoneInfo("Asia/Kolkata")).date().isoformat()
    existing_history = read_json(HISTORY, {"runs": []})
    if any(run.get("scanDate") == today_ist for run in existing_history.get("runs", [])):
        print(f"Scan already completed for {today_ist}; skipping duplicate run.")
        return
    old_document = read_json(OUTPUT, {})
    previous = old_document.get("baselines", {})
    stocks, scanned_stocks, warnings, successful = [], [], [], 0
    screen = settings["screen"]
    for candidate in candidates():
        try:
            result, closes, volumes = fetch_chart(candidate["symbol"])
            successful += 1
            stock = make_stock(candidate, result, closes, volumes, settings)
            scanned_stocks.append(stock)
            if stock["marketCap"] <= float(screen["minMarketCapCrore"]):
                continue
            if not stock["freshCross"]:
                continue
            stock["score"], stock["signalStrength"] = score_stock(stock, settings)
            baseline = previous.get(stock["symbol"], {})
            first_price = number(baseline.get("firstPrice")) or stock["lastPrice"]
            discovered_date = baseline.get("discoveredDate") or stock["asOf"]
            stock["firstPrice"], stock["discoveredDate"] = round(first_price, 2), discovered_date
            stock["percentChange"] = round((stock["lastPrice"] / first_price - 1) * 100, 2)
            previous[stock["symbol"]] = {"firstPrice": stock["firstPrice"], "discoveredDate": discovered_date}
            stocks.append(stock)
        except (HTTPError, URLError, KeyError, IndexError, ValueError, TypeError) as error:
            warnings.append(f"{candidate['symbol']}: {error}")
    if not successful:
        raise RuntimeError("No market data returned; " + "; ".join(warnings))
    market_dates = {stock["asOf"] for stock in scanned_stocks} if scanned_stocks else set()
    if market_dates and max(market_dates) < today_ist:
        print(f"No completed NSE session for {today_ist}; latest market data is {max(market_dates)}.")
        return
    scan_date = max(market_dates) if market_dates else today_ist

    run = {
        "generatedAt": generated, "scanDate": scan_date, "qualified": len(stocks),
        "freshCrosses": sum(1 for stock in stocks if stock["freshCross"]),
        "symbols": [stock["symbol"] for stock in stocks],
    }
    history = read_json(HISTORY, {"runs": [], "records": []})
    history["runs"] = (history.get("runs", []) + [run])[-int(settings["history"]["maxRuns"]):]
    records = history.get("records", [])
    existing_keys = {(record.get("symbol"), record.get("date")) for record in records}
    for stock in stocks:
        key = (stock["symbol"], stock["asOf"])
        if key not in existing_keys:
            records.append({"date": stock["asOf"], **stock})
    history["records"] = records
    report = {
        "generatedAt": generated, "status": "ok", "summary": {
            "qualified": len(stocks), "freshCrosses": run["freshCrosses"],
            "providerSuccesses": successful, "warnings": len(warnings),
            "strong": sum(stock["signalStrength"] == "STRONG" for stock in stocks),
            "moderate": sum(stock["signalStrength"] == "MODERATE" for stock in stocks),
            "watchlist": sum(stock["signalStrength"] == "WATCHLIST" for stock in stocks),
        }, "stocks": stocks,
    }
    document = {
        "generatedAt": generated,
        "source": "Yahoo Finance chart API (public endpoint) + user Screener CSV fundamentals",
        "screen": "fresh 50D/200D golden cross, market cap > 100 Cr, volume confirmation >= 1.5x",
        "warnings": warnings, "baselines": previous, "stocks": stocks,
    }
    atomic_write(OUTPUT, document)
    atomic_write(HISTORY, history)
    atomic_write(REPORT, report)
    write_history_csv(history["records"])
    write_html_report(report, stocks)
    print(f"Wrote {len(stocks)} qualifying fresh crosses to {OUTPUT}")
    if warnings:
        print("Warnings: " + " | ".join(warnings), file=sys.stderr)


def write_history_csv(records):
    if not records:
        return
    fields = [
        "date", "symbol", "name", "sector", "price", "change", "ma50",
        "previousMa50", "ma200", "previousMa200", "marketCap", "volume",
        "average", "volumeRatio", "rsi", "macd", "macdSignal", "score",
        "signalStrength", "recommendation",
    ]
    HISTORY_CSV.parent.mkdir(exist_ok=True)
    temporary = HISTORY_CSV.with_suffix(".tmp")
    with temporary.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(records)
    os.replace(temporary, HISTORY_CSV)


def write_html_report(report, stocks):
    report_date = stocks[0]["asOf"] if stocks else datetime.now(timezone.utc).date().isoformat()
    HTML_REPORT_DIR.mkdir(exist_ok=True)
    output = HTML_REPORT_DIR / f"{report_date}_golden_cross_report.html"
    rows = "".join(
        f"<tr><td>{index}</td><td>{stock['symbol']}</td><td>{stock['name']}</td>"
        f"<td>{stock['price']:.2f}</td><td>{stock['ma50']:.2f}</td><td>{stock['ma200']:.2f}</td>"
        f"<td>{stock['marketCap']:.2f}</td><td>{stock['volumeRatio']:.2f}x</td>"
        f"<td>{stock['rsi']:.2f}</td><td>{stock['macd']:.4f}</td><td>{stock['score']}</td>"
        f"<td>{stock['signalStrength']}</td></tr>"
        for index, stock in enumerate(sorted(stocks, key=lambda item: item["score"], reverse=True), 1)
    )
    html = f"""<!doctype html>
<html lang="en"><meta charset="utf-8"><title>Golden Cross Report {report_date}</title>
<style>body{{font:15px system-ui;margin:2rem;color:#172033}}table{{border-collapse:collapse;width:100%}}
th,td{{border:1px solid #d9dee8;padding:.55rem;text-align:left}}th{{background:#172033;color:white}}
strong{{font-size:1.2rem}}</style>
<h1>Golden Cross Scanner - {report_date}</h1>
<p>Qualified: <strong>{report['summary']['qualified']}</strong> |
Fresh crosses: <strong>{report['summary']['freshCrosses']}</strong> |
Strong: <strong>{report['summary']['strong']}</strong> |
Moderate: <strong>{report['summary']['moderate']}</strong> |
Watchlist: <strong>{report['summary']['watchlist']}</strong></p>
<table><thead><tr><th>Rank</th><th>Symbol</th><th>Company</th><th>Price</th><th>50 DMA</th>
<th>200 DMA</th><th>Market Cap Cr</th><th>Volume Ratio</th><th>RSI</th><th>MACD</th>
<th>Score</th><th>Signal</th></tr></thead><tbody>{rows}</tbody></table></html>"""
    output.write_text(html, encoding="utf-8")


if __name__ == "__main__":
    try:
        main()
    except (OSError, RuntimeError, csv.Error, ValueError) as error:
        print(f"Update failed: {error}", file=sys.stderr)
        sys.exit(1)
