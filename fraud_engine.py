"""Small deterministic rules engine; every decision is audit logged."""
from datetime import datetime, timedelta, timezone
import db

def evaluate(customer, linked_account, amount):
    reasons=[]; decision='allow'
    approved = db.rows("SELECT * FROM watchlist WHERE item_type='bank_account' AND status='approved' AND expires_at>?", (db.now_iso(),))
    destination = f"{linked_account['routing_number']}|{linked_account['account_number']}"
    if any(w['value'] == destination for w in approved):
        decision='hold'; reasons.append('Account linked to active scam')
    else:
        try: added = datetime.fromisoformat(linked_account['added_at'])
        except Exception: added = datetime.now(timezone.utc)-timedelta(days=365)
        if added.tzinfo is None: added=added.replace(tzinfo=timezone.utc)
        if datetime.now(timezone.utc)-added <= timedelta(hours=24) and amount > 10000:
            decision='warn'; reasons.append('Newly added account and transfer exceeds $10,000')
        elif amount > float(customer['balance'])*0.5:
            decision='warn'; reasons.append('Transfer exceeds 50% of available balance')
        else:
            decision='allow'; reasons.append('No demo fraud rules triggered')
    db.log_event('fraud_decision', f"customer={customer['name']}; account_id={linked_account['id']}; amount={amount:.2f}; decision={decision}; reasons={' | '.join(reasons)}")
    return {'decision':decision,'reasons':reasons}
