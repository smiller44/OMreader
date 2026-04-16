# Deal 1-Pager Generator

Upload a multifamily offering memorandum PDF. Get a standardized, editable Word doc 1-pager back in seconds.

## Setup

### 1. Clone this repo
```bash
git clone https://github.com/YOUR_USERNAME/deal-1pager.git
cd deal-1pager
```

### 2. Deploy to Streamlit Community Cloud
1. Push this repo to GitHub (can be private)
2. Go to [share.streamlit.io](https://share.streamlit.io)
3. Sign in with GitHub
4. Click "New app" → select this repo → set main file to `app.py`
5. Deploy

That's it. Share the URL.

### 3. Run locally (optional)
```bash
pip install -r requirements.txt
streamlit run app.py
```

## Usage
1. Paste your Anthropic API key (get one at console.anthropic.com)
2. Upload an OM PDF
3. Click Generate
4. Download the Word doc

## Notes
- All figures are sourced from the OM only — never inferred or calculated
- Fields not stated in the OM appear as "Not stated"
- The Word doc is fully editable — your boss can add notes, tweak bullets, forward it
- Each deal costs roughly $0.10–0.20 in API usage
- OMs are not stored anywhere — processed in memory and discarded
