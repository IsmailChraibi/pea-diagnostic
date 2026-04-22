"""
PEA Price Feed Diagnostic v2
=============================

Round 2 — informed by Round 1 results:
  • Yahoo: 429 → add retry with backoff
  • Stooq: wrong symbol → try multiple suffixes (.fr, .pa, no suffix)
  • Boursorama: page loads but regex misses → try 7 different patterns,
    plus offer raw HTML download so we can inspect the actual response
"""

import re
import time
from collections import Counter
from datetime import datetime, timezone

import pandas as pd
import requests
import streamlit as st

st.set_page_config(page_title="PEA Diagnostic v2", page_icon="🩺", layout="wide")

TICKERS = {
    "EPA:CW8":   {"yahoo": "CW8.PA",   "boursorama": "1rTCW8",   "name": "Amundi MSCI World"},
    "EPA:C40":   {"yahoo": "C40.PA",   "boursorama": "1rTC40",   "name": "Amundi CAC 40"},
    "EPA:PE500": {"yahoo": "PE500.PA", "boursorama": "1rTPE500", "name": "Amundi PEA S&P 500"},
}

BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Upgrade-Insecure-Requests": "1",
}
JSON_HEADERS = {
    "User-Agent": BROWSER_HEADERS["User-Agent"],
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Origin": "https://finance.yahoo.com",
    "Referer": "https://finance.yahoo.com/",
}
TIMEOUT = 12


# =====================================================================
# Boursorama — 7 regex strategies
# =====================================================================

BOURSORAMA_PATTERNS = [
    ("Ouverture théorique (period decimal)",
     re.compile(r'Ouverture\s+th[eé]orique[^\d]{0,30}([0-9]+\.[0-9]+)', re.IGNORECASE)),
    ("Ouverture théorique (any decimal)",
     re.compile(r'Ouverture\s+th[eé]orique[^\d]{0,30}([0-9]+[.,][0-9]+)', re.IGNORECASE)),
    ("data-ist-last attribute",
     re.compile(r'data-ist-last\s*=\s*["\']([0-9]+(?:[.,][0-9]+)?)["\']', re.IGNORECASE)),
    ("data-last attribute",
     re.compile(r'data-last\s*=\s*["\']([0-9]+(?:[.,][0-9]+)?)["\']', re.IGNORECASE)),
    ("Number followed by EUR",
     re.compile(r'([0-9]{1,5}[.,][0-9]{2,6})\s*EUR', re.IGNORECASE)),
    ("Number before 'Valeur liquidative'",
     re.compile(r'([0-9]{1,5}[.,][0-9]{2,6})[^a-zA-Z\d]{0,30}Valeur\s+liquidative', re.IGNORECASE)),
    ("c-instrument--last span",
     re.compile(r'c-instrument--last[^>]*>\s*([0-9]+[.,][0-9]+)', re.IGNORECASE)),
]


def _parse_num(s):
    try:
        return float(s.replace(",", "."))
    except (ValueError, AttributeError):
        return None


def fetch_boursorama_full(ticker_key):
    slug = TICKERS[ticker_key]["boursorama"]
    url = f"https://www.boursorama.com/bourse/trackers/cours/{slug}/"
    try:
        r = requests.get(url, timeout=TIMEOUT, headers=BROWSER_HEADERS)
        if r.status_code != 200:
            return {
                "ok": False, "error": f"HTTP {r.status_code}",
                "html": r.text[:50000], "html_size": len(r.text), "matches": [],
                "prices_found": [],
            }
        html = r.text
        matches = []
        prices_found = []
        for name, pat in BOURSORAMA_PATTERNS:
            ms = pat.findall(html)
            if ms:
                vals = [_parse_num(s) for s in ms[:8]]
                vals = [v for v in vals if v is not None and v > 0]
                matches.append({"pattern": name, "raw": ms[:8], "parsed": vals})
                prices_found.extend(vals)
            else:
                matches.append({"pattern": name, "raw": [], "parsed": []})
        return {
            "ok": len(prices_found) > 0,
            "url": url, "html_size": len(html), "html": html,
            "matches": matches, "prices_found": prices_found,
        }
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}",
                "html": "", "matches": [], "html_size": 0, "prices_found": []}


# =====================================================================
# Yahoo with retry-and-backoff
# =====================================================================

def fetch_yahoo_with_retry(ticker_key, max_retries=3):
    sym = TICKERS[ticker_key]["yahoo"]
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}"
    attempts = []
    for attempt in range(max_retries):
        try:
            r = requests.get(url, timeout=TIMEOUT, headers=JSON_HEADERS,
                             params={"interval": "1d", "range": "5d"})
            attempts.append({"attempt": attempt + 1, "status": r.status_code,
                             "size": len(r.text)})
            if r.status_code == 200:
                data = r.json()
                result = (data.get("chart") or {}).get("result") or []
                if result:
                    meta = result[0].get("meta") or {}
                    price = meta.get("regularMarketPrice")
                    ts_unix = meta.get("regularMarketTime")
                    if price is not None:
                        as_of = datetime.fromtimestamp(int(ts_unix), tz=timezone.utc) \
                            if ts_unix else None
                        return {"ok": True, "price": float(price),
                                "as_of": as_of.strftime("%Y-%m-%d %H:%M UTC") if as_of else "?",
                                "currency": meta.get("currency", "?"),
                                "attempts": attempts}
            elif r.status_code == 429:
                time.sleep(2 ** attempt)
                continue
            else:
                return {"ok": False, "error": f"HTTP {r.status_code}",
                        "attempts": attempts, "body": r.text[:300]}
        except Exception as e:
            attempts.append({"attempt": attempt + 1, "error": str(e)})
            time.sleep(1)
    return {"ok": False, "error": "all retries failed (likely persistent 429)",
            "attempts": attempts}


# =====================================================================
# Stooq with multiple symbol formats
# =====================================================================

STOOQ_SUFFIXES = [".fr", ".pa", "", ".de", ".uk"]


def fetch_stooq_all_suffixes(ticker_key):
    base = TICKERS[ticker_key]["yahoo"].split(".")[0].lower()
    results = []
    for suffix in STOOQ_SUFFIXES:
        sym = f"{base}{suffix}"
        url = f"https://stooq.com/q/l/?s={sym}&f=sd2t2ohlcv&h&e=csv"
        try:
            r = requests.get(url, timeout=TIMEOUT, headers=BROWSER_HEADERS)
            if r.status_code != 200:
                results.append({"symbol": sym, "ok": False, "error": f"HTTP {r.status_code}"})
                continue
            lines = r.text.strip().split("\n")
            if len(lines) < 2:
                results.append({"symbol": sym, "ok": False, "error": "no rows"})
                continue
            headers = [h.strip().lower() for h in lines[0].split(",")]
            values = [v.strip() for v in lines[1].split(",")]
            row = dict(zip(headers, values))
            close = row.get("close", "N/D")
            if close in ("N/D", "", "0", "-"):
                results.append({"symbol": sym, "ok": False,
                                "error": "close=N/D (ticker not found)",
                                "raw": str(row)[:120]})
            else:
                results.append({"symbol": sym, "ok": True,
                                "price": float(close), "date": row.get("date"),
                                "raw": str(row)[:120]})
        except Exception as e:
            results.append({"symbol": sym, "ok": False, "error": str(e)})
    return results


# =====================================================================
# UI
# =====================================================================

st.title("🩺 PEA Diagnostic v2")
st.caption(
    "Round 2 — 7 Boursorama regex patterns + raw HTML download, "
    "Yahoo with retry/backoff, Stooq with 5 symbol suffixes."
)

ticker = st.selectbox(
    "Test ticker", options=list(TICKERS.keys()),
    format_func=lambda t: f"{t} — {TICKERS[t]['name']}"
)

if st.button("🔍 Run full diagnostic", type="primary"):
    # Boursorama
    st.markdown("---")
    st.subheader("🇫🇷 Boursorama (most likely to work)")
    with st.spinner("Fetching Boursorama page…"):
        bres = fetch_boursorama_full(ticker)

    if bres.get("ok"):
        st.success(f"✅ Page fetched ({bres['html_size']:,} chars). "
                   f"Total candidate prices found: **{len(bres['prices_found'])}**")
        sensible = [p for p in bres['prices_found'] if 5 <= p <= 5000]
        if sensible:
            most_common = Counter(sensible).most_common(5)
            st.info("**Most-frequent sensible price candidates:**  "
                    + "  •  ".join(f"€{p:.4f} ({n}×)" for p, n in most_common))
            st.caption("The price that appears most often is almost certainly the live price.")
    else:
        st.error(f"❌ {bres.get('error', 'failed')}")

    with st.expander("Per-pattern results", expanded=True):
        for m in bres.get("matches", []):
            if m["parsed"]:
                st.markdown(f"✅ **{m['pattern']}** → {m['parsed']}")
            elif m["raw"]:
                st.markdown(f"⚠️ {m['pattern']} → matched `{m['raw']}` but couldn't parse")
            else:
                st.markdown(f"❌ {m['pattern']}")

    if bres.get("html"):
        st.download_button(
            "📥 Download raw Boursorama HTML",
            data=bres["html"],
            file_name=f"boursorama_{ticker.replace(':', '_')}.html",
            mime="text/html",
            help="If patterns missed, download this and send it back so I can inspect.",
        )

    # Yahoo
    st.markdown("---")
    st.subheader("🟣 Yahoo Finance (with retry/backoff)")
    with st.spinner("Trying Yahoo with up to 3 retries…"):
        yres = fetch_yahoo_with_retry(ticker)
    if yres.get("ok"):
        st.success(f"✅ €{yres['price']:.2f} (as of {yres['as_of']}, currency {yres['currency']})")
    else:
        st.error(f"❌ {yres.get('error', 'failed')}")
    with st.expander("Attempt log"):
        st.json(yres.get("attempts", []))

    # Stooq
    st.markdown("---")
    st.subheader("🟠 Stooq (multiple symbol formats)")
    with st.spinner("Trying Stooq with .fr, .pa, no-suffix, .de, .uk…"):
        sres = fetch_stooq_all_suffixes(ticker)
    df = pd.DataFrame(sres)
    st.dataframe(df, hide_index=True, use_container_width=True)
    any_ok = any(r.get("ok") for r in sres)
    if any_ok:
        st.success("✅ Found at least one working Stooq suffix.")
    else:
        st.error("❌ No Stooq suffix works for this ticker.")

    # Summary
    st.markdown("---")
    st.subheader("📊 Summary")
    c1, c2, c3 = st.columns(3)
    c1.metric("Boursorama", "🟢" if bres.get("ok") else "🔴",
              f"{len(bres.get('prices_found', []))} prices")
    c2.metric("Yahoo", "🟢" if yres.get("ok") else "🔴",
              f"{len(yres.get('attempts', []))} attempts")
    c3.metric("Stooq", "🟢" if any_ok else "🔴",
              f"{sum(1 for r in sres if r.get('ok'))} suffixes")

else:
    st.info("Click **Run full diagnostic** above. Takes ~15 seconds.")
