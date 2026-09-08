"""
Row-level validation for a single CSV transaction row.

Design choice: this returns (transaction_dict | None, error | None) rather
than raising, because an invalid row must not abort the import -- it's
recorded and the import continues (assignment section 6).
"""

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

ALLOWED_TYPES = {"CREDIT", "DEBIT"}

# ISO-4217 is ~180 codes; for a take-home a fixed common set plus a shape
# check ("3 uppercase letters") is a reasonable, documented assumption
# rather than shipping the full ISO list. See README "Assumptions".
_COMMON_CURRENCIES = {
    "USD", "EUR", "GBP", "INR", "JPY", "AUD", "CAD", "CHF", "CNY", "SGD",
    "AED", "ZAR", "NZD", "SEK", "NOK", "HKD",
}

REQUIRED_COLUMNS = {"transaction_id", "account_id", "type", "amount", "currency", "timestamp"}


def validate_row(row: dict, row_number: int) -> tuple[dict | None, str | None]:
    transaction_id = (row.get("transaction_id") or "").strip()
    if not transaction_id:
        return None, "transaction_id is required and must be non-empty"

    account_id = (row.get("account_id") or "").strip()
    if not account_id:
        return None, "account_id is required and must be non-empty"

    txn_type = (row.get("type") or "").strip().upper()
    if txn_type not in ALLOWED_TYPES:
        return None, f"type must be one of {sorted(ALLOWED_TYPES)}, got '{row.get('type')}'"

    amount_raw = (row.get("amount") or "").strip()
    try:
        amount = Decimal(amount_raw)
    except (InvalidOperation, ValueError):
        return None, f"amount must be a valid decimal, got '{amount_raw}'"
    if amount <= 0:
        return None, "amount must be greater than zero"
    # Financial precision: reject more than 2 decimal places rather than
    # silently rounding, since silent rounding would misstate the ledger.
    if amount.as_tuple().exponent < -2:
        return None, "amount must not have more than 2 decimal places"

    currency = (row.get("currency") or "").strip().upper()
    if len(currency) != 3 or not currency.isalpha():
        return None, f"currency must be a 3-letter code, got '{row.get('currency')}'"
    if currency not in _COMMON_CURRENCIES:
        return None, f"currency '{currency}' is not in the supported currency list"

    ts_raw = (row.get("timestamp") or "").strip()
    timestamp = _parse_timestamp(ts_raw)
    if timestamp is None:
        return None, f"timestamp must be a valid ISO-8601 datetime, got '{ts_raw}'"

    return (
        {
            "transaction_id": transaction_id,
            "account_id": account_id,
            "type": txn_type,
            "amount": amount,
            "currency": currency,
            "timestamp": timestamp,
        },
        None,
    )


def _parse_timestamp(value: str) -> datetime | None:
    if not value:
        return None
    try:
        # Accept both "...Z" and explicit offsets.
        normalized = value.replace("Z", "+00:00")
        dt = datetime.fromisoformat(normalized)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return None
