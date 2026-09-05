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
