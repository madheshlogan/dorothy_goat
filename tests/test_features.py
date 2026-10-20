"""Scammer network graph, PDF report export, same-scammer memory."""
import json
import sqlite3
import pytest
from fastapi.testclient import TestClient

import db
import offline_dorothy as od
import report
from extractor import extract

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


