# -*- coding: utf-8 -*-
"""
Quant Strategy Lab — 每日訊號掃描器
====================================
由 GitHub Actions 排程執行(也可本機手動跑):
1. 讀取 config/watchlist.json 的「標的 × 策略」清單
2. yfinance 抓還原日線 → 計算每個策略今日/昨日訊號
3. 訊號翻轉(0→1 買進、1→0 出場)→ 發 Telegram 通知
4. 狀態寫入 data/signal_state.json(commit 回 repo,避免重複通知)

環境變數(GitHub Secrets):
  TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
"""
import json
import os
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
import requests
import yfinance as yf

ROOT = Path(__file__).resolve().parent.parent
WATCHLIST = ROOT / "config" / "watchlist.json"
STATE = ROOT / "data" / "signal_state.json"

TG_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TG_CHAT = os.environ.get("TELEGRAM_CHAT_ID", "")


# ────────────────────────── 策略訊號(與網站回測室邏輯一致) ──────────────────────────
def sig_ma_cross(df, p):
    fast = df["Close"].rolling(int(p.get("fast", 20))).mean()
    slow = df["Close"].rolling(int(p.get("slow", 60))).mean()
    return (fast > slow).astype(int)


def sig_momentum(df, p):
    lb = int(p.get("lookback", 120))
    return (df["Close"] > df["Close"].shift(lb)).astype(int)


def sig_breakout(df, p):
    in_n, out_n = int(p.get("inN", 20)), int(p.get("outN", 10))
    hh = df["High"].rolling(in_n).max().shift(1)
    ll = df["Low"].rolling(out_n).min().shift(1)
    sig = np.zeros(len(df))
    hold = 0
    close = df["Close"].values
    for i in range(len(df)):
        if not hold and not np.isnan(hh.iloc[i]) and close[i] >= hh.iloc[i]:
            hold = 1
        elif hold and not np.isnan(ll.iloc[i]) and close[i] <= ll.iloc[i]:
            hold = 0
        sig[i] = hold
    return pd.Series(sig.astype(int), index=df.index)


def sig_seasonal(df, p):
    months = p.get("months", [11, 12, 1, 2, 3, 4])
    return pd.Series(df.index.month.isin(months).astype(int), index=df.index)


def sig_rsi_pullback(df, p):
    ma_len = int(p.get("maLen", 200))
    rsi_len = int(p.get("rsiLen", 14))
    buy_lv, tp_lv = int(p.get("buyLv", 30)), int(p.get("tpLv", 70))
    trend = df["Close"].rolling(ma_len).mean()
    delta = df["Close"].diff()
    up = delta.clip(lower=0).ewm(alpha=1 / rsi_len, adjust=False).mean()
    dn = (-delta.clip(upper=0)).ewm(alpha=1 / rsi_len, adjust=False).mean()
    rsi = 100 - 100 / (1 + up / dn.replace(0, np.nan))
    sig = np.zeros(len(df))
    hold = 0
    for i in range(1, len(df)):
        if np.isnan(trend.iloc[i]) or np.isnan(rsi.iloc[i]):
            continue
        if not hold and df["Close"].iloc[i] > trend.iloc[i] \
                and rsi.iloc[i - 1] <= buy_lv < rsi.iloc[i]:
            hold = 1
        elif hold and (rsi.iloc[i] >= tp_lv or df["Close"].iloc[i] < trend.iloc[i]):
            hold = 0
        sig[i] = hold
    return pd.Series(sig.astype(int), index=df.index)


STRATEGIES = {
    "ma_cross": sig_ma_cross,
    "momentum": sig_momentum,
    "breakout": sig_breakout,
    "seasonal": sig_seasonal,
    "rsi_pullback": sig_rsi_pullback,
}


# ────────────────────────── Telegram ──────────────────────────
def tg_send(text: str):
    if not TG_TOKEN or not TG_CHAT:
        print("[warn] 未設定 Telegram secrets,僅列印:\n" + text)
        return
    r = requests.post(
        f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
        json={"chat_id": TG_CHAT, "text": text, "parse_mode": "HTML"},
        timeout=15,
    )
    if not r.ok:
        print("[error] Telegram 發送失敗:", r.text)


# ────────────────────────── 主流程 ──────────────────────────
def main():
    watchlist = json.loads(WATCHLIST.read_text(encoding="utf-8"))
    state = json.loads(STATE.read_text(encoding="utf-8")) if STATE.exists() else {}

    tickers = sorted({w["ticker"] for w in watchlist})
    print(f"下載 {len(tickers)} 檔:{tickers}")
    px = yf.download(tickers, period="3y", auto_adjust=True,
                     group_by="ticker", progress=False)

    changes, holds = [], []
    for w in watchlist:
        tk, st = w["ticker"], w["strategy"]
        params = w.get("params", {})
        label = w.get("label", f"{tk} {st}")
        fn = STRATEGIES.get(st)
        if fn is None:
            print(f"[skip] 未知策略 {st}")
            continue
        try:
            df = px[tk].dropna() if len(tickers) > 1 else px.dropna()
            if len(df) < 60:
                print(f"[skip] {tk} 資料不足")
                continue
            sig = fn(df, params)
            today, prev = int(sig.iloc[-1]), int(sig.iloc[-2])
            last_px = float(df["Close"].iloc[-1])
            key = f"{tk}|{st}|{json.dumps(params, sort_keys=True)}"
            state[key] = {"signal": today, "date": str(df.index[-1].date()),
                          "price": round(last_px, 2)}
            if today != prev:
                arrow = "🔴 買進訊號" if today == 1 else "🟢 出場訊號"  # 台股慣例紅買
                changes.append(
                    f"{arrow} <b>{label}</b>\n"
                    f"  {tk} @ {last_px:,.2f}({df.index[-1].date()})"
                )
            elif today == 1:
                holds.append(f"{label}({tk})")
        except Exception as e:
            print(f"[error] {tk} {st}: {e}")

    STATE.parent.mkdir(exist_ok=True)
    STATE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")

    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    if changes:
        msg = f"📡 <b>Quant Lab 訊號掃描</b> {now}\n\n" + "\n\n".join(changes)
        if holds:
            msg += "\n\n📌 持倉中:" + "、".join(holds)
        tg_send(msg)
        print("已通知訊號翻轉:", len(changes))
    else:
        print("今日無訊號翻轉。持倉中:", holds)
        # 想每天都收心跳訊息的話,取消下行註解:
        # tg_send(f"📡 Quant Lab {now}:無新訊號。持倉:{'、'.join(holds) or '空手'}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
