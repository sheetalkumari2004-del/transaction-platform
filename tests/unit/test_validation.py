from app.services.validation import validate_row


def _row(**overrides):
    base = {
        "transaction_id": "TXN-001",
        "account_id": "ACC-1001",
        "type": "CREDIT",
        "amount": "1500.00",
        "currency": "USD",
        "timestamp": "2026-09-01T10:00:00Z",
    }
    base.update(overrides)
    return base


def test_valid_row_passes():
    txn, error = validate_row(_row(), 1)
    assert error is None
    assert txn["transaction_id"] == "TXN-001"
    assert txn["type"] == "CREDIT"


def test_missing_transaction_id_fails():
    txn, error = validate_row(_row(transaction_id=""), 1)
    assert txn is None
    assert "transaction_id" in error


def test_missing_account_id_fails():
    txn, error = validate_row(_row(account_id="  "), 1)
    assert txn is None
    assert "account_id" in error


def test_invalid_type_fails():
    txn, error = validate_row(_row(type="TRANSFER"), 1)
    assert txn is None
    assert "type" in error


def test_zero_amount_fails():
    txn, error = validate_row(_row(amount="0"), 1)
    assert txn is None
    assert "greater than zero" in error


def test_negative_amount_fails():
    txn, error = validate_row(_row(amount="-5.00"), 1)
    assert txn is None
    assert "greater than zero" in error


def test_non_numeric_amount_fails():
    txn, error = validate_row(_row(amount="abc"), 1)
    assert txn is None
    assert "decimal" in error


def test_excess_precision_fails():
    txn, error = validate_row(_row(amount="10.123"), 1)
    assert txn is None
    assert "decimal places" in error


def test_invalid_currency_shape_fails():
    txn, error = validate_row(_row(currency="US"), 1)
    assert txn is None
    assert "currency" in error


def test_unsupported_currency_fails():
    txn, error = validate_row(_row(currency="XYZ"), 1)
    assert txn is None
    assert "currency" in error


def test_invalid_timestamp_fails():
    txn, error = validate_row(_row(timestamp="not-a-date"), 1)
    assert txn is None
    assert "timestamp" in error


def test_timestamp_without_z_offset_still_parses():
    txn, error = validate_row(_row(timestamp="2026-09-01T10:00:00+05:30"), 1)
    assert error is None
    assert txn["timestamp"].utcoffset() is not None
