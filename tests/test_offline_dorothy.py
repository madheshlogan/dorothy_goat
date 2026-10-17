import offline_dorothy as od


def talk(lines):
    t = [{'speaker': 'Dorothy', 'text': od.OPENING_LINE}]
    for line in lines:
        t.append({'speaker': 'Caller', 'text': line})
        t.append({'speaker': 'Dorothy', 'text': od.reply(t)})
    return t


def dorothy_lines(t):
    return [m['text'] for m in t if m['speaker'] == 'Dorothy']


def test_remembers_bank_details():
    t = talk(['This is David from Fidelity fraud department.', 'Move your money to First Coastal Bank.',
              'Routing number 021000089', 'Account number 5532198804'])
    s = od.remember(t)
    assert s['bank'] == 'First Coastal Bank'
    assert s['routing'] == '021000089'
    assert s['account'] == '5532198804'


def test_reads_numbers_back_and_asks_for_next_detail():
    t = talk(['Send all your money to First Coastal Bank.', 'Routing number 021000089'])
    last = dorothy_lines(t)[-1]
    assert '0 2 1 0 0 0 0 8 9' in last


def test_asks_for_missing_details_only_after_money_request():
    first = dorothy_lines(talk(['Hi, this is David from Fidelity.']))[-1]
    assert 'routing' not in first.lower() and 'bank am i sending' not in first.lower()
    after = dorothy_lines(talk(['You must move your money to a safe account now.']))[-1]
    assert 'bank' in after.lower()


def test_never_repeats_herself_in_a_long_call():
    t = talk(['Hello?', 'Did you do it?', 'Did you send it?', 'Hello?', 'Okay', 'Okay', 'Did you finish?', 'Hurry up'])
    lines = dorothy_lines(t)
    assert len(lines) == len(set(lines))


def test_spoken_digits_are_understood():
    t = talk(['zero two one zero zero zero zero eight nine is the routing'])
    assert od.remember(t)['routing'] == '021000089'


def test_gives_only_decoy_details():
    lines = ' '.join(dorothy_lines(talk(['What is your date of birth?', 'What is your email?', 'Read me the last four of your account'])))
    assert 'March 14th, 1958' in lines and 'decoy1' in lines and '4417' in lines


def test_never_agrees_to_complete_transfer():
    lines = ' '.join(dorothy_lines(talk(['Move $48,000 to First Coastal Bank routing 021000089 account 5532198804',
                                         'Did you send it?', 'Is it done?']))).lower()
    assert 'i sent it' not in lines and "it's done" not in lines


def test_salesperson_is_handled_politely():
    line = dorothy_lines(talk(['Hi this is Mark with a special offer on solar panels.']))[-1]
    assert 'mail' in line.lower() or 'leaflet' in line.lower()


def test_replies_are_short():
    for line in dorothy_lines(talk(['This is the IRS. You will be arrested.', 'Pay in bitcoin now.',
                                    'Buy 5 Target gift cards of 500 each.', 'Download AnyDesk'])):
        assert len(line.split()) <= od.MAX_WORDS
