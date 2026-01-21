import os
import time
import requests
import pandas as pd

BASE_URL = os.getenv("MEXC_CONTRACT_BASE_URL", "https://contract.mexc.com/api/v1")

# Template do link do par:
# - padrão abre a página de perp no navegador; no celular pode abrir o app se a MEXC suportar universal links
# - se você tiver um deep link que abre direto no app, substitua via env MEXC_LINK_TEMPLATE
MEXC_LINK_TEMPLATE = os.getenv("MEXC_LINK_TEMPLATE", "https://futures.mexc.com/exchange/{symbol}")

# Agora usamos múltiplos timeframes
TIMEFRAMES = [t.strip() for t in os.getenv("TIMEFRAMES", "2h,4h,1d").split(",") if t.strip()]

SHORT_MA = int(os.getenv("SHORT_MA", "10"))
LONG_MA = int(os.getenv("LONG_MA", "100"))
MA_TYPE = os.getenv("MA_TYPE", "sma").lower()  # ema|sma

TOP_PERPS = int(os.getenv("TOP_PERPS", "80"))
OHLCV_LIMIT = int(os.getenv("OHLCV_LIMIT", "300"))

QUOTE = os.getenv("QUOTE", "USDT")
DEBUG = os.getenv("DEBUG", "0") == "1"

# Volume no output:
# - "M": sempre em milhões (6 bilhões -> 6000M)
# - "AUTO": usa B/M
VOLUME_MODE = os.getenv("VOLUME_MODE", "M").upper()

# Telegram
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
TELEGRAM_TOP_N = int(os.getenv("TELEGRAM_TOP_N", "20"))  # quantos pares mandar na mensagem


def calc_ma(series: pd.Series, period: int, ma_type: str) -> pd.Series:
    if ma_type == "sma":
        return series.rolling(period).mean()
    if ma_type == "ema":
        return series.ewm(span=period, adjust=False).mean()
    raise ValueError("MA_TYPE deve ser 'ema' ou 'sma'")


def format_volume(x) -> str:
    """Formata volume 24h em USDT."""
    try:
        x = float(x)
    except Exception:
        return ""

    if VOLUME_MODE == "AUTO":
        if x >= 1_000_000_000:
            return f"{x / 1_000_000_000:.1f}B".replace(".0B", "B")
        return f"{int(round(x / 1_000_000))}M"

    return f"{int(round(x / 1_000_000))}M"


def symbol_to_link(symbol: str) -> str:
    return MEXC_LINK_TEMPLATE.format(symbol=symbol)


def http_get_json(url, params=None, tries=3, timeout=25):
    last = None
    for i in range(tries):
        try:
            r = requests.get(url, params=params, timeout=timeout)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            last = e
            time.sleep(1.5 * (i + 1))
    raise last


def extract_turnover_usdt_24h(t: dict):
    # MEXC perps: normalmente amount24 (turnover USDT 24h)
    for k in ["amount24", "turnover24", "quoteVolume", "quoteVol"]:
        v = t.get(k)
        if v is not None:
            try:
                return float(v)
            except Exception:
                pass
    return None


def fetch_contract_tickers():
    url = f"{BASE_URL}/contract/ticker"
    data = http_get_json(url)

    tickers = None
    if isinstance(data, dict):
        tickers = data.get("data") or data.get("datas") or data.get("ticker") or data.get("result")
    if tickers is None and isinstance(data, list):
        tickers = data

    if not isinstance(tickers, list):
        raise RuntimeError(f"Formato inesperado em ticker: {data}")

    return tickers


def get_top_usdt_perps_and_turnover(n=80):
    tickers = fetch_contract_tickers()

    rows = []
    turnover_map = {}

    for t in tickers:
        if not isinstance(t, dict):
            continue

        sym = t.get("symbol") or t.get("contractId") or t.get("name")
        if not sym:
            continue
        if not sym.endswith(f"_{QUOTE}"):
            continue

        turnover = extract_turnover_usdt_24h(t)
        if turnover is None:
            turnover = 0.0

        turnover_map[sym] = float(turnover)
        rows.append((sym, float(turnover)))

    rows.sort(key=lambda x: x[1], reverse=True)
    symbols_top = [s for s, _ in rows[:n]]
    return symbols_top, turnover_map


def timeframe_to_mexc_interval_and_resample(tf: str):
    tf = tf.lower().strip()
    if tf == "1h":
        return "Min60", None, 1
    if tf == "2h":
        return "Min60", "2H", 2  # puxa 1h e agrega em 2h
    if tf == "4h":
        return "Hour4", None, 1
    if tf in ("1d", "d"):
        return "Day1", None, 1
    raise ValueError("TIMEFRAMES suportados: 1h, 2h, 4h, 1d")


def to_datetime_auto(ts_series: pd.Series) -> pd.Series:
    s = pd.to_numeric(ts_series, errors="coerce")
    unit = "s" if s.dropna().median() < 1e12 else "ms"
    return pd.to_datetime(s, unit=unit, utc=True)


def parse_kline_to_df(payload):
    if isinstance(payload, dict):
        data = payload.get("data") or payload.get("datas") or payload.get("result")
    else:
        data = payload

    if data is None:
        raise RuntimeError(f"Resposta sem data: {payload}")

    if isinstance(data, dict) and "time" in data:
        df = pd.DataFrame(
            {
                "ts": data["time"],
                "open": data.get("open"),
                "high": data.get("high"),
                "low": data.get("low"),
                "close": data.get("close"),
                "volume": data.get("vol") or data.get("volume"),
            }
        )
    elif isinstance(data, list):
        if len(data) == 0:
            return pd.DataFrame(columns=["ts", "open", "high", "low", "close", "volume"])
        first = data[0]
        if isinstance(first, (list, tuple)):
            df = pd.DataFrame(data).iloc[:, :6]
            df.columns = ["ts", "open", "high", "low", "close", "volume"]
        elif isinstance(first, dict):
            df = pd.DataFrame(data)
            rename = {}
            for a, b in [
                ("time", "ts"),
                ("timestamp", "ts"),
                ("t", "ts"),
                ("o", "open"),
                ("h", "high"),
                ("l", "low"),
                ("c", "close"),
                ("v", "volume"),
                ("vol", "volume"),
            ]:
                if a in df.columns and b not in df.columns:
                    rename[a] = b
            df = df.rename(columns=rename)
            df = df[["ts", "open", "high", "low", "close", "volume"]]
        else:
            raise RuntimeError(f"Formato de kline inesperado: {first}")
    else:
        raise RuntimeError(f"Formato de kline inesperado: {type(data)}")

    df["ts"] = to_datetime_auto(df["ts"])
    for c in ["open", "high", "low", "close", "volume"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")

    df = df.dropna(subset=["ts", "close"]).sort_values("ts")
    return df


def fetch_ohlcv(symbol: str, tf: str, limit: int):
    interval, resample_rule, factor = timeframe_to_mexc_interval_and_resample(tf)

    end_s = int(time.time())  # SEGUNDOS
    interval_s = {
        "Min60": 60 * 60,
        "Hour4": 4 * 60 * 60,
        "Day1": 24 * 60 * 60,
    }[interval]

    base_limit = int(limit * factor)
    start_s = end_s - int(base_limit * interval_s * 1.3)

    url = f"{BASE_URL}/contract/kline/{symbol}"
    params = {"interval": interval, "start": start_s, "end": end_s}

    payload = http_get_json(url, params=params)
    df = parse_kline_to_df(payload)

    if resample_rule:
        df = df.set_index("ts")
        df = (
            df.resample(resample_rule)
            .agg(
                open=("open", "first"),
                high=("high", "max"),
                low=("low", "min"),
                close=("close", "last"),
                volume=("volume", "sum"),
            )
            .dropna(subset=["close"])
            .reset_index()
        )

    return df.tail(limit)


def df_to_markdown_with_links(df: pd.DataFrame, title: str, out_path: str, timeframe: str, top_n: int = 200):
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(f"# {title}\n\n")
        f.write(f"- Gerado em UTC: {time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime())}\n")
        f.write(f"- Timeframe: `{timeframe}` | MA: `{MA_TYPE} {SHORT_MA}/{LONG_MA}` | Top perps: `{TOP_PERPS}`\n\n")

        f.write("| Par | Trend | Close | Volume (24h, USDT) | Dist (%) |\n")
        f.write("|---|---:|---:|---:|---:|\n")

        if df is None or df.empty:
            f.write("| - | - | - | - | - |\n")
            return

        for _, row in df.head(top_n).iterrows():
            sym = str(row.get("symbol", ""))
            link = str(row.get("link", ""))
            trend = str(row.get("trend", ""))
            close = row.get("close", "")
            vol = str(row.get("volume_diario", ""))
            dist = row.get("ma_dist_pct", "")

            try:
                close_str = f"{float(close):.6g}"
            except Exception:
                close_str = str(close)

            try:
                dist_str = f"{float(dist):.3f}"
            except Exception:
                dist_str = str(dist)

            par_md = f"[{sym}]({link})" if link else sym
            f.write(f"| {par_md} | {trend} | {close_str} | {vol} | {dist_str} |\n")


def save_outputs_bullish(timeframe: str, out: pd.DataFrame, bullish_df: pd.DataFrame):
    tf_slug = timeframe.replace(" ", "").lower()

    out.to_csv(f"scanner_resultado_{tf_slug}.csv", index=False)
    bullish_df.to_csv(f"scanner_alta_{tf_slug}.csv", index=False)

    df_to_markdown_with_links(
        bullish_df,
        f"Scanner ALTA ({timeframe}) (menor distância -> maior)",
        f"scanner_alta_{tf_slug}.md",
        timeframe=timeframe,
    )
    df_to_markdown_with_links(
        out,
        f"Scanner COMPLETO ({timeframe})",
        f"scanner_resumo_{tf_slug}.md",
        timeframe=timeframe,
    )


def html_escape(s: str) -> str:
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def telegram_send_html(html: str):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": html,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    requests.post(url, json=payload, timeout=25)


def build_telegram_html_bullish(timeframe: str, bullish_df: pd.DataFrame) -> str:
    ts = time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime())
    header = (
        f"<b>Scanner MEXC Perps</b>\n"
        f"Quando: {html_escape(ts)}\n"
        f"TF: <code>{html_escape(timeframe)}</code> | MA: <code>{html_escape(MA_TYPE)} {SHORT_MA}/{LONG_MA}</code> | Top: <code>{TOP_PERPS}</code>\n"
        f"Ordem: menor distância entre MAs → maior\n"
    )

    out = [header, f"\n<b>ALTA</b> (Top {TELEGRAM_TOP_N})"]
    if bullish_df is None or bullish_df.empty:
        out.append("• (vazio)")
        return "\n".join(out)[:3900]

    for _, r in bullish_df.head(TELEGRAM_TOP_N).iterrows():
        sym = html_escape(str(r.get("symbol", "")))
        link = str(r.get("link", ""))
        vol = html_escape(str(r.get("volume_diario", "")))

        dist = r.get("ma_dist_pct", "")
        try:
            dist_str = f"{float(dist):.3f}%"
        except Exception:
            dist_str = html_escape(str(dist))

        out.append(f'• <a href="{link}">{sym}</a> | vol {vol} | dist {dist_str}')

    return "\n".join(out)[:3900]


def main():
    print(
        f"[info] MEXC perps | TFs={TIMEFRAMES} | MA={MA_TYPE} {SHORT_MA}/{LONG_MA} | TOP={TOP_PERPS} | VOLUME_MODE={VOLUME_MODE}"
    )

    symbols, turnover_map = get_top_usdt_perps_and_turnover(TOP_PERPS)
    print(f"[info] símbolos selecionados: {len(symbols)}")

    for tf in TIMEFRAMES:
        if DEBUG:
            print(f"[info] processando timeframe: {tf}")

        results = []
        for sym in symbols:
            try:
                df = fetch_ohlcv(sym, tf, OHLCV_LIMIT)
                if len(df) < LONG_MA + 5:
                    continue

                close = df["close"]
                ma_s = calc_ma(close, SHORT_MA, MA_TYPE)
                ma_l = calc_ma(close, LONG_MA, MA_TYPE)

                last_close = float(close.iloc[-1])
                last_ma_s = float(ma_s.iloc[-1])
                last_ma_l = float(ma_l.iloc[-1])

                if pd.isna(last_ma_s) or pd.isna(last_ma_l) or last_ma_l == 0:
                    continue

                ma_dist_pct = (last_ma_s - last_ma_l) / last_ma_l * 100.0

                # SOMENTE tendência de ALTA
                bullish = (last_ma_s > last_ma_l) and (last_close > last_ma_s) and (last_close > last_ma_l)
                if not bullish:
                    continue

                turnover_24h_usdt = float(turnover_map.get(sym, 0.0))

                results.append(
                    {
                        "symbol": sym,
                        "link": symbol_to_link(sym),
                        "trend": "ALTA",
                        "close": last_close,
                        "volume_diario": format_volume(turnover_24h_usdt),
                        "ma_dist_pct": float(ma_dist_pct),
                    }
                )

            except Exception:
                continue

        out = pd.DataFrame(results, columns=["symbol", "link", "trend", "close", "volume_diario", "ma_dist_pct"])

        bullish_df = (
            out.assign(abs_dist=lambda d: d["ma_dist_pct"].abs())
            .sort_values("abs_dist", ascending=True)
            .drop(columns=["abs_dist"])
        )

        save_outputs_bullish(tf, out, bullish_df)

        # Telegram: 1 mensagem por timeframe (somente ALTA)
        try:
            if TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID:
                msg = build_telegram_html_bullish(tf, bullish_df)
                telegram_send_html(msg)
        except Exception as e:
            print("[warn] Telegram falhou:", repr(e))


if __name__ == "__main__":
    main()
