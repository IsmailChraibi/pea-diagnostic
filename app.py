"""
PEA Price Feed Diagnostic
==========================
A single-purpose Streamlit app that tests whether we can fetch live prices
for Euronext Paris ETFs from four different free data sources.

Goal: answer ONE question — which source (if any) works reliably from
Streamlit Cloud's network?

Once we know, Phase 2 rebuilds the full dashboard using that source.
"""

from datetime import datetime, timezone
import requests
import streamlit as st

st.set_page_config(page_title="PEA Price Feed Diagnostic", page_icon="🩺", layout="wide")

# Test tickers covering different levels of data-provider coverage
TICKERS = {
    "EPA:CW8":   {"yahoo": "CW8.PA",   "stooq": "cw8.fr",   "boursorama": "1rTCW8",   "name": "Amundi MSCI World"},
    "EPA:C40":   {"yahoo": "C40.PA",   "stooq": "c40.fr",   "boursorama": "1rTC40",   "name": "Amundi CAC 40"},
    "EPA:PE500": {"yahoo": "PE500.PA", "stooq": "pe500.fr", "boursorama": "1rTPE500", "name": "Amundi PEA S&P 500"},
    "EPA:PAEEM": {"yahoo": "PAEEM.PA", "stooq": "paeem.fr", "boursorama": "1rTPAEEM", "name": "Amundi PEA EM"},
}

# Full browser-like headers to avoid bot detection
BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9,fr;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
}
JSON_HEADERS = {
    "User-Agent": BROWSER_HEADERS["User-Agent"],
    "Accept": "application/json",
    "Origin": "https://finance.yahoo.com",
    "Referer": "https://finance.yahoo.com/",
}

TIMEOUT = 10


# =====================================================================
# Four separate fetch functions — each one reports success or the exact error
# =====================================================================

def fetch_yahoo(ticker_key):
    """Yahoo Finance JSON chart API."""
    sym = TICKERS[ticker_key]["yahoo"]
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}"
    try:
        r = requests.get(url, timeout=TIMEOUT, headers=JSON_HEADERS,
                         params={"interval": "1d", "range": "5d"})
        if r.status_code != 200:
            return {"ok": False, "error": f"HTTP {r.status_code}", "detail": r.text[:200]}
        data = r.json()
        result = (data.get("chart") or {}).get("result") or []
        if not result:
            return {"ok": False, "error": "empty response",
                    "detail": str(data)[:200]}
        meta = result[0].get("meta") or {}
        price = meta.get("regularMarketPrice")
        ts_unix = meta.get("regularMarketTime")
        if price is None:
            return {"ok": False, "error": "no price in response",
                    "detail": str(meta)[:200]}
        as_of = datetime.fromtimestamp(int(ts_unix), tz=timezone.utc) \
            if ts_unix else None
        return {
            "ok": True, "price": float(price),
            "as_of": as_of.strftime("%Y-%m-%d %H:%M UTC") if as_of else "unknown",
            "currency": meta.get("currency", "?"),
        }
    except requests.exceptions.Timeout:
        return {"ok": False, "error": "timeout after 10s", "detail": ""}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}", "detail": str(e)[:200]}


def fetch_stooq(ticker_key):
    """Stooq CSV API."""
    sym = TICKERS[ticker_key]["stooq"]
    url = f"https://stooq.com/q/l/?s={sym}&f=sd2t2ohlcv&h&e=csv"
    try:
        r = requests.get(url, timeout=TIMEOUT, headers=BROWSER_HEADERS)
        if r.status_code != 200:
            return {"ok": False, "error": f"HTTP {r.status_code}",
                    "detail": r.text[:200]}
        lines = r.text.strip().split("\n")
        if len(lines) < 2:
            return {"ok": False, "error": "no data rows",
                    "detail": r.text[:200]}
        headers = [h.strip().lower() for h in lines[0].split(",")]
        values = [v.strip() for v in lines[1].split(",")]
        row = dict(zip(headers, values))
        close = row.get("close", "N/D")
        if close in ("N/D", "", "0", "-"):
            return {"ok": False, "error": f"ticker not found or no price",
                    "detail": str(row)[:200]}
        return {
            "ok": True, "price": float(close),
            "as_of": f"{row.get('date', '?')} {row.get('time', '')}",
            "currency": "EUR",
        }
    except requests.exceptions.Timeout:
        return {"ok": False, "error": "timeout after 10s", "detail": ""}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}", "detail": str(e)[:200]}


def fetch_boursorama(ticker_key):
    """Boursorama page scrape."""
    import re
    slug = TICKERS[ticker_key]["boursorama"]
    url = f"https://www.boursorama.com/bourse/trackers/cours/{slug}/"
    try:
        r = requests.get(url, timeout=TIMEOUT, headers=BROWSER_HEADERS)
        if r.status_code != 200:
            return {"ok": False, "error": f"HTTP {r.status_code}",
                    "detail": f"first 200 chars: {r.text[:200]}"}
        html = r.text
        # Try two patterns
        patterns = [
            r'data-ist-last=["\']([0-9]+(?:[.,][0-9]+)?)["\']',
            r'([0-9]{1,5}[.,][0-9]{2,6})\s*EUR',
        ]
        for pat in patterns:
            m = re.search(pat, html, re.IGNORECASE)
            if m:
                p = float(m.group(1).replace(",", "."))
                if p > 0:
                    return {
                        "ok": True, "price": p, "as_of": "from page HTML",
                        "currency": "EUR",
                    }
        return {"ok": False, "error": "no price found in HTML",
                "detail": f"HTML length: {len(html)} chars. First 300 chars: {html[:300]}"}
    except requests.exceptions.Timeout:
        return {"ok": False, "error": "timeout after 10s", "detail": ""}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}", "detail": str(e)[:200]}


def fetch_financial_modeling_prep(ticker_key):
    """Financial Modeling Prep free tier (no key needed for basic quote,
    but requires a demo key for most endpoints). Using their demo key
    which works for a limited number of tickers."""
    sym = TICKERS[ticker_key]["yahoo"]  # FMP uses same suffix as Yahoo for Euronext
    url = f"https://financialmodelingprep.com/api/v3/quote/{sym}"
    try:
        r = requests.get(url, timeout=TIMEOUT, headers=JSON_HEADERS,
                         params={"apikey": "demo"})
        if r.status_code == 401:
            return {"ok": False, "error": "401 — API key required",
                    "detail": "FMP requires signup for free API key"}
        if r.status_code != 200:
            return {"ok": False, "error": f"HTTP {r.status_code}",
                    "detail": r.text[:200]}
        data = r.json()
        if not data or not isinstance(data, list):
            return {"ok": False, "error": "empty response", "detail": str(data)[:200]}
        quote = data[0]
        price = quote.get("price")
        if price is None:
            return {"ok": False, "error": "no price field", "detail": str(quote)[:200]}
        return {
            "ok": True, "price": float(price),
            "as_of": quote.get("timestamp", "?"),
            "currency": "EUR",
        }
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}", "detail": str(e)[:200]}


# =====================================================================
# UI
# =====================================================================

st.title("🩺 PEA Price Feed Diagnostic")
st.caption(
    "This app tests whether Streamlit Cloud can reach each of four free price "
    "data sources. Goal: identify which source works so we can build the full "
    "dashboard around it."
)

st.info(
    "**How to read results:** 🟢 means the source works. 🔴 means it failed — "
    "read the error to see if it's blocked (403), rate-limited (429), timing "
    "out, or returning bad data."
)

# Pick a ticker
ticker = st.selectbox(
    "Test ticker",
    options=list(TICKERS.keys()),
    format_func=lambda t: f"{t} — {TICKERS[t]['name']}",
    help="Different tickers test different levels of data provider coverage. "
         "CW8 is a large, well-known ETF (best coverage). PAEEM is a small Amundi "
         "PEA-specific ETF (worst coverage).",
)

# Run all four tests
if st.button("🔍 Run diagnostic", type="primary"):
    st.markdown("---")
    with st.spinner(f"Testing all sources for {ticker}…"):
        results = {
            "Yahoo Finance JSON": fetch_yahoo(ticker),
            "Stooq CSV":          fetch_stooq(ticker),
            "Boursorama scrape":  fetch_boursorama(ticker),
            "Financial Modeling Prep": fetch_financial_modeling_prep(ticker),
        }

    # Summary — which worked?
    worked = [name for name, r in results.items() if r.get("ok")]
    failed = [name for name, r in results.items() if not r.get("ok")]

    if worked:
        st.success(
            f"**{len(worked)} of {len(results)} sources worked:** "
            f"{', '.join(worked)}"
        )
    else:
        st.error(
            f"❌ **All {len(results)} sources failed.** This means Streamlit "
            "Cloud cannot reach these providers. We'll need a different data strategy."
        )

    st.markdown("---")

    # Detailed per-source results
    for name, r in results.items():
        if r.get("ok"):
            with st.container(border=True):
                st.markdown(f"### 🟢 {name}")
                c1, c2, c3 = st.columns(3)
                c1.metric("Price", f"€{r['price']:,.2f}")
                c2.metric("Currency", r.get("currency", "?"))
                c3.metric("As of", r.get("as_of", "?"))
        else:
            with st.container(border=True):
                st.markdown(f"### 🔴 {name}")
                st.markdown(f"**Error:** `{r.get('error', 'unknown')}`")
                detail = r.get("detail", "")
                if detail:
                    with st.expander("Raw response detail"):
                        st.code(detail[:500], language=None)

    # Summary table
    st.markdown("---")
    st.subheader("Summary")
    import pandas as pd
    summary_df = pd.DataFrame([
        {
            "Source": name,
            "Status": "🟢 OK" if r.get("ok") else "🔴 FAIL",
            "Price": r.get("price") if r.get("ok") else None,
            "Error": r.get("error", "") if not r.get("ok") else "",
        }
        for name, r in results.items()
    ])
    st.dataframe(
        summary_df, hide_index=True, use_container_width=True,
        column_config={"Price": st.column_config.NumberColumn(format="€%.2f")},
    )

    st.markdown("---")
    st.markdown(
        "**Next step:** If any source worked, that's the one we build the full "
        "dashboard around. If all failed, screenshot this page and paste in the "
        "conversation — we'll switch to a different strategy (e.g. browser-side "
        "fetching via `streamlit-javascript`, or a small proxy API)."
    )

else:
    st.markdown(
        "👆 Click **Run diagnostic** to test all four sources.  \n"
        "The test takes about 10-15 seconds."
    )
    st.markdown("---")
    with st.expander("ℹ️ What each source is"):
        st.markdown(
            "- **Yahoo Finance JSON** — `query1.finance.yahoo.com/v8/finance/chart/`. "
            "Direct from Yahoo's chart backend, same data that powers finance.yahoo.com.\n"
            "- **Stooq CSV** — `stooq.com/q/l/?s=...&e=csv`. Polish-Chinese free provider, "
            "~20k tickers including Euronext.\n"
            "- **Boursorama scrape** — HTML scrape of `boursorama.com/bourse/trackers/cours/...`. "
            "French retail broker, always has live French ETF data.\n"
            "- **Financial Modeling Prep** — `financialmodelingprep.com/api/v3/quote/`. "
            "Uses a demo API key; may be rate-limited."
        )
