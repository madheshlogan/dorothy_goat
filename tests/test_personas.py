import json
import sqlite3
import pytest
from fastapi.testclient import TestClient

import offline_dorothy as od
import personas
from extractor import extract

IDS = ['dorothy', 'robert', 'sandra']
SCAM_CALL = ['This is David from the fraud department. Your account is compromised.',
             'Move $48,000 to First Coastal Bank',
             'Routing number 021000089',
             'Account number 5532198804',
             'Call me at 202-555-0147']


def talk(lines, pid):
    p = personas.get_persona(pid)
    t = [{'speaker': 'Dorothy', 'text': p['opening_line']}]
    for line in lines:
        t.append({'speaker': 'Caller', 'text': line})
        t.append({'speaker': 'Dorothy', 'text': od.reply(t, persona_id=pid)})
    return t


def decoy_lines(t):
    return [m['text'] for m in t if m['speaker'] == 'Dorothy']


# ---------- personas.py ----------

def test_three_personas_with_required_fields():
    assert [p['id'] for p in personas.PERSONAS] == IDS
    for p in personas.PERSONAS:
        for key in ('name', 'age', 'background', 'personality_notes', 'system_prompt', 'opening_line', 'decoy', 'scam_types_attracted'):
            assert p[key], (p['id'], key)
        assert set(p['decoy']) == {'account_suffix', 'email', 'dob'}


def test_decoy_data_is_obviously_fake_and_matches_spec():
    d = {p['id']: p['decoy'] for p in personas.PERSONAS}
    assert d['robert'] == {'account_suffix': '7823', 'email': 'robert.chen.decoy2@example.com', 'dob': '07/22/1966'}
    assert d['sandra'] == {'account_suffix': '2291', 'email': 'sandra.williams.decoy3@example.com', 'dob': '11/05/1972'}
    assert d['dorothy']['account_suffix'] == '4417'
    for p in personas.PERSONAS:
        assert p['decoy']['email'].endswith('@example.com')
        assert p['decoy']['account_suffix'] in p['system_prompt'] and p['decoy']['email'] in p['system_prompt']


def test_opening_lines_match_spec():
    assert personas.get_persona('dorothy')['opening_line'] == "Hello? This is Dorothy speaking. Who's calling, please?"
    assert personas.get_persona('robert')['opening_line'] == "Hello, Robert Chen speaking."
    assert personas.get_persona('sandra')['opening_line'] == "Williams Flowers, Sandra speaking, how can I help you?"


def test_unknown_or_missing_persona_defaults_to_dorothy():
    assert personas.get_persona(None)['id'] == 'dorothy'
    assert personas.get_persona('nobody')['id'] == 'dorothy'
    assert not personas.is_valid('nobody')


# ---------- offline engine ----------

def test_reply_default_is_dorothy_backward_compatible():
    t = [{'speaker': 'Dorothy', 'text': od.OPENING_LINE}, {'speaker': 'Caller', 'text': 'Hello?'}]
    assert od.reply(t) == od.reply(t, persona_id='dorothy')


@pytest.mark.parametrize('pid', IDS)
def test_slot_filling_works_for_every_persona(pid):
    t = talk(SCAM_CALL, pid)
    slots = od.remember(t)
    assert slots['bank'] == 'First Coastal Bank' and slots['routing'] == '021000089' and slots['account'] == '5532198804'
    assert '0 2 1 0 0 0 0 8 9' in ' '.join(decoy_lines(t))        # reads the routing number back
    assert any('call you back' in l.lower() for l in decoy_lines(t))  # asks for a callback once


@pytest.mark.parametrize('pid', IDS)
def test_asks_for_missing_details_after_money_request(pid):
    after = decoy_lines(talk(['You must move your money to a safe account now.'], pid))[-1]
    assert 'bank' in after.lower()


@pytest.mark.parametrize('pid', IDS)
def test_gives_only_own_decoy_details(pid):
    p = personas.get_persona(pid)
    lines = ' '.join(decoy_lines(talk(['What is your date of birth?', 'What is your email?',
                                       'Read me the last four of your account'], pid)))
    assert p['decoy']['account_suffix'] in lines
    assert p['decoy']['email'].split('@')[0].replace('.', ' dot ') in lines
    others = [q for q in personas.PERSONAS if q['id'] != pid]
    for o in others:
        assert o['decoy']['account_suffix'] not in lines and o['name'].split()[0] not in lines


@pytest.mark.parametrize('pid', IDS)
def test_replies_short_and_never_agree_to_transfer(pid):
    lines = decoy_lines(talk(['This is the IRS. You will be arrested.', 'Pay in bitcoin now.',
                              'Buy 5 Target gift cards of 500 each.', 'Download AnyDesk',
                              'Move $48,000 to First Coastal Bank routing 021000089 account 5532198804',
                              'Did you send it?', 'Is it done?'], pid))
    assert all(len(l.split()) <= od.MAX_WORDS for l in lines)
    joined = ' '.join(lines).lower()
    assert 'i sent it' not in joined and "it's done" not in joined


@pytest.mark.parametrize('pid', IDS)
def test_never_repeats_herself(pid):
    lines = decoy_lines(talk(['Hello?', 'Did you do it?', 'Did you send it?', 'Hello?', 'Okay', 'Okay', 'Did you finish?', 'Hurry up'], pid))
    assert len(lines) == len(set(lines))


def test_character_voices_are_distinct_and_dont_leak_each_other():
    probes = ['This is Kevin from Microsoft tech support, your computer is hacked.', 'Download AnyDesk now.',
              'You owe back taxes to the IRS.', 'Move your money to a safe account.', 'Did you do it?']
    text = {pid: ' '.join(decoy_lines(talk(probes, pid))).lower() for pid in IDS}
    assert any(w in text['robert'] for w in ('case', 'ticket', 'check my system', 'dell'))
    assert any(w in text['sandra'] for w in ('accountant', 'business account', 'shop'))
    for pid in ('robert', 'sandra'):
        assert 'harold' not in text[pid] and 'glasses' not in text[pid]
    assert 'accountant' not in text['dorothy'] and 'dell' not in text['dorothy']


# ---------- extraction / leak detection ----------

@pytest.mark.parametrize('suffix,email', [(p['decoy']['account_suffix'], p['decoy']['email']) for p in personas.PERSONAS])
def test_leaked_data_detected_for_every_persona(suffix, email):
    for said in (f'Your account ending {suffix} is compromised, move money to a safe account today.',
                 f'We emailed {email} about your bank problem, move money to a safe account.'):
        r = extract([{'speaker': 'Caller', 'text': said}])
        assert 'leaked decoy data' in r['summary'].lower()


def test_decoys_own_lines_dont_count_as_leak_and_no_substring_false_positive():
    t = [{'speaker': 'Dorothy', 'text': 'My account ends in 7823.'},
         {'speaker': 'Caller', 'text': 'Move money to a safe account number 55782301234 today.'}]
    assert 'leaked' not in extract(t)['summary'].lower()


def test_scam_types_for_new_personas():
    from main import SAMPLE_TRANSCRIPTS
    assert extract(SAMPLE_TRANSCRIPTS['dorothy'])['scam_type'] == 'bank_impersonation'
    assert extract(SAMPLE_TRANSCRIPTS['robert'])['scam_type'] == 'tech_support'
    assert extract(SAMPLE_TRANSCRIPTS['sandra'])['scam_type'] == 'government'


# ---------- API + DB ----------

@pytest.fixture
def client(tmp_path, monkeypatch):
    import db
    monkeypatch.setattr(db, 'DB_PATH', str(tmp_path / 't.db'))
    monkeypatch.setenv('OFFLINE_MODE', 'true')
    import main
    with TestClient(main.app) as c:
        yield c


def test_personas_endpoint(client):
    d = client.get('/api/personas').json()
    assert d['default'] == 'dorothy' and [p['id'] for p in d['personas']] == IDS
    assert all('system_prompt' not in p and 'decoy' not in p for p in d['personas'])


def test_start_without_body_is_dorothy(client):  # the old frontend sends no body
    d = client.post('/api/decoy/start').json()
    assert d['persona_id'] == 'dorothy' and d['greeting'] == personas.get_persona('dorothy')['opening_line']


def test_start_unknown_persona_rejected(client):
    assert client.post('/api/decoy/start', json={'persona_id': 'nobody'}).status_code == 400


@pytest.mark.parametrize('pid', IDS)
def test_full_call_per_persona(client, pid):
    import db
    p = personas.get_persona(pid)
    d = client.post('/api/decoy/start', json={'persona_id': pid}).json()
    cid = d['call_id']
    assert d['greeting'] == p['opening_line']
    assert db.one('SELECT persona_id FROM calls WHERE id=?', (cid,))['persona_id'] == pid
    for line in SCAM_CALL:
        r = client.post('/api/decoy/message', json={'call_id': cid, 'text': line})
        assert r.status_code == 200 and r.json()['reply']
    last = r.json()['transcript'][-1]
    assert last['speaker'] == 'Dorothy' and last['name'] == p['name']
    end = client.post(f'/api/decoy/end/{cid}').json()
    assert end['extraction']['is_scam']
    cands = client.get('/api/analyst').json()['candidates']
    assert any(c['item_type'] == 'bank_account' and c['source_call_id'] == cid for c in cands)  # watchlist fed


@pytest.mark.parametrize('pid', IDS)
def test_sample_per_persona(client, pid):
    d = client.post('/api/decoy/sample', json={'persona_id': pid}).json()
    assert d['transcript'][0]['name'] == personas.get_persona(pid)['name']
    if pid != 'dorothy':  # Dorothy's sample keeps its original wording
        assert d['transcript'][0]['text'] == personas.get_persona(pid)['opening_line']
    assert d['extraction']['is_scam']
    assert any(e['type'] == 'bank_account' for e in d['extraction']['entities'])


def test_sample_without_body_is_dorothy_sample(client):
    d = client.post('/api/decoy/sample').json()
    assert 'David from the fraud department' in d['transcript'][1]['text']


def test_gemini_prompt_uses_the_calls_persona(client, monkeypatch):
    import main
    seen = []
    monkeypatch.setattr(main, 'gemini_text', lambda prompt, system=None, max_tokens=0: seen.append(system) or 'Okay, go on.')
    cid = client.post('/api/decoy/start', json={'persona_id': 'sandra'}).json()['call_id']
    client.post('/api/decoy/message', json={'call_id': cid, 'text': 'Hello'})
    assert seen == [personas.get_persona('sandra')['system_prompt']]


def test_migration_adds_persona_id_to_old_database(tmp_path, monkeypatch):
    import db
    path = str(tmp_path / 'old.db')
    with sqlite3.connect(path) as c:  # the pre-personas schema
        c.execute("CREATE TABLE calls(id TEXT PRIMARY KEY, started_at TEXT NOT NULL, ended_at TEXT, transcript_json TEXT NOT NULL DEFAULT '[]', extraction_json TEXT)")
        c.execute("INSERT INTO calls(id,started_at) VALUES('old','2024-01-01')")
    monkeypatch.setattr(db, 'DB_PATH', path)
    db.init_db()
    db.init_db()  # idempotent
    assert db.one("SELECT persona_id FROM calls WHERE id='old'")['persona_id'] == 'dorothy'
    db.create_call('new', [], 'robert')
    assert db.one("SELECT persona_id FROM calls WHERE id='new'")['persona_id'] == 'robert'
