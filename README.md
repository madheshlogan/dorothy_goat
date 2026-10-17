# Decoy Customer ("Dorothy") – ideathon prototype

An AI honeypot customer: Dorothy talks to scammers, the scammer's bank account is extracted,
an analyst approves it, and a real customer's payment to that account is stopped.
**Fake data only.**

## Setup (first time)
1. Install Python 3.11+ (tick "Add Python to PATH" on Windows), Git, and Chrome or Edge.
2. In a terminal:
   ```
   git clone https://github.com/madheshlogan/dorothy_goat.git
   cd dorothy_goat
   python -m venv venv
   venv\Scripts\activate          (Mac: source venv/bin/activate)
   python -m pip install -r requirements.txt
   ```
3. Copy `.env.example` to `.env` and paste your free Gemini key from https://aistudio.google.com/apikey.
   Leave the key empty or set `OFFLINE_MODE=true` to use the built-in offline Dorothy.
4. Check Gemini: `python check_gemini.py` (line 5 should say SUCCESS).

## Run
```
uvicorn main:app --reload
```
Open http://127.0.0.1:8000 in Chrome or Edge.

## Demo (5 steps)
1. **Decoy call**: Start call, pick a voice, Start microphone, and talk as "David from the fraud department".
   Give Dorothy a bank name, routing number and account number.
2. **End call + extract**: the scammer's details are pulled out.
3. **Analyst review**: approve the bank account.
4. **Margaret's app**: add the same account and withdraw $48,000.
5. The transfer is held, the safety chat appears, and the trusted contact is notified.

Backup: "Load sample scam call" runs the whole flow without the microphone. "Reset demo" starts over.

## Tests
```
python -m pytest
```

## Notes
- Gemini errors are printed in the terminal as `[gemini] ...`; Dorothy then answers in offline mode for that turn.
- Free-tier limits are shown at https://aistudio.google.com/rate-limit (about one request per Dorothy reply).
- The microphone only works on `127.0.0.1` / `localhost`.
