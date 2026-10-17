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
"""
import random
import re

MONEY_MOVE = r"\b(move|transfer|wire|send)\s+(your|the|all|it|everything|\$|\d)|safe account|protected account|secure account|gift cards?|bitcoin|crypto|\bpay\b|withdraw (it|all|the money|your money)"

# Fake details Dorothy is allowed to give (they are the "leaked" decoy data).
DECOY = {
    "account_last4": "4417",
    "email": "dorothy.miller.decoy1@example.com",
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
    "asks_dob": [f"My birthday? March 14th, 1958. Harold always forgot it. Is that what you needed?"],
    "asks_email": [f"My email... my daughter set it up. It's dorothy dot miller dot decoy1 at example dot com. Did I say that right?"],
    "asks_account": [f"Let me look at my statement... the account ends in {DECOY['account_last4']}, I think. Four-four-one-seven. Is that the one that's in trouble?"],
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


def _stalls(slots):
    return METHOD_STALLS if slots.get("method") in ("gift cards", "bitcoin") else STALLS


def _pick(options, used, rng):
    fresh = [o for o in options if o not in used]
    return rng.choice(fresh or options)


def _readback(prev, now):
    """If the caller just gave a new detail, Dorothy repeats it back (keeps them engaged and confirms it)."""
    if now.get("routing") and now.get("routing") != prev.get("routing"):
        return f"Routing number {_spell(now['routing'])}. Did I get that right?"
    if now.get("account") and now.get("account") != prev.get("account"):
        return f"Account {_spell(now['account'])}. Is that correct?"
    if now.get("bank") and now.get("bank") != prev.get("bank"):
        return f"{now['bank']}. I've never heard of them. Are they a proper bank?"
    if now.get("amount") and now.get("amount") != prev.get("amount"):
        return f"{_money(now['amount'])}? Oh my, that's nearly everything."
    if now.get("wallet") and now.get("wallet") != prev.get("wallet"):
        w = now["wallet"]
        return f"Oh my, that's a long one. {' '.join(w[:6].upper())}... I'll never get that right. Could you read it again slowly?"
    if now.get("phone") and now.get("phone") != prev.get("phone"):
        return f"{now['phone']}. I'll put it by the phone."
    return None


def _all_questions(slots):
    for slot in ("bank", "routing", "account", "amount", "phone"):
        if slot not in slots and not (slot in ("bank", "routing", "account") and slots.get("method") in ("gift cards", "bitcoin")):
            return ASK_FOR[slot]
    return []


def _next_question(slots, used, rng):
    if not slots.get("money_requested"):
        return None
    if slots.get("method") == "gift cards" and slots.get("card_count"):
        return None
    for slot in ("bank", "routing", "account", "amount", "phone"):
        if slot not in slots:
            if slot in ("bank", "routing", "account") and slots.get("method") in ("gift cards", "bitcoin"):
                continue  # gift card / crypto scams don't need bank details
            return _pick(ASK_FOR[slot], used, rng)
    return None


def reply(transcript, seed=None):
    """Return Dorothy's next line. `transcript` is the list of {speaker, text}, ending with the caller's message."""
    rng = random.Random(seed if seed is not None else len(transcript))
    used = {m.get("text", "") for m in transcript if m.get("speaker") == "Dorothy"}
    dorothy_turns = len(used)
    last = next((m.get("text", "") for m in reversed(transcript) if m.get("speaker") != "Dorothy"), "")

    before = remember(transcript[:-1]) if transcript else {}
    now = remember(transcript)
    intent = intent_of(_spoken_digits_to_numbers(last))

    # A brand new detail always gets read back first.
    readback = _readback(before, now)
    question = _next_question(now, used, rng)

    # Gift cards / crypto: ask for the specifics scammers will happily give.
    if intent == "card_numbers":
        return _trim(_pick(RESPONSES["card_numbers"], used, rng))
    if now.get("method") == "gift cards" and "gift card" in last.lower():
        if now.get("card_count") and now.get("card_value") and not (before.get("card_count") and before.get("card_value")):
            brand = (now.get("card_brand", "") + " ").lstrip()
            return _trim(f"{now['card_count']} {brand}gift cards, {_money(now['card_value'])} each. I'll have to drive to the store. Where do I send the numbers once I have them?")
        if not now.get("card_count"):
            return "Gift cards? From the drugstore? Which kind, how many, and how much on each one?"
    if now.get("method") == "bitcoin" and re.search(r"bitcoin|crypto|wallet", last.lower()) and "bitcoin" not in " ".join(used).lower():
        return "Bitcoin? I don't know how to do that. Is there a machine somewhere? And where does the money go, is there an address?"

    if intent == "identity" and not now.get("money_requested"):
        org = now.get("org", "The bank")
        org = org[0].upper() + org[1:]
        name = f" And your name was {now['caller_name']}?" if now.get("caller_name") else " What did you say your name was?"
        options = [r.format(org=org) for r in RESPONSES["identity"]]
        if now.get("org") == "tech support":
            options = [f"{org}? Is something wrong with my computer? It has been very slow lately."]
        return _trim(_pick(options, used, rng) + name)

    # Ask once for a callback after they've given some details (scammers refuse; that's a red flag we capture).
    asked_callback = any("call you back" in u.lower() for u in used)
    if not asked_callback and len([k for k in ("bank", "routing", "account") if k in now]) >= 2 and intent not in ("threat", "asks_code"):
        base = readback or "Alright."
        return _trim(f"{base} Before I do anything, can I call you back on the number on my statement?")

    if readback:
        follow = question or _pick(_stalls(now), used, rng)
        return _trim(f"{readback} {follow}")

    if intent in ("money_move", "urgency", "compromised") and question and now.get("money_requested"):
        combos = [f"{lead} {q}" for lead in LEADS[intent] for q in _all_questions(now)]
        return _trim(_pick(combos, used, rng))

    if intent in RESPONSES and intent not in ("other",):
        line = _pick(RESPONSES[intent], used, rng)
        if intent == "greeting" and dorothy_turns > 2:
            line = question or _pick(_stalls(now), used, rng)
        return _trim(line)

    if question:
        return _trim(question)
    pool = (_stalls(now) + RESPONSES["other"]) if now.get("money_requested") else RESPONSES["other"]
    return _trim(_pick(pool, used, rng))


def _trim(text):
    words = text.split()
    return " ".join(words[:MAX_WORDS]) if len(words) > MAX_WORDS else text


OPENING_LINE = "Hello? This is Dorothy speaking. Who's calling, please?"
