import os
from datetime import datetime, timedelta, timezone
import pytest
import db
from fraud_engine import evaluate

@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DB_PATH', str(tmp_path/'test.db'))
    db.init_db()
    yield

def customer(): return db.one('SELECT * FROM customers WHERE id=1')
def account(bank='Safe Bank', routing='011000015', number='000001234567', added=None):
    with db.connect() as c:
        cur=c.execute('INSERT INTO linked_accounts(customer_id,bank_name,routing_number,account_number,added_at) VALUES(1,?,?,?,?)',(bank,routing,number,added or (datetime.now(timezone.utc)-timedelta(days=730)).isoformat()))
        aid=cur.lastrowid
    return db.one('SELECT * FROM linked_accounts WHERE id=?',(aid,))

def test_rule_1_approved_watchlist_account_holds():
    a=account('Scam Bank','021000089','5532198804')
    now=db.now_iso()
    with db.connect() as c: c.execute("INSERT INTO watchlist(item_type,value,scam_type,risk_score,source_call_id,status,created_at,expires_at) VALUES('bank_account',?,'bank_impersonation',90,'call','approved',?,?)",('021000089|5532198804',now,(datetime.now(timezone.utc)+timedelta(days=90)).isoformat()))
    result=evaluate(customer(),a,48000)
    assert result['decision']=='hold' and result['reasons']==['Account linked to active scam']

def test_rule_2_recent_account_large_transfer_warns():
    a=account(added=datetime.now(timezone.utc).isoformat())
    result=evaluate(customer(),a,12000)
    assert result['decision']=='warn' and 'Newly added account' in result['reasons'][0]

def test_rule_3_over_half_balance_warns():
    a=account()
    result=evaluate(customer(),a,160000)
    assert result['decision']=='warn' and '50%' in result['reasons'][0]

def test_rule_4_ordinary_transfer_allowed():
    a=account()
    result=evaluate(customer(),a,500)
    assert result['decision']=='allow'
