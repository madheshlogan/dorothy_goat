# Multiple decoy personas

The Decoy Call screen now lets you pick who answers the phone. Dorothy is still the default.

| Persona | Who | Attracts | Decoy data (fake) |
|---|---|---|---|
| **Dorothy Miller**, 68 | Retired school teacher, Ohio. Confused and slow, mentions her late husband Harold. | Bank and government impersonation | acct ending 4417, `dorothy.miller.decoy1@example.com`, DOB 03/14/1958 |
| **Robert Chen**, 58 | Retired IT manager, California. Tech-savvy but worried, asks for ticket/case IDs, old Dell laptop. | Tech support, investment, crypto | acct ending 7823, `robert.chen.decoy2@example.com`, DOB 07/22/1966 |
| **Sandra Williams**, 52 | Flower shop owner, Texas. Busy, stressed, "my accountant", worried about audits. | Business email compromise, IRS, invoice fraud | acct ending 2291, `sandra.williams.decoy3@example.com`, DOB 11/05/1972 |

All data is fake (`example.com` emails, invented DOBs). Every persona works with no Gemini key.

> **DOB note:** your spec listed Dorothy's DOB as 03/14/1956, but the existing code and tests use 03/14/1958 (which matches her age of 68), so I kept 1958. To change it, edit `decoy.dob` for Dorothy in `personas.py` and the assertion in `tests/test_offline_dorothy.py`.

## What changed

| File | Change |
|---|---|
| `personas.py` (new) | The 3 personas: id, name, age, background, personality, Gemini `system_prompt`, `opening_line`, `decoy`, `scam_types_attracted`. Also `leaked_decoy_data()`. |
| `offline_dorothy.py` | `reply(transcript, persona_id=None)`. Same slot-filling logic (bank, routing, account, amount, phone), but each persona has its own wording in `VOICES`. Default is Dorothy, so old calls behave exactly as before. |
| `main.py` | `POST /api/decoy/start` and `/api/decoy/sample` accept an optional `{"persona_id": "..."}` (no body = Dorothy; unknown id = 400). `GET /api/personas` lists them. `/api/decoy/message` reads the persona from the call record and uses its system prompt (Gemini) or voice (offline). 3 sample transcripts: Dorothy = bank, Robert = tech support, Sandra = IRS. |
| `db.py` | `calls.persona_id TEXT DEFAULT 'dorothy'`, plus an automatic migration (checks `PRAGMA table_info`, then `ALTER TABLE`) so existing `.db` files upgrade on startup. |
| `extractor.py` | Leaked-data check covers all three suffixes (4417, 7823, 2291) and all three emails. Suffixes match whole numbers only, so `55782301234` is not a false hit. Offline scam typing now returns `tech_support` / `government` as well as `bank_impersonation`. |
| `templates/decoy.html`, `static/decoy.js`, `static/style.css` | Persona cards (name, age, background, "Attracts: ..."), selected one highlighted, Dorothy default, locked while a call is in progress and unlocked when it ends. The title and voice label follow the persona. Robert gets a male browser voice. |
| `static/analyst.js` | Transcripts show the persona's name. |
| `tests/test_personas.py` (new) | 38 tests (see below). |

**Design note:** the decoy's lines are always stored with `speaker: "Dorothy"` (as a role marker; the extractor, offline engine and UI all key off it) plus a `name` field (e.g. "Robert Chen") used for display. This means nothing downstream had to change.

## How to run

```powershell
cd C:\Users\Administrator\Desktop\dorothy_goat
venv\Scripts\activate
uvicorn main:app --reload
```
Open http://127.0.0.1:8000 in Chrome or Edge. (Existing databases migrate automatically on startup. `.env` can stay `OFFLINE_MODE=true`.)

## How to test

### 1. Automated (about 3 seconds)
```powershell
python -m pytest
```
Expect **51 passed** (13 original + 38 new). The new tests cover: persona data and the exact opening lines, defaulting to Dorothy, slot filling and read-backs for each persona, each persona giving only its own decoy data, characters not leaking each other's wording ("Harold" never appears for Robert/Sandra), 45-word reply cap, extraction for all three, leaked-data detection, API start/message/end/sample per persona, the watchlist being fed, the Gemini prompt using the call's persona, and migrating an old database.

### 2. In the browser
1. Go to http://127.0.0.1:8000/decoy. Three cards appear; **Dorothy** is highlighted.
2. Click **Robert Chen**: the card highlights, the title becomes "Talk to Robert Chen", and the voice list switches to male voices.
3. Click **Start call**: the cards grey out and a "locked" note appears. Robert greets you: *"Hello, Robert Chen speaking."*
4. Type as the scammer: `This is Kevin from tech support, your computer is hacked, you must act today.` Robert should mention his laptop and ask for a case/ticket number.
5. Continue: `Move 25000 dollars to a safe account at Harbor Trust Bank`, then `routing 026009593`, then `account 7741120098`. He reads the numbers back and asks for what's missing.
6. Click **End call + extract**: the extraction appears (scam type `tech_support`) and the cards unlock.
7. Try **Sandra** with: `This is the IRS, you owe back taxes, a warrant will be issued today`. She should say things like "my accountant" and "is this about the business account?"
8. Try **Dorothy**: she keeps her glasses, Harold and "dear" style.
9. Click **Load sample scam call** with each persona selected: you get that persona's sample (bank / tech support / IRS) with extraction.
10. Go to **Analyst**: the calls appear with the persona's name in the transcript, and the bank accounts appear as pending candidates. Approve one, then use **Margaret's app** exactly as before. The hold/fraud flow is unchanged.

### 3. With curl (no browser)
```powershell
curl http://127.0.0.1:8000/api/personas
curl -X POST http://127.0.0.1:8000/api/decoy/start -H "Content-Type: application/json" -d "{\"persona_id\":\"sandra\"}"
curl -X POST http://127.0.0.1:8000/api/decoy/start          # no body -> Dorothy (old behaviour)
curl -X POST http://127.0.0.1:8000/api/decoy/start -H "Content-Type: application/json" -d "{\"persona_id\":\"x\"}"   # -> 400
```
(`curl` here is real curl; in PowerShell 5.1 use `curl.exe`.)

### 4. Check the database migration
```powershell
python -c "import sqlite3; print([r[1] for r in sqlite3.connect('decoy_customer.db').execute('PRAGMA table_info(calls)')])"
```
`persona_id` should be in the list. Old rows have `dorothy`.

### 5. With Gemini (optional)
Set `GEMINI_API_KEY` and `OFFLINE_MODE=false` in `.env`, restart, and run a call per persona. Each call uses that persona's system prompt. If Gemini fails, the terminal prints `[gemini] ...` and that turn falls back to the persona's offline voice. This path is covered by an automated test with a stubbed Gemini but hasn't been run against the real API.

## Adding a fourth persona
1. Append an entry to `PERSONAS` in `personas.py`.
2. Add a voice (copy `ROBERT_VOICE`) to `VOICES` in `offline_dorothy.py`. Its `callback` line must contain "call you back".
3. Add a sample transcript to `SAMPLE_TRANSCRIPTS` in `main.py`.
4. Its suffix and email are picked up by leak detection automatically.
