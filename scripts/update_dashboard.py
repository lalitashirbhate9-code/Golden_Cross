import json
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "data" / "nse_golden_cross.json"
CANDIDATES = [
    {"name":"N R Agarwal Inds","symbol":"NRAIL","sector":"Paper & Packaging","pe":15.05,"roce":8.49,"marketCap":993.92,"catalyst":"Profit growth +111% · paper demand"},
    {"name":"Nexus Select","symbol":"NXST","sector":"Real Estate","pe":57.06,"roce":5.83,"marketCap":25132.34,"catalyst":"Sales growth +10.9% · yield 1.47%"},
    {"name":"Nitiraj Engineer","symbol":"NITIRAJ","sector":"Engineering","pe":93.62,"roce":1.79,"marketCap":224.69,"catalyst":"Profit growth +419% · order momentum"},
]

def fetch(symbol):
    end = int(time.time())
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}.NS?period1={end-730*86400}&period2={end}&interval=1d&events=history"
    request = Request(url, headers={"User-Agent":"nse-golden-cross-dashboard/1.0"})
    for attempt in range(3):
        try:
            with urlopen(request, timeout=25) as response:
                payload = json.load(response)
            result = payload["chart"]["result"][0]
            closes = [float(v) for v in result["indicators"]["quote"][0]["close"] if v is not None]
            if len(closes) < 200: raise ValueError("fewer than 200 daily closes")
            return result, closes
        except Exception:
            if attempt == 2: raise
            time.sleep(2 ** attempt)

def main():
    stocks, warnings = [], []
    for candidate in CANDIDATES:
        try:
            result, closes = fetch(candidate["symbol"])
            price, previous = closes[-1], closes[-2]
            ma50, ma200 = sum(closes[-50:]) / 50, sum(closes[-200:]) / 200
            if ma50 >= ma200 and price >= ma50:
                stock = dict(candidate, price=round(price,2), ma50=round(ma50,2), ma200=round(ma200,2), change=round((price/previous-1)*100,2), volume=0, average=0, crossed="trend intact", qualified=True, asOf=datetime.fromtimestamp(result["timestamp"][-1], timezone.utc).date().isoformat())
                stocks.append(stock)
        except Exception as error:
            warnings.append(f"{candidate['symbol']}: {error}")
    document = {"generatedAt": datetime.now(timezone.utc).isoformat().replace("+00:00","Z"), "source":"Yahoo Finance chart API (public endpoint)", "screen":"50D MA >= 200D MA and price >= 50D MA", "warnings":warnings, "stocks":stocks}
    OUTPUT.parent.mkdir(exist_ok=True)
    temporary = OUTPUT.with_suffix(".tmp")
    temporary.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    temporary.replace(OUTPUT)

if __name__ == "__main__":
    main()
