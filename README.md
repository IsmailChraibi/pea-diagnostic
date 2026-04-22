# PEA Price Feed Diagnostic

A tiny, single-purpose Streamlit app that tests whether live ETF prices can
be fetched from **Streamlit Cloud's network** — which is our blocker before
building the full dashboard.

## Why this exists

The previous full app (v3.x) crashed with cryptic errors, and we never got
visibility into which data source was actually failing. This app does one
thing and one thing only: fetch CW8's current price (or another test ticker)
from four different providers, and show the result for each.

Once we know which source works, we build the full dashboard around it.

## How to deploy (fresh, 5 minutes)

### 1. Create a new GitHub repo

- Go to [github.com/new](https://github.com/new)
- Name: `pea-diagnostic` (or anything)
- Visibility: **Public** is fine — no secrets, no personal data in this app
- Leave everything else unchecked, click **Create repository**

### 2. Upload the two files

On the new repo's page, click **"uploading an existing file"** (in the welcome text).
Drag in **`app.py`** and **`requirements.txt`**. Leave the commit message
default, click **Commit changes**.

Your repo should now show a flat list with just those two files at the root.

### 3. Deploy on Streamlit Cloud

- Go to [share.streamlit.io](https://share.streamlit.io)
- Click **Create app** → **Deploy a public app from GitHub**
- Repository: select your new `pea-diagnostic` repo
- Branch: `main`
- Main file path: `app.py`
- App URL: pick anything (e.g. `pea-diag`)
- Click **Deploy**

First build takes ~90 seconds. When it finishes, the app loads.

### 4. Run the diagnostic

1. Pick a ticker (default CW8 is fine)
2. Click **🔍 Run diagnostic**
3. Wait ~10 seconds
4. Read the results

## What to do with the results

**If 1+ sources show 🟢**, that's our answer. Screenshot the page, paste it
in the conversation, and we build the full dashboard around whichever source
worked.

**If all 4 show 🔴**, Streamlit Cloud is fully blocked from these services.
Screenshot and paste — we'll switch to a browser-side fetching approach
(JavaScript in the user's browser rather than Python on the server).

## Files

- `app.py` — the diagnostic (~230 lines)
- `requirements.txt` — 3 deps: streamlit, requests, pandas

That's it. No secrets, no transaction data, no Google Sheets. Just a
connectivity test.
