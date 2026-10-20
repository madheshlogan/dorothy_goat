"""SQLite persistence and seed data for the fake-data-only demo."""
import json
import os
import re
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

DB_PATH = os.getenv("DECOY_DB_PATH", str(Path(__file__).with_name("decoy_customer.db")))

def now_iso():
    return datetime.now(timezone.utc).isoformat()

def connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn

def init_db():
    with connect() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS calls(
          id TEXT PRIMARY KEY, started_at TEXT NOT NULL, ended_at TEXT,
          transcript_json TEXT NOT NULL DEFAULT '[]', extraction_json TEXT,
          persona_id TEXT NOT NULL DEFAULT 'dorothy', prior_context TEXT
        );
        CREATE TABLE IF NOT EXISTS watchlist(
          id INTEGER PRIMARY KEY AUTOINCREMENT, item_type TEXT NOT NULL, value TEXT NOT NULL,
          bank_name TEXT, scam_type TEXT, risk_score INTEGER NOT NULL DEFAULT 0,
          source_call_id TEXT, status TEXT NOT NULL CHECK(status IN ('pending','approved','rejected')),
          created_at TEXT NOT NULL, expires_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS customers(
          id INTEGER PRIMARY KEY, name TEXT NOT NULL, age INTEGER NOT NULL, balance REAL NOT NULL,
          trusted_contact_name TEXT NOT NULL, trusted_contact_phone TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS linked_accounts(
          id INTEGER PRIMARY KEY AUTOINCREMENT, customer_id INTEGER NOT NULL, bank_name TEXT NOT NULL,
          routing_number TEXT NOT NULL, account_number TEXT NOT NULL, added_at TEXT NOT NULL,
          FOREIGN KEY(customer_id) REFERENCES customers(id)
        );
        CREATE TABLE IF NOT EXISTS transactions(
          id INTEGER PRIMARY KEY AUTOINCREMENT, customer_id INTEGER NOT NULL, linked_account_id INTEGER NOT NULL,
          amount REAL NOT NULL, status TEXT NOT NULL CHECK(status IN ('allowed','warned','held','cancelled')),
          reason TEXT NOT NULL, created_at TEXT NOT NULL,
          FOREIGN KEY(customer_id) REFERENCES customers(id), FOREIGN KEY(linked_account_id) REFERENCES linked_accounts(id)
        );
        CREATE TABLE IF NOT EXISTS events(
          id INTEGER PRIMARY KEY AUTOINCREMENT, type TEXT NOT NULL, detail TEXT NOT NULL, created_at TEXT NOT NULL
        );
        """)
        migrate(c)
        seed_customer(c)

def migrate(c):
    """Databases created before personas existed lack calls.persona_id. SQLite has no ADD COLUMN IF NOT EXISTS, so check first."""
    cols = {r[1] for r in c.execute("PRAGMA table_info(calls)").fetchall()}
    if 'persona_id' not in cols:
        c.execute("ALTER TABLE calls ADD COLUMN persona_id TEXT NOT NULL DEFAULT 'dorothy'")
    if 'prior_context' not in cols:  # JSON about earlier calls from the same phone number (same-scammer memory)
        c.execute("ALTER TABLE calls ADD COLUMN prior_context TEXT")

def seed_customer(c=None):
    own = c is None
    c = c or connect()
    try:
        c.execute("INSERT OR IGNORE INTO customers(id,name,age,balance,trusted_contact_name,trusted_contact_phone) VALUES(1,?,?,?,?,?)",
                  ("Margaret Carter", 68, 310000.0, "Emily Carter (daughter)", "202-555-0199"))
        count = c.execute("SELECT COUNT(*) FROM linked_accounts WHERE customer_id=1").fetchone()[0]
        if count == 0:
            added = (datetime.now(timezone.utc) - timedelta(days=730)).isoformat()
            c.execute("INSERT INTO linked_accounts(customer_id,bank_name,routing_number,account_number,added_at) VALUES(1,?,?,?,?)",
                      ("Northstar Community Bank (demo)", "011000015", "000001234567", added))
        if own: c.commit()
    finally:
        if own: c.close()

def log_event(event_type, detail):
    with connect() as c:
        c.execute("INSERT INTO events(type,detail,created_at) VALUES(?,?,?)", (event_type, detail, now_iso()))

def create_call(call_id, transcript=None, persona_id='dorothy'):
    with connect() as c:
        c.execute("INSERT OR IGNORE INTO calls(id,started_at,transcript_json,persona_id) VALUES(?,?,?,?)",
                  (call_id, now_iso(), json.dumps(transcript or []), persona_id))

def save_transcript(call_id, transcript, ended=True):
    with connect() as c:
        c.execute("INSERT OR IGNORE INTO calls(id,started_at,transcript_json) VALUES(?,?,?)", (call_id, now_iso(), '[]'))
        c.execute("UPDATE calls SET transcript_json=?, ended_at=? WHERE id=?",
                  (json.dumps(transcript), now_iso() if ended else None, call_id))

def save_extraction(call_id, result):
    with connect() as c:
        c.execute("UPDATE calls SET extraction_json=? WHERE id=?", (json.dumps(result), call_id))
        expires = (datetime.now(timezone.utc) + timedelta(days=90)).isoformat()
        for entity in result.get("entities", []):
            if entity.get("type") == "bank_account":
                value = f"{entity.get('routing_number','')}|{entity.get('account_number','')}"
                bank = entity.get("bank_name", "Unknown bank")
                c.execute("INSERT INTO watchlist(item_type,value,bank_name,scam_type,risk_score,source_call_id,status,created_at,expires_at) VALUES(?,?,?,?,?,?, 'pending',?,?)",
                          ("bank_account", value, bank, result.get("scam_type"), result.get("risk_score", 0), call_id, now_iso(), expires))
            elif entity.get("type") in ("phone_number", "amount"):
                val = str(entity.get("value", ""))
                c.execute("INSERT INTO watchlist(item_type,value,bank_name,scam_type,risk_score,source_call_id,status,created_at,expires_at) VALUES(?,?,?,?,?,?, 'pending',?,?)",
                          (entity["type"], val, None, result.get("scam_type"), result.get("risk_score", 0), call_id, now_iso(), expires))

def reset_db():
    with connect() as c:
        c.execute("DELETE FROM calls"); c.execute("DELETE FROM watchlist"); c.execute("DELETE FROM transactions"); c.execute("DELETE FROM events"); c.execute("DELETE FROM linked_accounts"); c.execute("DELETE FROM customers")
        seed_customer(c)
        c.execute("INSERT INTO events(type,detail,created_at) VALUES(?,?,?)", ("reset", "Demo database reset and Margaret reseeded", now_iso()))

def rows(query, params=()):
    with connect() as c:
        return [dict(r) for r in c.execute(query, params).fetchall()]

def one(query, params=()):
    with connect() as c:
        r = c.execute(query, params).fetchone()
        return dict(r) if r else None


# ---------------------------------------------------------------------------
# Same-scammer memory: find earlier calls that involved the same phone number.
# Phone numbers are only known after extraction, so we search the extracted entities.
# ---------------------------------------------------------------------------
def digits(value):
    return re.sub(r"\D", "", str(value or ""))

def normalize_phone(value):
    """Digits only, without a leading US country code, so '(202) 555-0147' == '+1 202.555.0147'."""
    d = digits(value)
    return d[1:] if len(d) == 11 and d.startswith("1") else d

def call_entities(extraction_json):
    """Entities list from a stored extraction (empty if missing or malformed)."""
    try:
        return (json.loads(extraction_json or "{}") or {}).get("entities", []) or []
    except (ValueError, TypeError, AttributeError):
        return []

def get_calls_by_phone(phone_number, exclude_call_id=None):
    """Earlier calls (oldest first) whose extracted entities contain this phone number."""
    wanted = normalize_phone(phone_number)
    if not wanted:
        return []
    found = []
    for c in rows("SELECT id,started_at,ended_at,extraction_json FROM calls WHERE extraction_json IS NOT NULL ORDER BY started_at ASC, rowid ASC"):
        if c["id"] == exclude_call_id:
            continue
        ents = call_entities(c["extraction_json"])
        if any(e.get("type") == "phone_number" and normalize_phone(e.get("value")) == wanted for e in ents):
            ex = json.loads(c["extraction_json"])
            found.append({"id": c["id"], "started_at": c["started_at"], "ended_at": c["ended_at"],
                          "risk_score": ex.get("risk_score"), "scam_type": ex.get("scam_type"), "entities": ents})
    return found

def get_prior_entities(phone_number, exclude_call_id=None):
    """Union of the bank names, routing numbers and account numbers seen in earlier calls from this number (oldest first)."""
    banks, routing, accounts = [], [], []
    for call in get_calls_by_phone(phone_number, exclude_call_id):
        for e in call["entities"]:
            if e.get("type") != "bank_account":
                continue
            bank = (e.get("bank_name") or "").strip()
            if bank and bank != "Unknown bank" and bank not in banks: banks.append(bank)
            if e.get("routing_number") and e["routing_number"] not in routing: routing.append(e["routing_number"])
            if e.get("account_number") and e["account_number"] not in accounts: accounts.append(e["account_number"])
    return {"banks": banks, "routing_numbers": routing, "account_numbers": accounts}

def build_prior_context(phone_number, exclude_call_id=None):
    """What we remember about this number, or None for a first-time caller / missing number.
    Shape: {phone, prior_calls, last_call_date, prior_entities: {bank, routing, account}} (most recent values)."""
    calls = get_calls_by_phone(phone_number, exclude_call_id)
    if not calls:
        return None
    ents = get_prior_entities(phone_number, exclude_call_id)
    return {"phone": normalize_phone(phone_number), "prior_calls": len(calls),
            "last_call_date": (calls[-1]["started_at"] or "")[:10],
            "prior_entities": {"bank": ents["banks"][-1] if ents["banks"] else None,
                               "routing": ents["routing_numbers"][-1] if ents["routing_numbers"] else None,
                               "account": ents["account_numbers"][-1] if ents["account_numbers"] else None}}

def save_prior_context(call_id, ctx):
    with connect() as c:
        c.execute("UPDATE calls SET prior_context=? WHERE id=?", (json.dumps(ctx) if ctx else None, call_id))

def load_prior_context(call_id):
    r = one("SELECT prior_context FROM calls WHERE id=?", (call_id,))
    try:
        return json.loads(r["prior_context"]) if r and r["prior_context"] else None
    except ValueError:
        return None
