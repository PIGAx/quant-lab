# -*- coding: utf-8 -*-
"""
Quant Strategy Lab — 價格資料快取
==================================
由 GitHub Actions 每日執行(也可本機手動跑):
1. 抓「市場總覽指數」+ config/watchlist.json 內所有標的的還原日線(3 年)
2. 存成 data/prices/<檔名>.json(網頁同網域直接 fetch,無 CORS 問題)
3. 產生 data/prices/index.json 清單(最新價、漲跌幅、sparkline 用縮圖數列)
"""
import json
import re
from pathlib import Path

import pandas as pd
import yfinance as yf

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "prices"
WATCHLIST = ROOT / "config" / "watchlist.json"

# 市場總覽固定清單(可自行增減)
MARKET_SUMMARY = [
    {"ticker": "^TWII",   "name": "台灣加權指數",   "market": "TW"},
    {"ticker": "0050.TW", "name": "元大台灣50",     "market": "TW"},
    {"ticker": "^GSPC",   "name": "S&P 500",        "market": "US"},
    {"ticker": "^IXIC",   "name": "Nasdaq",         "market": "US"},
    {"ticker": "^DJI",    "name": "道瓊工業",       "market": "US"},
    {"ticker": "^SOX",    "name": "費城半導體",     "market": "US"},
    {"ticker": "TWD=X",   "name": "USD/TWD",        "market": "FX"},
]


def safe_name(ticker: str) -> str:
    """^TWII → IDX_TWII、TWD=X → TWD_X、2330.TW → 2330.TW"""
    return re.sub(r"[^A-Za-z0-9._-]", "_", ticker.replace("^", "IDX_"))


def main():
    OUT.mkdir(parents=True, exist_ok=True)

    items = {m["ticker"]: dict(m) for m in MARKET_SUMMARY}
    if WATCHLIST.exists():
        for w in json.loads(WATCHLIST.read_text(encoding="utf-8")):
            tk = w["ticker"]
            if tk not in items:
                items[tk] = {
                    "ticker": tk,
                    "name": w.get("label", tk),
                    "market": "TW" if tk.endswith(".TW") else "US",
                }

    tickers = list(items)
    print(f"下載 {len(tickers)} 檔:{tickers}")
    px = yf.download(tickers, period="3y", auto_adjust=True,
                     group_by="ticker", progress=False)

    manifest = []
    for tk, meta in items.items():
        try:
            df = px[tk].dropna() if len(tickers) > 1 else px.dropna()
            if len(df) < 30:
                print(f"[skip] {tk} 資料不足")
                continue
            fname = safe_name(tk) + ".json"
            rec = {
                "ticker": tk,
                "name": meta["name"],
                "market": meta["market"],
                "updated": str(df.index[-1].date()),
                "dates": [str(d.date()) for d in df.index],
                "o": [round(float(x), 4) for x in df["Open"]],
                "h": [round(float(x), 4) for x in df["High"]],
                "l": [round(float(x), 4) for x in df["Low"]],
                "c": [round(float(x), 4) for x in df["Close"]],
                "v": [int(x) if not pd.isna(x) else 0 for x in df["Volume"]],
            }
            (OUT / fname).write_text(
                json.dumps(rec, ensure_ascii=False, separators=(",", ":")),
                encoding="utf-8")

            close = rec["c"]
            last, prev = close[-1], close[-2]
            spark = close[-60:]  # 近 60 日 sparkline
            manifest.append({
                "ticker": tk, "name": meta["name"], "market": meta["market"],
                "file": fname, "updated": rec["updated"],
                "last": round(last, 2),
                "chg": round(last - prev, 2),
                "chgPct": round((last / prev - 1) * 100, 2),
                "spark": [round(x, 2) for x in spark],
                "isIndex": tk in {m["ticker"] for m in MARKET_SUMMARY},
            })
            print(f"[ok] {tk} {rec['updated']} close={last:,.2f}")
        except Exception as e:
            print(f"[error] {tk}: {e}")

    (OUT / "index.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"完成:{len(manifest)} 檔 → data/prices/")


if __name__ == "__main__":
    main()
