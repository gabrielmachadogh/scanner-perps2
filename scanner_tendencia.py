import os
import time
import requests
import pandas as pd

BASE_URL = os.getenv("MEXC_CONTRACT_BASE_URL", "https://contract.mexc.com/api/v1")

# Link do par (pode trocar por deep link via env)
MEXC_LINK_TEMPLATE = os.getenv("MEXC_LINK_TEMPLATE", "https://futures.mexc.com/exchange/{symbol}")

# Timeframes (multi)
TIMEFRAMES = [t.strip() for t in os.getenv("TIMEFRAMES", "2h,4h,1d").split(",") if t.strip()]

# Filtro 1 (o que já existia): MA curta vs MA longa
SHORT_MA = int(os.getenv("SHORT_MA", "10"))
LONG_MA = int(os.getenv("LONG_MA", "100"))
MA_TYPE = os.getenv("MA_TYPE", "sma").lower()  # ema|sma

# Filtro 2 (novo): MA20 apontada pra cima + confirmação (N fechamentos acima)
MA20_PERIOD = int(os.getenv("MA20_PERIOD", "20"))
MA20_SLOPE_LOOKBACK = int(os.getenv("MA20_SLOPE_LOOKBACK", "1"))  # MA20[-1] vs MA20[-1-lookback]
MA20_CONFIRM_BARS = int(os.getenv("MA20_CONFIRM_BARS", "3"))       # <<< opção 2: 3 candles acima da MA20

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


def build_telegram_html_filter1(timeframe: str, bullish_df: pd.DataFrame) -> str:
    ts = time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime())
    header = (
        f"<b>Scanner MEXC Perps</b>\n"
        f"Quando: {html_escape(ts)}\n"
        f"TF: <code>{html_escape(timeframe)}</code>\n"
        f"Filtro 1: <code>{html_escape(MA_TYPE)} {SHORT_MA}/{LONG_MA}</code> e preço acima das duas\n"
        f"Ordem: menor distância entre MAs → maior\n"
    )

    out = [header, f"\n<b>FILTRO 1 (ALTA MA{SHORT_MA}/{LONG_MA})</b> (Top {TELEGRAM_TOP_N})"]
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


def build_telegram_html_filter2(timeframe: str, ma20_df: pd.DataFrame) -> str:
    ts = time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime())
    header = (
        f"<b>Scanner MEXC Perps</b>\n"
        f"Quando: {html_escape(ts)}\n"
        f"TF: <code>{html_escape(timeframe)}</code>\n"
        f"Filtro 2: MA{MA20_PERIOD} apontando pra cima e fechamento acima da MA{MA20_PERIOD} por "
        f"{MA20_CONFIRM_BARS} candles seguidos\n"
        f"Ordem: menor distância do preço para MA{MA20_PERIOD} → maior\n"
    )

    out = [header, f"\n<b>FILTRO 2 (MA{MA20_PERIOD} ↑ + {MA20_CONFIRM_BARS} fechamentos acima)</b> (Top {TELEGRAM_TOP_N})"]
    if ma20_df is None or ma20_df.empty:
        out.append("• (vazio)")
        return "\n".join(out)[:3900]

    for _, r in ma20_df.head(TELEGRAM_TOP_N).iterrows():
        sym = html_escape(str(r.get("symbol", "")))
        link = str(r.get("link", ""))
        vol = html_escape(str(r.get("volume_diario", "")))

        dist = r.get("dist_ma20_pct", "")
        try:
            dist_str = f"{float(dist):.3f}%"
        except Exception:
            dist_str = html_escape(str(dist))

        out.append(f'• <a href="{link}">{sym}</a> | vol {vol} | dist {dist_str}')

    return "\n".join(out)[:3900]


def main():
    print(
        f"[info] MEXC perps | TFs={TIMEFRAMES} | "
        f"Filtro1={MA_TYPE} {SHORT_MA}/{LONG_MA} | "
        f"Filtro2=MA{MA20_PERIOD} slope_lb={MA20_SLOPE_LOOKBACK} confirm={MA20_CONFIRM_BARS} | "
        f"TOP={TOP_PERPS} | VOLUME_MODE={VOLUME_MODE}"
    )

    symbols, turnover_map = get_top_usdt_perps_and_turnover(TOP_PERPS)
    print(f"[info] símbolos selecionados (por turnover 24h): {len(symbols)}")

    for tf in TIMEFRAMES:
        if DEBUG:
            print(f"[info] processando timeframe: {tf}")

        results_f1 = []  # filtro existente (ALTA MA curta/longa)
        results_f2 = []  # filtro novo (MA20 subindo + 3 fechamentos acima)

        for sym in symbols:
            try:
                df = fetch_ohlcv(sym, tf, OHLCV_LIMIT)
                if df is None or df.empty:
                    continue

                close = df["close"]

                # histórico mínimo com folga para:
                # - MA longa
                # - MA20 + lookback de inclinação
                # - confirmação de N candles acima
                required_len = max(
                    LONG_MA + 5,
                    MA20_PERIOD + MA20_SLOPE_LOOKBACK + MA20_CONFIRM_BARS + 5,
                )
                if len(close) < required_len:
                    continue

                last_close = float(close.iloc[-1])
                turnover_24h_usdt = float(turnover_map.get(sym, 0.0))

                # -------------------------
                # Filtro 2: MA20 ↑ + confirmação (N fechamentos acima da MA20)
                # -------------------------
                ma20 = calc_ma(close, MA20_PERIOD, MA_TYPE)

                last_ma20 = ma20.iloc[-1]
                prev_ma20 = ma20.iloc[-1 - MA20_SLOPE_LOOKBACK]

                if pd.notna(last_ma20) and pd.notna(prev_ma20) and float(last_ma20) != 0:
                    ma20_up = float(last_ma20) > float(prev_ma20)

                    last_n_close = close.iloc[-MA20_CONFIRM_BARS:]
                    last_n_ma20 = ma20.iloc[-MA20_CONFIRM_BARS:]

                    # Confirmação: os últimos N fechamentos precisam estar acima da MA20
                    above_for_n = (
                        pd.notna(last_n_ma20).all()
                        and (last_n_close.to_numpy() > last_n_ma20.to_numpy()).all()
                    )

                    if ma20_up and above_for_n:
                        dist_ma20_pct = (last_close - float(last_ma20)) / float(last_ma20) * 100.0
                        results_f2.append(
                            {
                                "symbol": sym,
                                "link": symbol_to_link(sym),
                                "close": last_close,
                                "volume_diario": format_volume(turnover_24h_usdt),
                                "ma20": float(last_ma20),
                                "dist_ma20_pct": float(dist_ma20_pct),
                            }
                        )

                # -------------------------
                # Filtro 1 (já existia): MA curta vs MA longa + preço acima das duas
                # -------------------------
                ma_s = calc_ma(close, SHORT_MA, MA_TYPE)
                ma_l = calc_ma(close, LONG_MA, MA_TYPE)

                last_ma_s = ma_s.iloc[-1]
                last_ma_l = ma_l.iloc[-1]

                if pd.isna(last_ma_s) or pd.isna(last_ma_l) or float(last_ma_l) == 0:
                    continue

                ma_dist_pct = (float(last_ma_s) - float(last_ma_l)) / float(last_ma_l) * 100.0
                bullish = (float(last_ma_s) > float(last_ma_l)) and (last_close > float(last_ma_s)) and (last_close > float(last_ma_l))

                if bullish:
                    results_f1.append(
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

        # DataFrames + ordenação
        f1 = pd.DataFrame(results_f1, columns=["symbol", "link", "trend", "close", "volume_diario", "ma_dist_pct"])
        f1 = (
            f1.assign(abs_dist=lambda d: d["ma_dist_pct"].abs())
              .sort_values("abs_dist", ascending=True)
              .drop(columns=["abs_dist"])
        )

        f2 = pd.DataFrame(results_f2, columns=["symbol", "link", "close", "volume_diario", "ma20", "dist_ma20_pct"])
        f2 = (
            f2.assign(abs_dist=lambda d: d["dist_ma20_pct"].abs())
              .sort_values("abs_dist", ascending=True)
              .drop(columns=["abs_dist"])
        )

        # Telegram: manda DUAS mensagens separadas (filtro 1 e filtro 2) por timeframe
        try:
            if TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID:
                msg1 = build_telegram_html_filter1(tf, f1)
                msg2 = build_telegram_html_filter2(tf, f2)
                telegram_send_html(msg1)
                telegram_send_html(msg2)
        except Exception as e:
            print("[warn] Telegram falhou:", repr(e))


if __name__ == "__main__":
    main()
    
