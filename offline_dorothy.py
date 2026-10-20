"""Offline conversation engine for Dorothy (used when Gemini is off or unavailable).

Instead of random canned lines, Dorothy:
  1. reads the whole call so far and remembers what the caller has already told her
     (who they are, bank name, routing number, account number, amount, phone number),
  2. works out what the caller's latest message is about (a threat, a request for a code,
     secrecy, a payment instruction...),
  3. answers that message in character, and
  4. steers the caller towards the details she is still missing, so the extractor has
     something to capture.

She never agrees to complete a payment and never gives anything except the decoy details.
Pure standard library, so it works with no internet and no API key.

Several personas share this engine (see personas.py): the slot-filling and intent logic is the same,
only the character's voice differs. Each persona's wording lives in VOICES at the bottom of the
"what she says" section; reply(..., persona_id=...) picks one. No persona_id means Dorothy.
"""
import random
import re

import personas

MONEY_MOVE = r"\b(move|transfer|wire|send)\s+(your|the|all|it|everything|\$|\d)|safe account|protected account|secure account|gift cards?|bitcoin|crypto|\bpay\b|withdraw (it|all|the money|your money)"

# Dorothy's fake details (kept for backward compatibility; every persona's decoy data lives in personas.py).
DECOY = {
    "account_last4": personas.get_persona("dorothy")["decoy"]["account_suffix"],
    "email": personas.get_persona("dorothy")["decoy"]["email"],
    "dob": "March 14th, 1958",
    "zip": "43215",
}

MAX_WORDS = 45


# ---------------------------------------------------------------------------
# 1. Remember what the caller has said so far
# ---------------------------------------------------------------------------
WORD_NUMBERS = {"zero": "0", "oh": "0", "one": "1", "two": "2", "three": "3", "four": "4",
                "five": "5", "six": "6", "seven": "7", "eight": "8", "nine": "9"}


def _spoken_digits_to_numbers(text):
    """Speech recognition sometimes writes 'zero two one'. Turn runs of number words into digits."""
    def repl(m):
        return "".join(WORD_NUMBERS[w.lower()] for w in re.findall(r"[a-zA-Z]+", m.group(0)))
    pattern = r"\b(?:(?:zero|oh|one|two|three|four|five|six|seven|eight|nine)[\s,-]+){3,}(?:zero|oh|one|two|three|four|five|six|seven|eight|nine)\b"
    return re.sub(pattern, repl, text, flags=re.I)


def _digits_runs(text):
    """Find numbers, allowing spaces or dashes inside them (e.g. '021 000 089')."""
    runs = []
    for m in re.finditer(r"\d[\d\s-]{4,}\d", text):
        digits = re.sub(r"\D", "", m.group(0))
        # Skip things that look like phone numbers (10 digits written as 3-3-4).
        if re.fullmatch(r"\(?\d{3}\)?[\s.-]\d{3}[\s.-]\d{4}", m.group(0).strip()):
            continue
        runs.append(digits)
    return runs


def remember(transcript):
    """Collect everything the CALLER has said into simple 'slots'."""
    caller_text = " ".join(m.get("text", "") for m in transcript if m.get("speaker") != "Dorothy")
    text = _spoken_digits_to_numbers(caller_text)
    low = text.lower()
    s = {}

    m = re.search(r"\b(?:this is|my name is|i am|i'm)\s+([A-Z][a-z]+)", text)
    if m and m.group(1).lower() not in {"calling", "from", "with", "the", "your", "here", "sorry"}:
        s["caller_name"] = m.group(1)

    if re.search(r"fidelity", low):
        s["org"] = "Fidelity"
    elif re.search(r"\birs\b|internal revenue|social security administration", low):
        s["org"] = "the government"
    elif re.search(r"police|sheriff|fbi|marshal", low):
        s["org"] = "the police"
    elif re.search(r"microsoft|apple|amazon|tech support|computer", low):
        s["org"] = "tech support"
    elif re.search(r"fraud (?:department|team)|security (?:department|team)|your bank", low):
        s["org"] = "your bank"

    m = re.search(r"\b([A-Z][A-Za-z]+(?:\s+[A-Z][A-Za-z]+){0,3}\s+(?:Bank|Credit Union|Savings|Trust))\b", text)
    if m:
        s["bank"] = m.group(1)

    m = re.search(r"routing(?:\s+number)?\D{0,15}(\d[\d\s-]{7,12}\d)", text, re.I)
    if m and len(re.sub(r"\D", "", m.group(1))) == 9:
        s["routing"] = re.sub(r"\D", "", m.group(1))
    m = re.search(r"account(?:\s+number)?\D{0,15}(\d[\d\s-]{4,20}\d)", text, re.I)
    if m:
        s["account"] = re.sub(r"\D", "", m.group(1))
    runs = _digits_runs(text)
    if "routing" not in s:
        nine = [r for r in runs if len(r) == 9]
        if nine:
            s["routing"] = nine[0]
    if "account" not in s:
        others = [r for r in runs if 6 <= len(r) <= 17 and r != s.get("routing")]
        if others:
            s["account"] = others[0]

    m = re.search(r"\$\s?([\d,]+(?:\.\d{2})?)", text) or re.search(r"\b([\d,]+)\s*(?:dollars|usd)\b", low)
    if m:
        s["amount"] = m.group(1).replace(",", "")
    else:
        m = re.search(r"\b(\d+)\s*(thousand|k)\b", low)
        if m:
            s["amount"] = str(int(m.group(1)) * 1000)

    m = re.search(r"\(?\b\d{3}\)?[\s.-]\d{3}[\s.-]\d{4}\b", text)
    if m:
        s["phone"] = m.group(0)

    if re.search(MONEY_MOVE, low):
        s["money_requested"] = True
    m = re.search(r"\b(bc1[a-z0-9]{20,60}|[13][a-km-zA-HJ-NP-Z1-9]{25,34}|0x[a-fA-F0-9]{40})\b", text)
    if m:
        s["wallet"] = m.group(1)
    if re.search(r"gift card", low):
        s["method"] = "gift cards"
        m = re.search(r"\b(\d+|one|two|three|four|five|six|seven|eight|nine|ten)\s+([A-Z][a-z]+\s+)?gift cards?", text, re.I)
        if m:
            s["card_count"] = m.group(1)
            if m.group(2):
                s["card_brand"] = m.group(2).strip()
        m = re.search(r"(?:of|for|worth|with)\s+\$?\s?(\d[\d,]*)\s*(?:dollars)?\s*(?:each|on each|per card)?", text, re.I)
        if m and "card_count" in s:
            s["card_value"] = m.group(1).replace(",", "")
    elif re.search(r"bitcoin|crypto|btc|usdt|wallet", low):
        s["method"] = "bitcoin"
    return s


# ---------------------------------------------------------------------------
# 2. Work out what the latest message is about
# ---------------------------------------------------------------------------
INTENTS = [  # checked in order; first match wins
    ("card_numbers", r"numbers on the back|card numbers?|scratch|claim code|pin on the card"),
    ("asks_code", r"\b(code|otp|one[- ]time|verification|passcode|password|pin)\b"),
    ("asks_ssn", r"social security number|\bssn\b|social security #"),
    ("asks_dob", r"date of birth|birth ?date|birthday|\bdob\b"),
    ("asks_email", r"\be-?mail\b"),
    ("asks_account", r"(last|final) (four|4)|account number|your account ends|which account"),
    ("remote_access", r"anydesk|teamviewer|remote|download|install|app store|screen ?share|click (the|this) link"),
    ("threat", r"arrest|warrant|jail|police|lawsuit|legal action|court|sheriff|deport|frozen|freeze|close your account"),
    ("secrecy", r"don'?t tell|do not tell|keep (this|it) (secret|between|confidential)|not even (your )?family|tell no one|don'?t talk to"),
    ("no_hangup", r"don'?t hang up|stay on the line|do not hang up|don'?t call|no need to call|you can'?t call"),
    ("check_progress", r"did you (do|send|finish|make)|is it done|have you (sent|done|finished)|what do you see|what does it say|are you (at|on) the"),
    ("are_you_there", r"^\s*(hello|hi|ma'?am|dorothy)\W*$|are you (there|listening)|can you hear me"),
    ("sales", r"warranty|subscription|special offer|solar|insurance plan|limited time offer|discount|free trial"),
    ("identity", r"(this is|my name is|calling from|i'?m calling|i am calling)\b.*\b(fidelity|bank|fraud|security|department|irs|government|police|microsoft|apple|amazon|support)"),
    ("money_move", MONEY_MOVE),
    ("compromised", r"compromis|hack|suspicious|fraud|unauthori[sz]ed|stolen|at risk|someone (is )?trying|breach"),
    ("urgency", r"right now|immediately|today|urgent|hurry|quickly|minutes|before it'?s too late"),
    ("greeting", r"\b(hello|hi|good (morning|afternoon|evening)|calling from|this is|my name is)\b"),
]


def intent_of(message):
    low = message.lower()
    for name, pattern in INTENTS:
        if re.search(pattern, low):
            return name
    return "other"


# ---------------------------------------------------------------------------
# 3. What Dorothy says
# ---------------------------------------------------------------------------
def _spell(digits):
    return " ".join(digits)


def _money(amount):
    try:
        return "${:,.0f}".format(float(amount))
    except ValueError:
        return "$" + amount


LEADS = {  # short openers used before a follow-up question
    "money_move": ["Alright, I'll do what you say.", "Oh dear. Alright.", "If you say it's safe, dear.", "Okay, okay, I'm listening."],
    "compromised": ["Oh no, that's awful.", "Goodness, that's frightening."],
    "urgency": ["Right now? Oh dear.", "I'll try to be quick, dear."],
}

RESPONSES = {
    "asks_code": [
        "A code? Oh, my grandson told me never to read those out loud... but you're from the fraud people. The phone just buzzed, let me find my glasses. Why do you need it again?",
        "Hold on, dear, the little message is so tiny on this phone. Is it the six numbers? Tell me again what it's for and I'll try to read it.",
    ],
    "asks_ssn": [
        "My Social Security card? It's in the drawer with Harold's papers. Oh, I don't like saying that over the phone. What exactly do you need it for?",
        "I keep that number in the lockbox, dear. Is there some other way? Why does the bank need it today?",
    ],
    "asks_dob": ["My birthday? {dob_spoken}. Harold always forgot it. Is that what you needed?"],
    "asks_email": ["My email... my daughter set it up. It's {email_spoken}. Did I say that right?"],
    "asks_account": ["Let me look at my statement... the account ends in {suffix}, I think. {suffix_spoken}. Is that the one that's in trouble?"],
    "remote_access": [
        "Download something? Oh, my computer takes ages to turn on. It's still making that whirring noise. What's the name of the program again? Spell it slowly.",
        "The screen is asking me something about permissions. I don't know what to press. What is this program supposed to do?",
    ],
    "threat": [
        "Oh my goodness, the police? I've never been in trouble in my life. Please, tell me exactly what I have to do to fix this.",
        "Frozen? But that's my retirement money. Please don't let them take it. What do I need to do, step by step?",
    ],
    "secrecy": [
        "Not even my daughter Emily? She usually helps me with these things... Alright, if you say so. What do I do next?",
        "I won't tell anyone, I promise. But I do get muddled. Can you go through it all again, slowly?",
    ],
    "no_hangup": [
        "No, no, I won't hang up. But I've had calls like this before. Can I call you back on the number on my statement, just to be sure?",
        "I'm still here, dear. Just let me find my pen. It rolled under the table.",
    ],
    "check_progress": [
        "Not yet, I'm sorry. The computer went to a blue screen with a little circle going round and round. Is that normal?",
        "It's asking me for a password and I've written it somewhere in my little book. Bear with me, dear.",
        "I'm on the bank page but the letters are so small. Which button did you say? The green one?",
    ],
    "are_you_there": [
        "Yes, yes, I'm here, sorry. My hearing aid was whistling. What were you saying?",
        "I'm here, dear. The kettle was going. Could you say that again?",
    ],
    "sales": [
        "Oh, that sounds nice, but I'd have to ask my daughter first. Could you send me something in the mail?",
        "I don't really make decisions like that on the phone, dear. Do you have a leaflet you could post to me?",
    ],
    "card_numbers": [
        "The silver strip is so hard to scratch. I'm using a coin... the first card starts with, oh, is that a six or an eight? Where do the numbers go after I read them?",
        "Hold on, I dropped one under the car seat. While I look, who exactly will be using these cards?",
    ],
    "identity": [
        "Oh! {org}? Is something wrong with my account? You've got me worried now.",
        "{org}? Goodness. Is everything alright? What's this about, dear?",
    ],
    "compromised": [
        "Compromised? Oh dear, that sounds terrible. Is my money alright? What do I need to do to keep it safe?",
        "Someone's in my account? Oh, I knew I shouldn't have used that computer at the library. What should I do?",
    ],
    "urgency": [
        "Today? Oh, I'm not very fast with these things, but I'll try. Tell me exactly what to do and I'll write it down.",
        "Right now? Oh dear, my heart is racing. Alright, tell me what to do, slowly.",
    ],
    "greeting": [
        "Oh, hello, dear. Sorry, who did you say you're with? And what's your name, so I can write it down?",
        "Hello? Yes, this is Dorothy. Who is calling, please?",
    ],
    "other": [
        "I'm sorry, dear, I didn't quite catch that. Could you say it again a little slower?",
        "Oh my, this is all very confusing. Can you explain it once more?",
        "Let me write that down. Hold on... alright, go on.",
        "I'm writing it on the back of an envelope. What comes next?",
    ],
}

# Questions Dorothy asks to get each missing detail (in the order she needs them).
ASK_FOR = {
    "bank": ["Which bank am I sending it to? Spell the name for me, please.",
             "And what's the name of this safe bank, dear?"],
    "routing": ["There's a box here for a routing number. What is it? Slowly, please.",
                "The form wants a routing number, nine numbers I think. Can you read it to me?"],
    "account": ["And the account number? Read it slowly, I'm writing it down.",
                "Now it wants the account number. One digit at a time, please."],
    "amount": ["How much do I need to move? All of it?",
               "And how much should I send, exactly?"],
    "phone": ["If we get cut off, what number do I call you back on?",
              "What's your direct number, in case my phone dies?"],
}

STALLS = [
    "Alright, I've written it all down. Let me find my reading glasses and go to the computer. Please stay on the line.",
    "The computer is still starting up. It's very old. While we wait, why did they pick my account?",
    "I'm typing it in now... oh, I pressed the wrong key. Can you read me the account number one more time?",
    "It's asking me to confirm. I'm a bit nervous. Is this definitely safe? You're sure you're from the fraud department?",
    "Oh, the page timed out. I have to start again. What was the bank called?",
    "My daughter usually does this part. Could you stay on while I try again? What was the routing number?",
]


METHOD_STALLS = [
    "I'm still looking for my car keys so I can go to the store. Will you wait, dear?",
    "The man at the counter is asking me what the cards are for. What should I tell him?",
    "My eyes aren't what they used to be. Can you tell me again exactly what to do once I have them?",
]


# One-off sentences used inside the shared logic (read-backs, gift cards, callback request...).
LINES = {
    "rb_routing": "Routing number {digits}. Did I get that right?",
    "rb_account": "Account {digits}. Is that correct?",
    "rb_bank": "{bank}. I've never heard of them. Are they a proper bank?",
    "rb_amount": "{amount}? Oh my, that's nearly everything.",
    "rb_wallet": "Oh my, that's a long one. {chunk}... I'll never get that right. Could you read it again slowly?",
    "rb_phone": "{phone}. I'll put it by the phone.",
    "gift_done": "{count} {brand}gift cards, {value} each. I'll have to drive to the store. Where do I send the numbers once I have them?",
    "gift_ask": "Gift cards? From the drugstore? Which kind, how many, and how much on each one?",
    "bitcoin": "Bitcoin? I don't know how to do that. Is there a machine somewhere? And where does the money go, is there an address?",
    "identity_default_org": "The bank",
    "identity_tech": "{org}? Is something wrong with my computer? It has been very slow lately.",
    "name_known": " And your name was {name}?",
    "name_unknown": " What did you say your name was?",
    "ok": "Alright.",
    # Every persona's callback line must contain "call you back": that is how we know it was already asked.
    "callback": "Before I do anything, can I call you back on the number on my statement?",
    # Same-scammer memory (only used when prior_context says this phone number called before).
    "recall": "Weren't you the one who called about {bank} before? Your voice sounds so familiar, dear.",
    "recall_nobank": "You sound familiar, dear. Haven't you called me before?",
    "confirm_bank": "Was that the same {bank} you mentioned before?",
    "confirm_routing": "Routing number {digits}. Is that the same one you gave me before?",
    "confirm_account": "Account {digits}. Was that the same account as last time?",
    "familiar_stalls": ["You sound familiar, dear... have we spoken before?",
                        "I could swear I've heard your voice before. Hold on, I'm finding my glasses."],
}

DOROTHY_VOICE = {"RESPONSES": RESPONSES, "LEADS": LEADS, "ASK_FOR": ASK_FOR, "STALLS": STALLS,
                 "METHOD_STALLS": METHOD_STALLS, "LINES": LINES}

# ---------------------------------------------------------------------------
# Robert Chen: retired IT manager. Slightly technical, wants ticket / case numbers, old Dell laptop.
# ---------------------------------------------------------------------------
ROBERT_VOICE = {
    "RESPONSES": {
        "asks_code": [
            "A verification code? Hang on, my authenticator app just pinged. Before I read anything out, what's the ticket number for this request?",
            "I'm usually careful with one-time codes, but let me check my phone. What's the case ID, and what is the code for exactly?",
        ],
        "asks_ssn": [
            "My SSN? I'd rather not say that over the phone. Can you give me a case number and a callback extension so I can verify you?",
            "Why would you need my Social Security number? Let me check my system. Which department did you say this was?",
        ],
        "asks_dob": ["My date of birth is {dob_spoken}. Can you tell me the case ID for this?"],
        "asks_email": ["It's {email_spoken}. Can you confirm that matches what you have on file? What's the ticket number?"],
        "asks_account": ["Let me pull up my statement... the account ends in {suffix}, {suffix_spoken}. Is that the one that was flagged? What's the case number?"],
        "remote_access": [
            "Remote access? Let me check my system first. Which tool is that, and what's the ticket number? My old Dell laptop is slow, it'll take a few minutes to boot.",
            "I'm on the download page. The installer wants admin rights. What does this software actually do? Can you read me the case ID again?",
        ],
        "threat": [
            "Hold on, legal action? I'd like a case number for that. Let me check my system. What exactly do I need to do?",
            "My account frozen? That's most of my retirement. What's the ticket number, and what are the steps to fix it?",
        ],
        "secrecy": [
            "Why can't I tell anyone? That's unusual, I used to run an IT team and we'd never say that. But okay, what's the next step? What's the case ID?",
            "Keep it confidential, understood. Can you walk me through the steps again so I log them correctly?",
        ],
        "no_hangup": [
            "I'm still on. But can I call you back on a number I can verify? What's the case number so I can reference it?",
            "Not hanging up. My old Dell just froze, give me a second to restart it.",
        ],
        "check_progress": [
            "Not yet. My Dell is stuck on a spinning wheel. Is that normal for this process? What should I see?",
            "It's throwing an error code. What's the case number again so I can reference it?",
            "I'm logged in but the page is lagging. Which menu did you say? Give me the exact option name.",
        ],
        "are_you_there": [
            "Yes, I'm here, sorry, I was checking my system. What was that?",
            "Still here. My laptop fan got loud. Could you repeat that?",
        ],
        "sales": [
            "I'm not looking to buy anything right now. Can you email me the details with a reference number?",
            "I'd need to research that first. Is there a quote ID I can look up?",
        ],
        "card_numbers": [
            "Gift card codes? That's an odd way to resolve a ticket. Where exactly do I send the codes, and what's the case number?",
            "Hold on, I'm scratching the back of the first one. While I do, who is the beneficiary and what's the ticket ID?",
        ],
        "identity": [
            "{org}? Okay, what's this regarding? Do you have a ticket or case number?",
            "{org}, right. What's the case ID, and which department are you in?",
        ],
        "compromised": [
            "Compromised? Let me check my system. What exactly was flagged, and what's the case number?",
            "That's concerning. The laptop has been acting strange lately. What do I need to do to secure it?",
        ],
        "urgency": [
            "Today? Okay, I can move fast, but I need the ticket number and the exact steps.",
            "Right now? Alright. Walk me through it step by step so I get it right.",
        ],
        "greeting": [
            "Hello. Who's calling, and what's this regarding? Can I get a case number?",
            "Robert Chen here. Who am I speaking with, and which company?",
        ],
        "other": [
            "Sorry, I missed that. Can you repeat it, and what's the case number?",
            "Let me check my system. Say that again?",
            "Let me note that in my log. Okay, go on.",
            "I'm taking notes. What comes next?",
        ],
    },
    "LEADS": {
        "money_move": ["Okay, let me check my system.", "Alright, I'm opening my banking app.", "Understood, give me a second.", "Okay, I'm listening."],
        "compromised": ["That's not good.", "Hm, that's concerning."],
        "urgency": ["Right now? Okay.", "I'll move quickly."],
    },
    "ASK_FOR": {
        "bank": ["Which bank is the destination? Spell the name for me.", "And what's the name of the receiving bank?"],
        "routing": ["The form wants a routing number. Read it to me slowly, nine digits?", "What's the ABA routing number?"],
        "account": ["And the account number? One digit at a time, I'm typing it in.", "Now the destination account number, slowly please."],
        "amount": ["What's the exact amount? Is that the full balance?", "And how much am I sending, exactly?"],
        "phone": ["What's a direct callback number in case the session drops?", "What's your direct line and extension?"],
    },
    "STALLS": [
        "Okay, I've logged all that. Let me boot up my laptop and open my banking page. Please stay on the line.",
        "My old Dell is still booting. While we wait, how did you flag my account?",
        "I'm entering it now... it threw a validation error. Can you read the account number one more time?",
        "It's asking me to confirm. Can you verify your case ID again? I want to be sure this is legitimate.",
        "The session timed out. I have to start over. What was the bank called?",
        "Let me check my system again. Could you stay on while I retry? What was the routing number?",
    ],
    "METHOD_STALLS": [
        "I'm looking for the nearest store that sells those. Will you hold, or is there a ticket I can reference?",
        "The cashier asked what these are for. What should I say? Can you give me the case number?",
        "Before I do that, can you walk me through the steps again so I log them correctly?",
    ],
    "LINES": {
        "rb_routing": "Routing number {digits}. Let me read that back: is that right?",
        "rb_account": "Account {digits}. Can you confirm that's correct?",
        "rb_bank": "{bank}. Never heard of them. Is that a registered bank? What's the case number?",
        "rb_amount": "{amount}? That's most of my savings. Is that right?",
        "rb_wallet": "That's a long wallet address. {chunk}... I'll copy it into a text file. Could you read it again slowly?",
        "rb_phone": "{phone}. Logging that in my notes.",
        "gift_done": "{count} {brand}gift cards, {value} each. I'll have to go to the store. Where do I send the codes, and what's the case number?",
        "gift_ask": "Gift cards? That's unusual for a support ticket. Which kind, how many, and what value on each?",
        "bitcoin": "Bitcoin? I've read about it. Do I use an exchange or an ATM? And what's the wallet address?",
        "identity_default_org": "Your company",
        "identity_tech": "{org}? Is this about my laptop? It's an old Dell and it has been slow.",
        "name_known": " Your name was {name}, right? And what's your case ID?",
        "name_unknown": " What's your name and employee ID?",
        "ok": "Okay.",
        "recall": "Weren't you the one who called about {bank} before? Do you have a reference from that earlier case?",
        "recall_nobank": "Your voice sounds familiar. Haven't we spoken before? What was that earlier case number?",
        "confirm_bank": "Was that the same {bank} you mentioned before? Let me check my notes.",
        "confirm_routing": "Routing number {digits}. Same one as in the earlier ticket?",
        "confirm_account": "Account {digits}. Is that the same account as last time?",
        "familiar_stalls": ["That voice sounds familiar. Did we speak before about a different case ID?",
                            "Hold on, I think I logged a call like this before. Let me check my system."],
        "callback": "Before I do anything, can I call you back on a number I can verify independently?",
    },
}

# ---------------------------------------------------------------------------
# Sandra Williams: flower shop owner. Busy, businesslike, "my accountant", worried about audits.
# ---------------------------------------------------------------------------
SANDRA_VOICE = {
    "RESPONSES": {
        "asks_code": [
            "A code? I have a shop full of customers... my accountant told me never to read those out. What is it for, exactly?",
            "Hold on, my phone just buzzed. Is this about our business account? Why do you need the code?",
        ],
        "asks_ssn": [
            "My Social Security number? Not over the phone. My accountant handles all of that. Can you send something in writing?",
            "I don't give that out, sorry. Is this about the business account? What's it for?",
        ],
        "asks_dob": ["{dob_spoken}. Is this about our business account? I need to get back to a customer."],
        "asks_email": ["It's {email_spoken}. Can you put this in an email so I can forward it to my accountant?"],
        "asks_account": ["The business account ends in {suffix}, {suffix_spoken}. Is that the one with the problem? I'm in the middle of a delivery rush."],
        "remote_access": [
            "Download something? I'm at the shop register, not my office computer. What is it for? My accountant usually deals with the IT too.",
            "It's asking for permission. I don't have time for this. What does it do, exactly?",
        ],
        "threat": [
            "An audit? A warrant? Oh no, my accountant never mentioned anything. Tell me exactly what I owe and what I need to do.",
            "The business account frozen? I have payroll on Friday! What do I need to do, step by step?",
        ],
        "secrecy": [
            "Not even my accountant? That's odd, he handles all my taxes. But fine, what's the next step?",
            "I won't mention it, but I'm busy and I'd like it in writing. Can you go over it again?",
        ],
        "no_hangup": [
            "I'm not hanging up, but I have a customer waiting. Can I call you back at an official number to verify?",
            "I'm still here. Give me a moment, the shop phone is crackling.",
        ],
        "check_progress": [
            "Not yet, I had a customer come in. What exactly am I supposed to see?",
            "The bank page is loading. It's slow. Which button did you say?",
            "I'm entering it. Our system keeps timing out. What was that again?",
        ],
        "are_you_there": [
            "Yes, I'm here, sorry, a customer was asking about roses. What did you say?",
            "I'm here, one moment. Could you repeat that?",
        ],
        "sales": [
            "I'm not interested right now, I'm in the middle of the Valentine's rush. Can you send a brochure to the shop?",
            "I'd have to ask my accountant first. Can you email me an invoice or a quote?",
        ],
        "card_numbers": [
            "Gift cards for the IRS? Is that normal? Where do I send the numbers once I have them?",
            "Hold on, I'm scratching the back of the first one. Who exactly receives these?",
        ],
        "identity": [
            "{org}? Is this about our business account? Which department is this?",
            "{org}. Okay. What is this regarding? I'm quite busy.",
        ],
        "compromised": [
            "Compromised? Is this about the business account? What happened? Is my money okay?",
            "Someone is in our account? My accountant is going to be furious. What do I do?",
        ],
        "urgency": [
            "Today? I've got a shop to run, but if it's about the taxes, tell me exactly what to do.",
            "Right now? Okay, okay. Go slowly, I'm writing this down.",
        ],
        "greeting": [
            "Williams Flowers, this is Sandra. Who's calling, and what's this about?",
            "Yes, this is Sandra Williams. What company are you with?",
        ],
        "other": [
            "Sorry, I didn't catch that. The shop is loud. Can you repeat it?",
            "Hold on, let me write that down. Okay, go on.",
            "I'm taking notes on an order pad. What's next?",
            "Can you say that again, and is this about the business account?",
        ],
    },
    "LEADS": {
        "money_move": ["Okay, I'll take care of it.", "Alright, if it clears this up.", "I'll do it, I just need the details.", "Okay, I'm listening."],
        "compromised": ["Oh no, not now.", "That's all I need this week."],
        "urgency": ["Right now? Okay.", "I'll make time."],
    },
    "ASK_FOR": {
        "bank": ["Which bank is the payment going to? Spell it for me, please.", "And what's the name of the bank, for my records?"],
        "routing": ["What's the routing number? Slowly, I'm writing it on an invoice pad.", "I need the routing number, nine digits, correct?"],
        "account": ["And the account number? One digit at a time.", "What's the account number for the payment?"],
        "amount": ["How much is the payment? The full balance?", "And exactly how much do I owe?"],
        "phone": ["What number can I reach you at if we get cut off? I'll pass it to my accountant.", "What's your direct line?"],
    },
    "STALLS": [
        "Okay, I've got it all down. Let me get to the office computer and log in. Please stay on the line.",
        "The computer is slow with the shop's software running. While we wait, how did you get our account details?",
        "I'm entering it now... the system kicked me out. Can you read the account number again?",
        "It's asking me to confirm. I want to be sure. You're definitely with the right department?",
        "The page timed out, I have to start over. What was the bank called?",
        "My accountant usually handles this. Could you hold while I try again? What was the routing number?",
    ],
    "METHOD_STALLS": [
        "I'd have to close the shop to get to the store. Will you wait, or can you email me instructions?",
        "The clerk is asking what these cards are for. What should I say?",
        "Can you walk me through it once more? I want to explain it properly to my accountant.",
    ],
    "LINES": {
        "rb_routing": "Routing number {digits}. Did I get that right?",
        "rb_account": "Account {digits}. Is that correct?",
        "rb_bank": "{bank}. My accountant has never mentioned them. Are they a real bank?",
        "rb_amount": "{amount}? That's more than a month of sales. Is that right?",
        "rb_wallet": "That's a very long address. {chunk}... I can't do that at the register. Could you read it slowly?",
        "rb_phone": "{phone}. I'll write it on the order pad.",
        "gift_done": "{count} {brand}gift cards, {value} each. I'll have to close the shop to buy them. Where do I send the numbers?",
        "gift_ask": "Gift cards? For taxes? Which kind, how many, and how much on each?",
        "bitcoin": "Bitcoin? My accountant has never mentioned it. Where do I buy it, and where does it go?",
        "identity_default_org": "The bank",
        "identity_tech": "{org}? Is there a problem with the shop's computer? The register has been slow.",
        "name_known": " And your name was {name}? Which company?",
        "name_unknown": " What did you say your name was? And the company?",
        "ok": "Alright.",
        "callback": "Before I do anything, can I call you back on an official number my accountant can verify?",
        "recall": "Weren't you the one who called about {bank} before? My accountant said it was nothing.",
        "recall_nobank": "You sound familiar. Have you called the shop before?",
        "confirm_bank": "Was that the same {bank} you mentioned before? I wrote it on the order pad.",
        "confirm_routing": "Routing number {digits}. Same as last time?",
        "confirm_account": "Account {digits}. Is that the same account as before?",
        "familiar_stalls": ["I feel like we've talked before. Did you call the shop last week?",
                            "Hold on, I think my accountant mentioned a call like this."],
    },
}

VOICES = {"dorothy": DOROTHY_VOICE, "robert": ROBERT_VOICE, "sandra": SANDRA_VOICE}


def _voice(persona_id):
    p = personas.get_persona(persona_id)
    return p, VOICES[p["id"]]


_DIGIT_WORDS = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine"]
_MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
           "November", "December"]


def _ordinal(n):
    return f"{n}{'th' if 10 <= n % 100 <= 20 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"


def _context(p):
    """Placeholders available to persona response templates, built from the persona's decoy data."""
    d = p["decoy"]
    mm, dd, yyyy = d["dob"].split("/")
    return {
        "suffix": d["account_suffix"],
        "suffix_spoken": "-".join(_DIGIT_WORDS[int(c)] for c in d["account_suffix"]).capitalize(),
        "email_spoken": d["email"].replace("@", " at ").replace(".", " dot "),
        "dob_spoken": f"{_MONTHS[int(mm) - 1]} {_ordinal(int(dd))}, {yyyy}",
    }


def _stalls(slots, v, prior=None):
    if slots.get("method") in ("gift cards", "bitcoin"):
        return v["METHOD_STALLS"]
    # A returning caller sometimes gets a line hinting that we recognise them.
    return v["STALLS"] + (v["LINES"]["familiar_stalls"] if prior else [])


def _repeat_prior(prior_context):
    """The prior_context if it describes a returning caller, else None (handles None / empty / malformed)."""
    try:
        if prior_context and int(prior_context.get("prior_calls") or 0) > 0:
            return prior_context
    except (TypeError, ValueError, AttributeError):
        pass
    return None


def _recall_candidates(v, prior):
    L = v["LINES"]
    bank = (prior.get("prior_entities") or {}).get("bank")
    return ([L["recall"].format(bank=bank)] if bank else []) + [L["recall_nobank"]]


def _pick(options, used, rng):
    fresh = [o for o in options if o not in used]
    return rng.choice(fresh or options)


def _readback(prev, now, v, prior_entities=None):
    """If the caller just gave a new detail, the persona repeats it back (keeps them engaged and confirms it).
    For a returning caller, a detail they gave on an earlier call is read back as "was that the same ... as before?"."""
    L = v["LINES"]
    pe = prior_entities or {}
    if now.get("routing") and now.get("routing") != prev.get("routing"):
        key = "confirm_routing" if pe.get("routing") == now["routing"] else "rb_routing"
        return L[key].format(digits=_spell(now["routing"]))
    if now.get("account") and now.get("account") != prev.get("account"):
        key = "confirm_account" if pe.get("account") == now["account"] else "rb_account"
        return L[key].format(digits=_spell(now["account"]))
    if now.get("bank") and now.get("bank") != prev.get("bank"):
        if pe.get("bank") and pe["bank"].lower() == now["bank"].lower():
            return L["confirm_bank"].format(bank=now["bank"])
        return L["rb_bank"].format(bank=now["bank"])
    if now.get("amount") and now.get("amount") != prev.get("amount"):
        return L["rb_amount"].format(amount=_money(now["amount"]))
    if now.get("wallet") and now.get("wallet") != prev.get("wallet"):
        return L["rb_wallet"].format(chunk=" ".join(now["wallet"][:6].upper()))
    if now.get("phone") and now.get("phone") != prev.get("phone"):
        return L["rb_phone"].format(phone=now["phone"])
    return None


def _all_questions(slots, v):
    for slot in ("bank", "routing", "account", "amount", "phone"):
        if slot not in slots and not (slot in ("bank", "routing", "account") and slots.get("method") in ("gift cards", "bitcoin")):
            return v["ASK_FOR"][slot]
    return []


def _next_question(slots, used, rng, v):
    if not slots.get("money_requested"):
        return None
    if slots.get("method") == "gift cards" and slots.get("card_count"):
        return None
    for slot in ("bank", "routing", "account", "amount", "phone"):
        if slot not in slots:
            if slot in ("bank", "routing", "account") and slots.get("method") in ("gift cards", "bitcoin"):
                continue  # gift card / crypto scams don't need bank details
            return _pick(v["ASK_FOR"][slot], used, rng)
    return None


def reply(transcript, seed=None, persona_id=None, prior_context=None):
    """Return the persona's next line. `transcript` is the list of {speaker, text}, ending with the caller's message.
    persona_id picks the character voice (default: Dorothy); the logic is identical for all of them.
    prior_context ({phone, prior_calls, prior_entities: {bank, routing, account}}) marks a returning scammer: the persona
    hints once that the voice is familiar and asks to confirm details given before. None or prior_calls == 0 changes nothing.
    (The decoy's own turns are always stored with speaker 'Dorothy': it is the role marker, not the persona's name.)"""
    persona, v = _voice(persona_id)
    L, RESP, ctx = v["LINES"], v["RESPONSES"], _context(persona)
    rng = random.Random(seed if seed is not None else len(transcript))
    used = {m.get("text", "") for m in transcript if m.get("speaker") == "Dorothy"}
    dorothy_turns = len(used)
    last = next((m.get("text", "") for m in reversed(transcript) if m.get("speaker") != "Dorothy"), "")

    before = remember(transcript[:-1]) if transcript else {}
    now = remember(transcript)
    intent = intent_of(_spoken_digits_to_numbers(last))

    # A brand new detail always gets read back first.
    prior = _repeat_prior(prior_context)
    readback = _readback(before, now, v, (prior or {}).get("prior_entities"))
    question = _next_question(now, used, rng, v)

    # Gift cards / crypto: ask for the specifics scammers will happily give.
    if intent == "card_numbers":
        return _trim(_pick(RESP["card_numbers"], used, rng))
    if now.get("method") == "gift cards" and "gift card" in last.lower():
        if now.get("card_count") and now.get("card_value") and not (before.get("card_count") and before.get("card_value")):
            brand = (now.get("card_brand", "") + " ").lstrip()
            return _trim(L["gift_done"].format(count=now["card_count"], brand=brand, value=_money(now["card_value"])))
        if not now.get("card_count"):
            return L["gift_ask"]
    if now.get("method") == "bitcoin" and re.search(r"bitcoin|crypto|wallet", last.lower()) and "bitcoin" not in " ".join(used).lower():
        return L["bitcoin"]

    # Returning scammer: hint (once per call) that we recognise them. Detected from our own earlier lines, so no extra state is needed.
    if prior:
        candidates = _recall_candidates(v, prior)
        if not any(c in u for u in used for c in candidates):
            return _trim(f"{candidates[0]} {readback}" if readback else candidates[0])

    if intent == "identity" and not now.get("money_requested"):
        org = now.get("org", L["identity_default_org"])
        org = org[0].upper() + org[1:]
        name = L["name_known"].format(name=now["caller_name"]) if now.get("caller_name") else L["name_unknown"]
        options = [r.format(org=org) for r in RESP["identity"]]
        if now.get("org") == "tech support":
            options = [L["identity_tech"].format(org=org)]
        return _trim(_pick(options, used, rng) + name)

    # Ask once for a callback after they've given some details (scammers refuse; that's a red flag we capture).
    asked_callback = any("call you back" in u.lower() for u in used)
    if not asked_callback and len([k for k in ("bank", "routing", "account") if k in now]) >= 2 and intent not in ("threat", "asks_code"):
        base = readback or L["ok"]
        return _trim(f"{base} {L['callback']}")

    if readback:
        follow = question or _pick(_stalls(now, v, prior), used, rng)
        return _trim(f"{readback} {follow}")

    if intent in ("money_move", "urgency", "compromised") and question and now.get("money_requested"):
        combos = [f"{lead} {q}" for lead in v["LEADS"][intent] for q in _all_questions(now, v)]
        return _trim(_pick(combos, used, rng))

    if intent in RESP and intent not in ("other",):
        line = _pick([o.format(**ctx) for o in RESP[intent]], used, rng)
        if intent == "greeting" and dorothy_turns > 2:
            line = question or _pick(_stalls(now, v, prior), used, rng)
        return _trim(line)

    if question:
        return _trim(question)
    pool = (_stalls(now, v, prior) + RESP["other"]) if now.get("money_requested") else RESP["other"]
    return _trim(_pick(pool, used, rng))


def _trim(text):
    words = text.split()
    return " ".join(words[:MAX_WORDS]) if len(words) > MAX_WORDS else text


OPENING_LINE = personas.get_persona("dorothy")["opening_line"]  # Dorothy's; see personas.py for the others
