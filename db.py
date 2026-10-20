"""SQLite persistence and seed data for the fake-data-only demo."""
import json
import os
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
          persona_id TEXT NOT NULL DEFAULT 'dorothy'
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

