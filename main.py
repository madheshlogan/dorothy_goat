"""Decoy Customer: fake-data-only ideathon prototype."""
import json, uuid, random
from pathlib import Path
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field
import db
from extractor import extract, SAMPLE
from llm import gemini_text
from fraud_engine import evaluate
import offline_dorothy

BASE=Path(__file__).parent
app=FastAPI(title="Decoy Customer – Demo")
app.mount('/static', StaticFiles(directory=BASE/'static'), name='static')
templates=Jinja2Templates(directory=BASE/'templates')
CANNED=["Oh dear, I'm just looking for my glasses. Could you say that slowly?", "I'm sorry, I didn't quite catch the bank name. Could you repeat it?", "Now, let me find a pen. What was that number again?", "I'm not very quick with this technology. Could you explain that one more time?", "Is this something I need to do today?", "Could you read those digits slowly, one at a time?", "I want to make sure I wrote that down correctly. Can you repeat it?", "Can I call you back on the main number on my statement?", "I'm a little worried. Why exactly do you need me to do that?", "Let me check my glasses and my notes for a moment."]
SAMPLE_TRANSCRIPT=[{'speaker':'Dorothy','text':'Hello? I am Dorothy. Who is calling, please?'},{'speaker':'Caller','text':'David from the fraud department. Your account is compromised. You must act today.'},{'speaker':'Dorothy','text':'Oh dear, could you say that slowly? Which bank did you say?'},{'speaker':'Caller','text':"Move $48,000 to First Coastal Bank, routing 021000089, account 5532198804. Call me at 202-555-0147."},{'speaker':'Dorothy','text':"I'm writing that down. Why do I need to move it?"},{'speaker':'Caller','text':"Don't tell anyone, not even family. I can keep your money safe if you do it today."}]

class MessageIn(BaseModel):
    call_id:str
    text:str=Field(min_length=1,max_length=2000)
class AccountIn(BaseModel):
    bank_name:str=Field(min_length=1,max_length=100)
    routing_number:str=Field(min_length=1,max_length=30)
    account_number:str=Field(min_length=1,max_length=40)
class WithdrawIn(BaseModel):
    linked_account_id:int
    amount:float=Field(gt=0, le=310000)
class DecisionIn(BaseModel):
    answers:list[str]=[]

@app.on_event('startup')
def startup(): db.init_db()

@app.get('/', response_class=HTMLResponse)
def root():
    from fastapi.responses import RedirectResponse
    return RedirectResponse('/decoy')

@app.get('/{page}', response_class=HTMLResponse)
def page(page:str, request:Request):
    if page not in {'decoy','analyst','app'}: raise HTTPException(404,'Page not found')
    return templates.TemplateResponse(
    request=request,
    name=f"{page}.html",
    context={"request": request, "page": page}
)
@app.post('/api/decoy/start')
def start_call():
    call_id=str(uuid.uuid4())
    greeting={'speaker':'Dorothy','text':offline_dorothy.OPENING_LINE}
    db.create_call(call_id, [greeting])
    db.log_event('call_started', f'Decoy call {call_id} started')
    return {'call_id':call_id,'greeting':greeting['text'],'transcript':[greeting],'message':'Call started. Speak or type as the caller.'}

@app.post('/api/decoy/message')
def decoy_message(body:MessageIn):
    call=db.one('SELECT * FROM calls WHERE id=?',(body.call_id,))
    if not call: db.create_call(body.call_id, [])
    transcript=json.loads(db.one('SELECT transcript_json FROM calls WHERE id=?',(body.call_id,))['transcript_json'])
    transcript.append({'speaker':'Caller','text':body.text})
    prompt='''You are Dorothy Miller, 68, a retired school teacher in Ohio. You have an IRA and a brokerage account with a large balance. You are polite, a bit anxious, slightly hard of hearing, and slow with technology. Goal: keep the caller talking as long as possible and get them to state exactly what they want you to do, especially the bank name, routing number, account number, amount, and phone numbers. Ask clarifying questions, ask them to repeat numbers slowly, say you're writing things down, mention looking for your glasses. Ask 'Can I call you back on the main number on my statement?' at some point. Never reveal you are an AI. Never give real personal data; if asked, give only these decoy details: account ending 4417, email dorothy.miller.decoy1@example.com, date of birth 03/14/1958. Never actually agree to complete a transfer; stall instead. Keep replies under 40 words.\n'''
    prior='\n'.join(f"{m['speaker']}: {m['text']}" for m in transcript[-10:])
    raw=gemini_text(prompt+'Respond to the latest caller message as Dorothy. Transcript:\n'+prior+'\nDorothy:')
    reply=(raw or '').strip().strip('"')
    if reply.lower().startswith('dorothy:'): reply=reply[8:].strip()
    if not reply or len(reply.split())>45:
        # Offline mode (or Gemini unavailable): rule-based Dorothy that follows the conversation.
        reply=offline_dorothy.reply(transcript)
    transcript.append({'speaker':'Dorothy','text':reply})
    db.save_transcript(body.call_id,transcript,ended=False)
    return {'reply':reply,'transcript':transcript}

@app.post('/api/decoy/end/{call_id}')
def end_call(call_id:str):
    call=db.one('SELECT * FROM calls WHERE id=?',(call_id,))
    if not call: raise HTTPException(404,'Call not found')
    transcript=json.loads(call['transcript_json'])
    db.save_transcript(call_id,transcript,ended=True)
    result=extract(transcript)
    db.save_extraction(call_id,result); db.log_event('call_ended',f'Call {call_id} ended; extraction risk={result["risk_score"]}')
    return {'transcript':transcript,'extraction':result,'message':'Call saved and intelligence extracted.'}

@app.post('/api/extract/{call_id}')
def extract_call(call_id:str):
    call=db.one('SELECT * FROM calls WHERE id=?',(call_id,))
    if not call: raise HTTPException(404,'Call not found')
    result=extract(json.loads(call['transcript_json'])); db.save_extraction(call_id,result)
    return result

@app.post('/api/decoy/sample')
def sample_call():
    call_id=str(uuid.uuid4()); db.create_call(call_id,SAMPLE_TRANSCRIPT); db.save_transcript(call_id,SAMPLE_TRANSCRIPT,ended=True)
    result=extract(SAMPLE_TRANSCRIPT); db.save_extraction(call_id,result); db.log_event('sample_call_loaded',f'Sample call {call_id} loaded')
    return {'call_id':call_id,'transcript':SAMPLE_TRANSCRIPT,'extraction':result,'message':'Sample call loaded and extracted.'}

@app.get('/api/analyst')
def analyst_data():
    candidates=db.rows('SELECT * FROM watchlist ORDER BY created_at DESC')
    calls=db.rows('SELECT * FROM calls ORDER BY started_at DESC')
    approved_scripts=[]
    for c in calls:
        if c.get('extraction_json'):
            try:
                x=json.loads(c['extraction_json'])
                if x.get('is_scam') and x.get('script_lines'): approved_scripts.extend(x['script_lines'])
            except Exception: pass
    stats={
      'calls_handled':len(calls),
      'talk_time_seconds':sum(max(0,( __import__('datetime').datetime.fromisoformat(c['ended_at'])-__import__('datetime').datetime.fromisoformat(c['started_at'])).total_seconds()) for c in calls if c.get('ended_at')),
      'mule_accounts_found':len([w for w in candidates if w['item_type']=='bank_account' and w['status']=='approved']),
      'payments_blocked':len(db.rows("SELECT id FROM transactions WHERE status='held'")),
      'dollars_protected':sum(t['amount'] for t in db.rows("SELECT amount FROM transactions WHERE status='held'"))}
    latest=db.rows('SELECT extraction_json FROM calls WHERE extraction_json IS NOT NULL ORDER BY ended_at DESC LIMIT 1')
    latest_scripts=[]
    if latest:
      try: latest_scripts=json.loads(latest[0]['extraction_json']).get('script_lines',[])
      except Exception: pass
    return {'candidates':candidates,'calls':[{'id':c['id'],'transcript':json.loads(c['transcript_json']),'extraction':json.loads(c['extraction_json']) if c.get('extraction_json') else None} for c in calls], 'stats':stats,'latest_scripts':latest_scripts}

@app.post('/api/watchlist/{item_id}/{action}')
def review(item_id:int, action:str):
    if action not in {'approve','reject'}: raise HTTPException(400,'Action must be approve or reject')
    status='approved' if action=='approve' else 'rejected'
    with db.connect() as c:
        cur=c.execute("UPDATE watchlist SET status=? WHERE id=? AND status='pending'",(status,item_id))
        if cur.rowcount==0: raise HTTPException(404,'Pending candidate not found')
    db.log_event('watchlist_review',f'Candidate {item_id} {status}')
    return {'status':status}

@app.get('/api/customer')
def customer_data():
    customer=db.one('SELECT * FROM customers WHERE id=1')
    accounts=db.rows('SELECT * FROM linked_accounts WHERE customer_id=1 ORDER BY added_at DESC')
    transactions=db.rows('SELECT t.*,a.bank_name FROM transactions t JOIN linked_accounts a ON a.id=t.linked_account_id WHERE t.customer_id=1 ORDER BY t.created_at DESC LIMIT 10')
    latest=db.rows("SELECT extraction_json FROM calls WHERE extraction_json IS NOT NULL ORDER BY ended_at DESC LIMIT 1")
    scripts=[]
    if latest:
      try: scripts=json.loads(latest[0]['extraction_json']).get('script_lines',[])
      except Exception: pass
    return {'customer':customer,'accounts':accounts,'transactions':transactions,'scripts':scripts}

@app.post('/api/accounts')
def add_account(body:AccountIn):
    with db.connect() as c:
        cur=c.execute('INSERT INTO linked_accounts(customer_id,bank_name,routing_number,account_number,added_at) VALUES(1,?,?,?,?)',(body.bank_name,body.routing_number,body.account_number,db.now_iso()))
        account_id=cur.lastrowid
    db.log_event('account_added',f'Fake account added: {body.bank_name}; account_id={account_id}')
    return {'id':account_id,'message':'Demo account added.'}

@app.post('/api/withdraw')
def withdraw(body:WithdrawIn):
    customer=db.one('SELECT * FROM customers WHERE id=1')
    account=db.one('SELECT * FROM linked_accounts WHERE id=? AND customer_id=1',(body.linked_account_id,))
    if not account: raise HTTPException(404,'Account not found')
    if body.amount>customer['balance']: raise HTTPException(400,'Amount exceeds demo balance')
    result=evaluate(customer,account,body.amount)
    status={'allow':'allowed','warn':'warned','hold':'held'}[result['decision']]
    with db.connect() as c:
        cur=c.execute('INSERT INTO transactions(customer_id,linked_account_id,amount,status,reason,created_at) VALUES(1,?,?,?,?,?)',(account['id'],body.amount,status,'; '.join(result['reasons']),db.now_iso()))
        txid=cur.lastrowid
    return {'transaction_id':txid,'decision':result['decision'],'reasons':result['reasons'],'status':status,'message':'Transfer allowed.' if status=='allowed' else 'Transfer paused for a safety check.'}

@app.post('/api/transactions/{txid}/resolve')
def resolve_transaction(txid:int, body:DecisionIn):
    tx=db.one('SELECT * FROM transactions WHERE id=?',(txid,))
    if not tx: raise HTTPException(404,'Transaction not found')
    yes=any(a.lower()=='yes' for a in body.answers)
    if tx['status']=='held' or yes:
        with db.connect() as c: c.execute("UPDATE transactions SET status='held',reason=? WHERE id=?",('Safety hold: suspicious transfer signals or a Yes answer',txid))
        db.log_event('payment_held',f'Transaction {txid} held; trusted contact notification simulated')
        db.log_event('trusted_contact_notified',f"Simulated notice to Emily Carter (daughter) for transaction {txid}")
        return {'status':'held','message':'We never ask you to move money to protect it. Your money is safe. Please hang up.','specialist_message':'A demo specialist would join now. No real call was placed.','trusted_contact_notice':"We've let your trusted contact, Emily Carter, know."}
    return {'status':'confirm_required','message':'Both answers were No. Review the details and press Confirm transfer anyway to continue.'}

@app.post('/api/transactions/{txid}/confirm')
def confirm_transaction(txid:int):
    tx=db.one('SELECT * FROM transactions WHERE id=?',(txid,))
    if not tx: raise HTTPException(404,'Transaction not found')
    if tx['status']=='held': return {'status':'held','message':'This transfer remains on hold for your safety.'}
    with db.connect() as c: c.execute("UPDATE transactions SET status='allowed',reason='Customer confirmed both safety questions as No and pressed final confirm' WHERE id=?",(txid,))
    db.log_event('payment_confirmed',f'Transaction {txid} allowed after final confirmation')
    return {'status':'allowed','message':'Final confirmation complete. Demo transfer allowed.'}

@app.post('/api/reset')
def reset():
    db.reset_db()
    return {'message':'Demo reset complete. Margaret Carter and her safe old account were reseeded.'}
