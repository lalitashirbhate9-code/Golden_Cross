from __future__ import annotations
import csv, json, math, os, tempfile, time
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
INPUT = ROOT / "screener_latest_results.csv"
OUTPUT = ROOT / "data" / "nse_golden_cross.json"
HISTORY = ROOT / "data" / "history.json"
REPORT = ROOT / "data" / "report.json"
HISTORY_CSV = ROOT / "data" / "stock_history.csv"
CONFIG = ROOT / "config.json"
SYMBOLS = {"N R Agarwal Inds":"NRAIL", "Nexus Select":"NXST", "Nitiraj Engineer":"NITIRAJ"}


def num(value, default=None):
    try:
        result = float(str(value or "").replace(",", "").replace("%", "").strip())
        return result if math.isfinite(result) else default
    except (TypeError, ValueError):
        return default


def load_json(path, fallback):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return fallback


def candidates():
    if not INPUT.exists():
        return [{"name": name, "symbol": symbol, "source": {}} for name, symbol in SYMBOLS.items()]
    rows = []
    with INPUT.open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            name = (row.get("Stock") or row.get("Name") or "").strip()
            symbol = (row.get("NSE Symbol") or row.get("NSECode") or "").strip() or SYMBOLS.get(name, "")
            if name and symbol:
                rows.append({"name": name, "symbol": symbol.upper(), "source": row})
    if not rows:
        raise RuntimeError("No candidates with an NSE symbol were found.")
    return rows


def fetch(symbol):
    end = int(time.time())
    url = "https://query1.finance.yahoo.com/v8/finance/chart/%s.NS?period1=%d&period2=%d&interval=1d&events=history&includeAdjustedClose=true" % (symbol, end - 86400 * 730, end)
    request = Request(url, headers={"User-Agent": "nse-golden-cross-dashboard/1.0"})
    for attempt in range(3):
        try:
            with urlopen(request, timeout=25) as response:
                result = json.load(response)["chart"]["result"][0]
            closes = [float(v) for v in result["indicators"]["quote"][0]["close"] if v is not None]
            volumes = [float(v or 0) for v in result["indicators"]["quote"][0]["volume"]]
            if len(closes) < 220:
                raise ValueError("fewer than 220 daily closes")
            return result, closes, volumes
        except HTTPError as error:
            if error.code != 429 or attempt == 2:
                raise
            time.sleep(2 ** attempt)


def average(values, size):
    return sum(values[-size:]) / size


def ema(values, period):
    result = sum(values[:period]) / period
    multiplier = 2 / (period + 1)
    for value in values[period:]:
        result = (value - result) * multiplier + result
    return result


def rsi(values, period=14):
    changes = [after - before for before, after in zip(values[-period-1:-1], values[-period:])]
    gains = sum(max(change, 0) for change in changes) / period
    losses = sum(max(-change, 0) for change in changes) / period
    return 100.0 if losses == 0 and gains else 50.0 if losses == 0 else 100 - 100 / (1 + gains / losses)


def macd(values, fast=12, slow=26, signal=9):
    line = []
    for index in range(slow, len(values) + 1):
        line.append(ema(values[:index], fast) - ema(values[:index], slow))
    signal_value = ema(line, signal)
    return line[-1], signal_value, line[-1] - signal_value


def make_stock(candidate, result, closes, volumes, settings):
    fast, slow = 50, 200
    current, previous = closes[-1], closes[-2]
    ma50, ma200 = average(closes, fast), average(closes, slow)
    previous50 = sum(closes[-fast-1:-1]) / fast
    previous200 = sum(closes[-slow-1:-1]) / slow
    fresh = previous50 < previous200 and ma50 > ma200 and ma50 > previous50
    average_volume = average(volumes[:-1], 20)
    volume_ratio = volumes[-1] / average_volume if average_volume else 0
    macd_value, macd_signal, macd_histogram = macd(closes)
    row = candidate["source"]
    market_cap = num(row.get("Market Cap (Rs.Cr.)") or row.get("Market Cap"))
    if market_cap is None:
        raise ValueError("market cap is missing")
    current_rsi = rsi(closes)
    as_of = datetime.fromtimestamp(result["timestamp"][-1], timezone.utc).date().isoformat()
    return {"name": candidate["name"], "symbol": candidate["symbol"], "price": round(current,2), "lastPrice": round(current,2), "previousClose": round(previous,2), "ma50": round(ma50,2), "previousMa50": round(previous50,2), "ma200": round(ma200,2), "previousMa200": round(previous200,2), "marketCap": round(market_cap,2), "volume": round(volumes[-1]), "average": round(average_volume), "volumeRatio": round(volume_ratio,2), "strongVolume": volume_ratio >= 1.5, "rsi": round(current_rsi,2), "macd": round(macd_value,4), "macdSignal": round(macd_signal,4), "macdHistogram": round(macd_histogram,4), "macdTrend": "Bullish MACD" if macd_value > macd_signal else "Bearish MACD", "ma50Change": round(ma50 - previous50,4), "freshCross": fresh, "crossed": "fresh cross" if fresh else "trend intact", "asOf": as_of, "sector": row.get("Sector") or "Unclassified", "pe": num(row.get("P/E")), "roce": num(row.get("ROCE (%)"))}


def score(stock):
    points = 30
    points += 15 if stock["price"] > stock["ma200"] else 0
    points += 15 if 50 <= stock["rsi"] <= 65 else 0
    points += 15 if stock["strongVolume"] else 0
    points += 10 if stock["macd"] > stock["macdSignal"] else 0
    points += 10 if stock["ma50Change"] > 0 else 0
    points += 5 if stock["marketCap"] > 1000 else 0
    return points, "STRONG" if points >= 80 else "MODERATE" if points >= 60 else "WATCHLIST"


def atomic_write(path, document):
    path.parent.mkdir(exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix="nse-", suffix=".json", dir=path.parent)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(document, handle, indent=2)
        handle.write("\n")
    os.replace(temporary, path)


def main():
    settings = load_json(CONFIG, {"screen": {"minMarketCapCrore": 100}, "history": {"maxRuns": 365}})
    old = load_json(OUTPUT, {})
    history = load_json(HISTORY, {"runs": [], "records": []})
    generated = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    stocks, scanned, warnings = [], [], []
    for candidate in candidates():
        try:
            result, closes, volumes = fetch(candidate["symbol"])
            stock = make_stock(candidate, result, closes, volumes, settings)
            scanned.append(stock)
            if stock["marketCap"] <= float(settings["screen"].get("minMarketCapCrore", 100)) or not stock["freshCross"]:
                continue
            stock["score"], stock["signalStrength"] = score(stock)
            baseline = old.get("baselines", {}).get(stock["symbol"], {})
            first = num(baseline.get("firstPrice")) or stock["lastPrice"]
            discovered = baseline.get("discoveredDate") or stock["asOf"]
            stock["firstPrice"], stock["discoveredDate"] = round(first,2), discovered
            stock["percentChange"] = round((stock["lastPrice"] / first - 1) * 100, 2)
            stocks.append(stock)
        except (HTTPError, URLError, KeyError, IndexError, ValueError, TypeError) as error:
            warnings.append("%s: %s" % (candidate["symbol"], error))
    if not scanned:
        raise RuntimeError("No market data returned; " + "; ".join(warnings))
    scan_date = max(stock["asOf"] for stock in scanned)
    baseline_map = old.get("baselines", {})
    for stock in stocks:
        baseline_map[stock["symbol"]] = {"firstPrice": stock["firstPrice"], "discoveredDate": stock["discoveredDate"]}
    records = history.get("records", [])
    keys = {(record.get("symbol"), record.get("date")) for record in records}
    for stock in stocks:
        if (stock["symbol"], stock["asOf"]) not in keys:
            records.append({"date": stock["asOf"], **stock})
    run = {"generatedAt": generated, "scanDate": scan_date, "qualified": len(stocks), "freshCrosses": len(stocks), "symbols": [stock["symbol"] for stock in stocks]}
    history["runs"] = (history.get("runs", []) + [run])[-int(settings.get("history", {}).get("maxRuns", 365)):]
    history["records"] = records
    report = {"generatedAt": generated, "status": "ok", "summary": {"qualified": len(stocks), "freshCrosses": len(stocks), "strong": sum(s["signalStrength"] == "STRONG" for s in stocks), "moderate": sum(s["signalStrength"] == "MODERATE" for s in stocks), "watchlist": sum(s["signalStrength"] == "WATCHLIST" for s in stocks), "warnings": len(warnings)}, "stocks": stocks}
    document = {"generatedAt": generated, "source": "Yahoo Finance chart API + Screener CSV fundamentals", "screen": "previous 50D < previous 200D; current 50D > current 200D; rising 50D; market cap > 100 Cr", "warnings": warnings, "baselines": baseline_map, "stocks": stocks}
    atomic_write(OUTPUT, document)
    atomic_write(HISTORY, history)
    atomic_write(REPORT, report)
    HISTORY_CSV.parent.mkdir(exist_ok=True)
    fields = ["date", "symbol", "name", "price", "ma50", "ma200", "marketCap", "volumeRatio", "rsi", "macd", "score", "signalStrength"]
    with HISTORY_CSV.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader(); writer.writerows(records)
    print("Wrote %d qualifying fresh crosses for %s" % (len(stocks), scan_date))


if __name__ == "__main__":
    main()
import csv
import json
import os
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
INPUT = ROOT / "screener_latest_results.csv"
OUTPUT = ROOT / "data" / "nse_golden_cross.json"
SYMBOLS = {"N R Agarwal Inds":"NRAIL", "Nexus Select":"NXST", "Nitiraj Engineer":"NITIRAJ"}

def num(value, default=None):
    try:
        return float(str(value or "").replace(",", "").replace("%", "").strip())
    except (TypeError, ValueError):
        return default

def candidates():
    with INPUT.open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            name = (row.get("Stock") or row.get("Name") or "").strip()
            symbol = (row.get("NSE Symbol") or row.get("NSECode") or "").strip() or SYMBOLS.get(name, "")
            if name and symbol:
                yield {"name":name, "symbol":symbol.upper(), "source":row}

def fetch(symbol):
    end = int(time.time())
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}.NS?period1={end-730*86400}&period2={end}&interval=1d&events=history&includeAdjustedClose=true"
    request = Request(url, headers={"User-Agent":"nse-golden-cross-dashboard/1.0"})
    for attempt in range(3):
        try:
            with urlopen(request, timeout=25) as response:
                result = json.load(response)["chart"]["result"][0]
            closes = [float(v) for v in result["indicators"]["quote"][0]["close"] if v is not None]
            if len(closes) < 200:
                raise ValueError("fewer than 200 daily closes")
            return result, closes
        except HTTPError as error:
            if error.code != 429 or attempt == 2:
                raise
            time.sleep(2 ** attempt)

def main():
    previous = {}
    if OUTPUT.exists():
        try:
            old = json.loads(OUTPUT.read_text(encoding="utf-8"))
            previous.update(old.get("baselines", {}))
            for stock in old.get("stocks", []):
                if stock.get("symbol"):
                    previous.setdefault(stock["symbol"], {"firstPrice":stock.get("firstPrice", stock.get("lastPrice", stock.get("price"))), "discoveredDate":stock.get("discoveredDate", stock.get("asOf"))})
        except (OSError, ValueError, TypeError):
            previous = {}
    stocks, warnings, successful = [], [], 0
    for candidate in candidates():
        try:
            result, closes = fetch(candidate["symbol"]); successful += 1
            last = closes[-1]; previous_close = closes[-2]
            ma50 = sum(closes[-50:]) / 50; ma200 = sum(closes[-200:]) / 200
            as_of = datetime.fromtimestamp(result["timestamp"][-1], timezone.utc).date().isoformat()
            row = candidate["source"]
            stock = {"name":candidate["name"], "symbol":candidate["symbol"], "lastPrice":round(last,2), "price":round(last,2), "ma50":round(ma50,2), "ma200":round(ma200,2), "change":round((last/previous_close-1)*100,2), "pe":num(row.get("P/E")), "roce":num(row.get("ROCE (%)")), "marketCap":num(row.get("Market Cap (Rs.Cr.)"),0), "volume":num(row.get("Volume 1D"),0), "average":num(row.get("Average Volume 1Mth"),0), "sector":row.get("Sector") or "Unclassified", "catalyst":"50D MA is above 200D MA", "crossed":"trend intact", "qualified":ma50 >= ma200 and last >= ma50, "asOf":as_of}
            if stock["qualified"]:
                baseline = previous.get(stock["symbol"], {})
                first = num(baseline.get("firstPrice")); discovered = baseline.get("discoveredDate")
                if not first or not discovered:
                    first, discovered = stock["lastPrice"], stock["asOf"]
                stock["firstPrice"] = round(first,2); stock["discoveredDate"] = discovered; stock["percentChange"] = round((stock["lastPrice"]/first-1)*100,2)
                previous[stock["symbol"]] = {"firstPrice":stock["firstPrice"], "discoveredDate":stock["discoveredDate"]}
                stocks.append(stock)
        except (HTTPError, URLError, KeyError, IndexError, ValueError, TypeError) as error:
            warnings.append(f"{candidate['symbol']}: {error}")
    if not successful:
        raise RuntimeError("No market data returned; " + "; ".join(warnings))
    document = {"generatedAt":datetime.now(timezone.utc).isoformat().replace("+00:00","Z"), "source":"Yahoo Finance chart API (public endpoint)", "screen":"50D MA >= 200D MA and price >= 50D MA", "warnings":warnings, "baselines":previous, "stocks":stocks}
    OUTPUT.parent.mkdir(exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix="nse-", suffix=".json", dir=OUTPUT.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(document, handle, indent=2); handle.write("\n")
        os.replace(temporary, OUTPUT)
    finally:
        if os.path.exists(temporary): os.unlink(temporary)

if __name__ == "__main__":
    main()
