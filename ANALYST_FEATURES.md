# Analyst features: network graph, PDF reports, same-scammer memory

All three work independently, with no Gemini key, and need no setup beyond `pip install -r requirements.txt` (adds `reportlab`).

## 1. Scammer Network (analyst page)
A graph where each call is a dot and a line joins two calls that share a **routing number, account number or phone number** (phone formats are normalised, so `(202) 555-0147` matches `202-555-0147`). Colour = risk: green < 40, orange 40-70, red > 70. Calls with no links still show as isolated dots. Clicking a dot opens its summary, with buttons for the transcript and the PDF report. Fewer than 2 analysed calls shows "Not enough data... run more calls". The graph uses vis.js from cdnjs, loaded only on the analyst page (it needs internet access).

- API: `GET /api/analyst/network` returns `{nodes:[{id,call_id,label,color,risk_score,summary,...}], edges:[{from,to,label,shared_entity_type}]}`
- Code: `network.py`, `static/analyst.js` (`loadNetwork`), `templates/analyst.html`

## 2. PDF report
Each call in "Call transcripts" has a **Download Report** button (also in the graph's side panel). The PDF has: header, call ID/date/duration/persona, a coloured risk-score box, scam type and is-scam, red flags, entities table, scam script phrases, the full transcript (decoy vs caller), the summary, and the footer. It works for calls with no entities, an empty transcript or no extraction. If `reportlab` is missing, a plain-text `.txt` report with the same content is returned instead.

- API: `GET /api/analyst/report/{call_id}` returns `dorothy_report_<first 8 chars>.pdf` (404 for an unknown call)
- Code: `report.py`

## 3. Same-scammer memory
Calls are matched by the **phone number the scammer gives** (found in earlier calls' extractions).
- **During the call:** once the caller says a number we've seen before, `calls.prior_context` is saved and the persona hints once ("Weren't you the one who called about First Coastal Bank before?"), asks "Was that the same First Coastal Bank you mentioned before?" when a bank/routing/account matches, and adds "You sound familiar..." stall lines. With Gemini, the system prompt gets the same hint.
- **At extraction:** risk +20 per earlier call (capped at +40, max 100), `repeat_caller` red flag, and the summary ends "Repeat caller — previously contacted on YYYY-MM-DD."
- **Analyst page:** a red "⚠ Repeat caller" badge next to the call.
- API: `GET /api/analyst/caller/{phone_number}` returns the call history and the union of banks, routing and account numbers seen for that number.
- Code: `db.py` (`get_calls_by_phone`, `get_prior_entities`, `build_prior_context`, `prior_context` column, auto-migration), `offline_dorothy.reply(..., prior_context=)`, `extractor.extract(..., prior_context=)`, `main._prior_context_for`.
- Only calls that have been **extracted** count as history (a number is only known after extraction).

## How to test
```powershell
python -m pytest                 # 95 tests; new ones are in tests/test_features.py
uvicorn main:app --reload
```
1. Decoy page: **Load sample scam call** twice. Open **Analyst**: the network shows two linked dots (account, phone and routing edges), and the second call has the "⚠ Repeat caller" badge with risk +20.
2. Load the sample a third time: risk +40. A fourth stays at +40.
3. Click a dot: the summary appears at the right. Click **Download Report** (graph panel or call list) and open the PDF.
4. Live call: after step 1, start a call as the caller and type `Hi, call me back at 202-555-0147`. Dorothy asks whether you're the one who called about First Coastal Bank before. Then type `Send it to First Coastal Bank` and she asks if it's the same bank as before. End the call: the extraction shows the repeat-caller flag and note.
5. `curl http://127.0.0.1:8000/api/analyst/caller/202-555-0147` shows the history.
6. Robert and Sandra recognise returning callers too, in their own voices.
