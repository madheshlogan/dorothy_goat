"""Scam intelligence extraction with strict schema validation and offline regex fallback."""
import re
from pydantic import BaseModel, Field, ValidationError, field_validator
from llm import gemini_text, safe_json
from personas import leaked_decoy_data

SCAM_TYPES = {'bank_impersonation','investment','tech_support','romance','government','other','not_scam'}
FLAGS = {'impersonates_fidelity_or_bank','urgency','secrecy','move_money_to_safe_account','asks_for_codes_or_passwords','crypto_or_gift_cards','refuses_callback','threats','repeat_caller'}
class Entity(BaseModel):
    type: str
    bank_name: str = ''
    routing_number: str = ''
    account_number: str = ''
    value: str | float | int = ''
class Extraction(BaseModel):
    is_scam: bool
    scam_type: str
    risk_score: int = Field(ge=0, le=100)
    red_flags: list[str]
    entities: list[Entity]
    script_lines: list[str] = Field(max_length=5)
    summary: str
    @field_validator('scam_type')
    @classmethod
    def valid_type(cls, v):
        if v not in SCAM_TYPES: raise ValueError('invalid scam_type')
        return v
    @field_validator('red_flags')
    @classmethod
    def valid_flags(cls, v):
        return [x for x in v if x in FLAGS]

SAMPLE = """David: Hello Dorothy, this is David from the fraud department. Your account is compromised. You must act today. Move $48,000 to First Coastal Bank, routing 021000089, account 5532198804. Call me at 202-555-0147. Don't tell anyone, not even family. I can keep your money safe."""

def _caller_text(transcript):
    if not isinstance(transcript, list):
        return str(transcript)
    return ' '.join(str(m.get('text','')) for m in transcript if m.get('speaker') != 'Dorothy')

def _scam_type(low, scam):
    if not scam: return 'not_scam'
    if re.search(r'\bmicrosoft\b|tech support|hacked|virus|your computer', low): return 'tech_support'
    if re.search(r'\birs\b|internal revenue|back taxes|social security administration', low): return 'government'
    if 'bank' in low or 'fraud department' in low: return 'bank_impersonation'
    return 'other'

def offline_extract(transcript):
    # Only the caller's words: Dorothy repeats numbers back and gives her own decoy details,
    # which must not be counted as scammer data or as 'caller used leaked data'.
    text = _caller_text(transcript)
    low = text.lower(); flags=[]
    tests = {
      'impersonates_fidelity_or_bank': r'fraud department|bank|fidelity|security team',
      'urgency': r'act today|immediately|urgent|right now|today',
      'secrecy': r"don't tell|do not tell|keep.*secret|not even family", 
      'move_money_to_safe_account': r'safe account|move.*money|transfer.*money|keep your money safe',
      'asks_for_codes_or_passwords': r'password|verification code|one.time code|otp',
      'crypto_or_gift_cards': r'crypto|bitcoin|gift card',
      'refuses_callback': r"no callback|cannot call back|don't call back",
      'threats': r'arrest|jail|police|legal action|account.*close'
    }
    for f, pat in tests.items():
        if re.search(pat, low): flags.append(f)
    routing = re.findall(r'(?<!\d)\d{9}(?!\d)', text)
    accounts = re.findall(r'account(?: number)?\s*(?:is|:)?\s*(\d{6,17})', text, re.I)
    phones = re.findall(r'\b(?:\+?1[-. ]?)?\(?\d{3}\)?[-. ]\d{3}[-. ]\d{4}\b', text)
    amounts = re.findall(r'\$\s?([\d,]+(?:\.\d{2})?)', text)
    entities=[]
    if routing or accounts:
        banks = re.findall(r'(?:to|at)\s+([A-Z][A-Za-z]+(?:\s+[A-Z][A-Za-z]+){0,3}\s+Bank)', text)
        entities.append({'type':'bank_account','bank_name':banks[0] if banks else 'Unknown bank','routing_number':routing[0] if routing else '', 'account_number':accounts[0] if accounts else ''})
    entities += [{'type':'phone_number','value':p} for p in dict.fromkeys(phones)]
    entities += [{'type':'amount','value':float(a.replace(',',''))} for a in dict.fromkeys(amounts)]
    leaked = leaked_decoy_data(text)  # any persona's decoy account suffix or email
    scam = bool(flags) or bool(routing and accounts)
    risk = min(100, (35 if scam else 5) + 10*len(flags) + (30 if leaked else 0))
    lines=[]
    for phrase in ['Your account is compromised','You must act today',"Don't tell anyone, not even family",'move your money to a safe account','act immediately']:
        if phrase.lower() in low: lines.append(phrase)
    if not lines and scam: lines = [x.strip()[:100] for x in re.findall(r'[^.!?]+[.!?]?', text) if re.search(r'urgent|must|secret|safe|compromis|transfer|money|account', x, re.I)][:5]
    return {'is_scam':scam,'scam_type':_scam_type(low, scam), 'risk_score':risk, 'red_flags':flags, 'entities':entities, 'script_lines':lines[:5], 'summary': ('Caller used pressure or suspicious payment instructions.' if scam else 'No clear scam indicators were detected.') + (' Uses leaked decoy data.' if leaked else '')}

def _extract(transcript):
    text = '\n'.join(f"{m.get('speaker','Caller')}: {m.get('text','')}" for m in transcript)
    prompt = f'''Analyze this call transcript for fraud. Return ONLY strict JSON with keys is_scam (boolean), scam_type (one of {sorted(SCAM_TYPES)}), risk_score (integer 0-100), red_flags (only from {sorted(FLAGS - {'repeat_caller'})}), entities (array; bank account objects have type, bank_name, routing_number, account_number; phone objects have type, value; amount objects have type, numeric value), script_lines (up to 5 verbatim short pressure phrases), summary (one sentence). Distinguish ordinary sales from scams; selling a product without red flags is not a scam.\nTranscript:\n{text}'''
    raw = gemini_text(prompt)
    parsed = safe_json(raw)
    if parsed is not None:
        try: result = Extraction.model_validate(parsed).model_dump()
        except ValidationError: result = None
        if result is None:
            raw = gemini_text(prompt + '\nYour previous output was invalid. Return valid JSON only.')
            parsed = safe_json(raw)
            try: result = Extraction.model_validate(parsed).model_dump() if parsed is not None else None
            except ValidationError: result = None
        if result:
            if leaked_decoy_data(_caller_text(transcript)):
                result['risk_score']=min(100,result['risk_score']+30)
                result['summary'] = result['summary'].rstrip('.') + '; uses leaked decoy data.'
            return result
    result = offline_extract(transcript)
    if leaked_decoy_data(_caller_text(transcript)) and 'uses leaked decoy data' not in result['summary'].lower():
        result['risk_score']=min(100,result['risk_score']+30); result['summary'] += ' Uses leaked decoy data.'
    return result

def apply_repeat_caller(result, prior_context):
    """Same-scammer memory: raise the risk by 20 per earlier call from this number (capped at +40), flag it, note it in the summary.
    'repeat_caller' is decided here from our own records, never by the LLM, so it is always cleared first."""
    result['red_flags'] = [f for f in result.get('red_flags', []) if f != 'repeat_caller']
    try:
        prior_calls = int((prior_context or {}).get('prior_calls') or 0)
    except (TypeError, ValueError):
        prior_calls = 0
    if prior_calls <= 0:
        return result
    result['risk_score'] = min(100, result['risk_score'] + min(20 * prior_calls, 40))
    result['red_flags'].append('repeat_caller')
    when = (prior_context.get('last_call_date') or 'an earlier date')
    result['summary'] = result['summary'].rstrip() + f" Repeat caller — previously contacted on {when}."
    return result

def extract(transcript, prior_context=None):
    """Extract scam intelligence. prior_context (from db.build_prior_context) marks a returning caller."""
    return apply_repeat_caller(_extract(transcript), prior_context)
