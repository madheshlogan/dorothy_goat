"""Scammer network graph, PDF report export, same-scammer memory."""
import json
import sqlite3
import pytest
from fastapi.testclient import TestClient

import db
import network
import offline_dorothy as od
import report
from extractor import apply_repeat_caller, extract

BANK = 'First Coastal Bank'
PHONE = '202-555-0147'


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DB_PATH', str(tmp_path / 't.db'))
    monkeypatch.setenv('OFFLINE_MODE', 'true')
    import main
    with TestClient(main.app) as c:
        yield c


def add_call(call_id, started, entities, risk=50, scam_type='bank_impersonation', flags=None, summary='s'):
    """Insert an already-extracted call straight into the DB."""
    db.create_call(call_id, [])
    with db.connect() as c:
        c.execute('UPDATE calls SET started_at=?, extraction_json=? WHERE id=?',
                  (started, json.dumps({'is_scam': True, 'scam_type': scam_type, 'risk_score': risk, 'red_flags': flags or [],
                                        'entities': entities, 'script_lines': [], 'summary': summary}), call_id))


def acct(routing='021000089', account='5532198804', bank=BANK):
    return {'type': 'bank_account', 'bank_name': bank, 'routing_number': routing, 'account_number': account}


def phone(v=PHONE):
    return {'type': 'phone_number', 'value': v}


# ============ Feature 1: network ============

def test_network_empty_and_single_call(client):
    assert network.build_network() == {'nodes': [], 'edges': []}
    add_call('a', '2026-01-01T00:00:00+00:00', [acct()])
    n = client.get('/api/analyst/network').json()
    assert len(n['nodes']) == 1 and n['edges'] == []


def test_isolated_node_still_appears(client):
    add_call('a', '2026-01-01T00:00:00+00:00', [acct()])
    add_call('b', '2026-01-02T00:00:00+00:00', [acct('111000614', '3029981746')])
    n = client.get('/api/analyst/network').json()
    assert {x['id'] for x in n['nodes']} == {'a', 'b'} and n['edges'] == []


def test_edges_for_shared_routing_account_and_phone(client):
    add_call('a', '2026-01-01T00:00:00+00:00', [acct(), phone('202-555-0147')])
    add_call('b', '2026-01-02T00:00:00+00:00', [acct(account='999999999'), phone('(202) 555-0147')])  # routing + phone (different format)
    add_call('c', '2026-01-03T00:00:00+00:00', [acct(routing='111111111', account='5532198804')])      # account only, with a
    n = client.get('/api/analyst/network').json()
    kinds = {(e['from'], e['to'], e['shared_entity_type']) for e in n['edges']}
    assert kinds == {('a', 'b', 'routing'), ('a', 'b', 'phone'), ('a', 'c', 'account')}
    assert all(e['label'] for e in n['edges'])


def test_node_fields_and_colour_thresholds(client):
    for i, risk in enumerate([0, 39, 40, 70, 71, 100]):
        add_call(f'c{i}', f'2026-01-0{i + 1}T00:00:00+00:00', [], risk=risk, summary=f'sum{risk}')
    nodes = {n['id']: n for n in client.get('/api/analyst/network').json()['nodes']}
    assert [nodes[f'c{i}']['color'] for i in range(6)] == [network.GREEN, network.GREEN, network.ORANGE, network.ORANGE, network.RED, network.RED]
    n = nodes['c3']
    assert n['call_id'] == 'c3' and n['risk_score'] == 70 and n['summary'] == 'sum70'
    assert n['label'].startswith('2026-01-04') and 'bank_impersonation' in n['label']


def test_calls_without_extraction_are_not_nodes_and_bad_json_is_skipped(client):
    db.create_call('plain', [])
    db.create_call('bad', [])
    with db.connect() as c:
        c.execute("UPDATE calls SET extraction_json='{not json' WHERE id='bad'")
    assert client.get('/api/analyst/network').json()['nodes'] == []


# ============ Feature 2: report ============

def sample_call(client):
    return client.post('/api/decoy/sample').json()['call_id']


def test_pdf_download(client):
    cid = sample_call(client)
    r = client.get(f'/api/analyst/report/{cid}')
    assert r.status_code == 200 and r.headers['content-type'] == 'application/pdf'
    assert r.headers['content-disposition'] == f'attachment; filename="dorothy_report_{cid[:8]}.pdf"'
    assert r.content.startswith(b'%PDF') and len(r.content) > 2000


def test_report_unknown_call_404(client):
    assert client.get('/api/analyst/report/nope').status_code == 404


def test_pdf_survives_awkward_calls(client):
    with db.connect() as c:
        c.execute("INSERT INTO calls(id,started_at,transcript_json) VALUES('empty','2026-01-01T00:00:00+00:00','[]')")           # no extraction, empty transcript
        c.execute("INSERT INTO calls(id,started_at,transcript_json,extraction_json) VALUES('zero','2026-01-01T00:00:00+00:00','[]',?)",
                  (json.dumps({'is_scam': False, 'scam_type': 'not_scam', 'risk_score': 5, 'red_flags': [], 'entities': [], 'script_lines': [], 'summary': ''}),))
    for cid in ('empty', 'zero'):
        assert client.get(f'/api/analyst/report/{cid}').content.startswith(b'%PDF')
    nasty = [{'speaker': 'Caller', 'text': '<b>bold</b> & <script> ’ € \U0001F600 ' * 50}, {'speaker': 'Dorothy', 'text': 'ok', 'name': 'Robert Chen'}]
    db.create_call('nasty', nasty, 'robert')
    db.save_transcript('nasty', nasty, ended=True)
    db.save_extraction('nasty', extract(nasty))
    assert client.get('/api/analyst/report/nasty').content.startswith(b'%PDF')


def test_long_transcript_paginates(client):
    t = [{'speaker': 'Caller' if i % 2 else 'Dorothy', 'text': 'word ' * 40} for i in range(200)]
    db.create_call('long', t)
    db.save_transcript('long', t, ended=True)
    import re
    pdf = client.get('/api/analyst/report/long').content
    assert pdf.startswith(b'%PDF') and len(re.findall(rb'/Type /Page\b(?!s)', pdf)) > 3  # many pages, not one overflowing page


def test_text_fallback_without_reportlab(client, monkeypatch):
    cid = sample_call(client)
    monkeypatch.setattr(report, 'HAVE_REPORTLAB', False)
    r = client.get(f'/api/analyst/report/{cid}')
    assert r.headers['content-type'].startswith('text/plain') and r.headers['content-disposition'].endswith('.txt"')
    text = r.text
    for needle in ('Intelligence Report', cid, 'RISK SCORE', 'bank_impersonation', 'First Coastal Bank', '021000089',
                   'Dorothy Miller: ', 'David from the fraud department', 'For internal fraud investigation use only'):
        assert needle in text, needle


def test_text_report_for_extractionless_call():
    txt = report.build_text({'id': 'x', 'started_at': None, 'ended_at': None, 'transcript_json': '[]', 'extraction_json': None, 'persona_id': None})
    assert 'not been run' in txt and '(empty)' in txt


# ============ Feature 3: same-scammer memory ============

def test_phone_normalisation():
    assert db.normalize_phone('(202) 555-0147') == db.normalize_phone('+1 202.555.0147') == '2025550147'
    assert db.normalize_phone(None) == '' and db.normalize_phone('abc') == ''


def test_db_history_functions(client):
    add_call('a', '2026-01-01T00:00:00+00:00', [acct(), phone()])
    add_call('b', '2026-02-01T00:00:00+00:00', [acct('111000614', '3029981746', 'Lone Star Business Bank'), phone('(202) 555-0147')])
    add_call('c', '2026-03-01T00:00:00+00:00', [phone('415-555-0172')])
    calls = db.get_calls_by_phone('+1 202 555 0147')
    assert [c['id'] for c in calls] == ['a', 'b']
    assert [c['id'] for c in db.get_calls_by_phone(PHONE, exclude_call_id='b')] == ['a']
    assert db.get_calls_by_phone('') == [] and db.get_calls_by_phone(None) == [] and db.get_calls_by_phone('999-999-9999') == []
    ents = db.get_prior_entities(PHONE)
    assert ents == {'banks': [BANK, 'Lone Star Business Bank'], 'routing_numbers': ['021000089', '111000614'], 'account_numbers': ['5532198804', '3029981746']}
    ctx = db.build_prior_context(PHONE)
    assert ctx == {'phone': '2025550147', 'prior_calls': 2, 'last_call_date': '2026-02-01',
                   'prior_entities': {'bank': 'Lone Star Business Bank', 'routing': '111000614', 'account': '3029981746'}}
    assert db.build_prior_context('999-999-9999') is None and db.build_prior_context(None) is None


def test_caller_history_endpoint(client):
    add_call('a', '2026-01-01T00:00:00+00:00', [acct(), phone()])
    add_call('b', '2026-02-01T00:00:00+00:00', [phone()])
    d = client.get('/api/analyst/caller/202-555-0147').json()
    assert d['call_count'] == 2 and d['repeat_caller'] and d['entities']['banks'] == [BANK] and [c['id'] for c in d['calls']] == ['a', 'b']
    assert client.get('/api/analyst/caller/999-999-9999').json()['call_count'] == 0
    assert client.get('/api/analyst/caller/abc').status_code == 400


def test_second_sample_call_is_a_repeat_caller(client):
    first = client.post('/api/decoy/sample').json()
    assert 'repeat_caller' not in first['extraction']['red_flags'] and 'Repeat caller' not in first['extraction']['summary']
    second = client.post('/api/decoy/sample').json()['extraction']
    assert 'repeat_caller' in second['red_flags']
    assert second['risk_score'] == min(100, first['extraction']['risk_score'] + 20)
    first_date = db.one('SELECT started_at FROM calls WHERE id=?', (first['call_id'],))['started_at'][:10]
    assert second['summary'].endswith(f"Repeat caller — previously contacted on {first_date}.")
    third = client.post('/api/decoy/sample').json()['extraction']
    assert third['risk_score'] == min(100, first['extraction']['risk_score'] + 40)
    fourth = client.post('/api/decoy/sample').json()['extraction']
    assert fourth['risk_score'] == third['risk_score']  # bonus capped at +40 (unless already 100)


def test_repeat_bonus_math_and_cap():
    base = {'risk_score': 30, 'red_flags': ['urgency'], 'summary': 'x.'}
    assert apply_repeat_caller(dict(base, red_flags=['urgency']), {'prior_calls': 1, 'last_call_date': '2026-01-01'})['risk_score'] == 50
    assert apply_repeat_caller(dict(base, red_flags=[]), {'prior_calls': 9})['risk_score'] == 70
    assert apply_repeat_caller(dict(base, risk_score=90, red_flags=[]), {'prior_calls': 2})['risk_score'] == 100
    r = apply_repeat_caller(dict(base, red_flags=['repeat_caller', 'urgency']), None)   # an LLM can't invent the flag
    assert 'repeat_caller' not in r['red_flags'] and r['risk_score'] == 30
    for bad in ({}, {'prior_calls': 0}, {'prior_calls': 'x'}, {'prior_calls': None}):
        assert apply_repeat_caller(dict(base, red_flags=[]), bad)['risk_score'] == 30


def test_no_phone_and_firsttimer_do_not_crash(client):
    cid = client.post('/api/decoy/start').json()['call_id']
    r = client.post('/api/decoy/message', json={'call_id': cid, 'text': 'Hello, your account is compromised'})
    assert r.status_code == 200
    r = client.post('/api/decoy/message', json={'call_id': cid, 'text': f'Call me on {PHONE}'})  # new number, no history
    assert r.status_code == 200 and "Weren't you" not in r.json()['reply'] and db.load_prior_context(cid) is None
    assert 'repeat_caller' not in client.post(f'/api/decoy/end/{cid}').json()['extraction']['red_flags']


def test_live_call_recognises_returning_scammer(client):
    first = client.post('/api/decoy/sample').json()               # earlier call: First Coastal Bank, 202-555-0147
    cid = client.post('/api/decoy/start').json()['call_id']
    r1 = client.post('/api/decoy/message', json={'call_id': cid, 'text': f'This is David. Call me back at {PHONE}.'}).json()['reply']
    assert f"Weren't you the one who called about {BANK} before?" in r1
    assert db.load_prior_context(cid)['prior_calls'] == 1
    r2 = client.post('/api/decoy/message', json={'call_id': cid, 'text': 'Move your money to a safe account today.'}).json()['reply']
    assert "Weren't you" not in r2                                 # only asked once
    r3 = client.post('/api/decoy/message', json={'call_id': cid, 'text': f'Send it to {BANK}.'}).json()['reply']
    assert f'Was that the same {BANK} you mentioned before?' in r3
    end = client.post(f'/api/decoy/end/{cid}').json()['extraction']
    assert 'repeat_caller' in end['red_flags'] and 'Repeat caller' in end['summary']
    # re-extracting must not count the call as its own predecessor
    again = client.post(f'/api/extract/{cid}').json()
    assert again['risk_score'] == end['risk_score']
    calls = {c['id']: c for c in client.get('/api/analyst').json()['calls']}
    assert 'repeat_caller' in calls[cid]['extraction']['red_flags'] and calls[first['call_id']]['started_at']


def test_offline_reply_with_prior_context():
    prior = {'phone': '2025550147', 'prior_calls': 1, 'prior_entities': {'bank': BANK, 'routing': '021000089', 'account': '5532198804'}}
    t = [{'speaker': 'Dorothy', 'text': od.OPENING_LINE}, {'speaker': 'Caller', 'text': 'Hello, call me at 202-555-0147.'}]
    first = od.reply(t, prior_context=prior)
    assert f"Weren't you the one who called about {BANK} before?" in first
    t += [{'speaker': 'Dorothy', 'text': first}, {'speaker': 'Caller', 'text': 'Routing number 021000089'}]
    second = od.reply(t, prior_context=prior)
    assert "Weren't you" not in second and 'same one you gave me before' in second   # confirm, don't re-ask
    assert 'Weren\'t you' not in od.reply(t[:2], prior_context=None)


@pytest.mark.parametrize('ctx', [None, {}, {'prior_calls': 0}, {'prior_calls': None}, {'prior_calls': 'x'}, {'prior_calls': 1}, {'prior_calls': 2, 'prior_entities': None}])
@pytest.mark.parametrize('pid', ['dorothy', 'robert', 'sandra'])
def test_reply_tolerates_any_prior_context(pid, ctx):
    t = [{'speaker': 'Dorothy', 'text': od.OPENING_LINE}, {'speaker': 'Caller', 'text': 'Move $5,000 to a safe account.'}]
    line = od.reply(t, persona_id=pid, prior_context=ctx)
    assert line and len(line.split()) <= od.MAX_WORDS


def test_no_prior_context_means_unchanged_behaviour():
    t = [{'speaker': 'Dorothy', 'text': od.OPENING_LINE}, {'speaker': 'Caller', 'text': 'Routing number 021000089, account 5532198804, First Coastal Bank'}]
    assert od.reply(t) == od.reply(t, prior_context={'prior_calls': 0})


@pytest.mark.parametrize('pid', ['robert', 'sandra'])
def test_other_personas_recognise_too(pid):
    prior = {'prior_calls': 1, 'prior_entities': {'bank': BANK}}
    t = [{'speaker': 'Dorothy', 'text': 'hi'}, {'speaker': 'Caller', 'text': 'Hello there'}]
    assert f"Weren't you the one who called about {BANK} before?" in od.reply(t, persona_id=pid, prior_context=prior)


def test_migration_adds_prior_context_column(tmp_path, monkeypatch):
    path = str(tmp_path / 'old.db')
    with sqlite3.connect(path) as c:
        c.execute("CREATE TABLE calls(id TEXT PRIMARY KEY, started_at TEXT NOT NULL, ended_at TEXT, transcript_json TEXT NOT NULL DEFAULT '[]', extraction_json TEXT)")
    monkeypatch.setattr(db, 'DB_PATH', path)
    db.init_db()
    db.init_db()
    cols = {r[1] for r in sqlite3.connect(path).execute('PRAGMA table_info(calls)')}
    assert {'persona_id', 'prior_context'} <= cols
