"""Registration limits apply to canonical IDs, not whitespace padding."""
import pytest
from app.accounts import PasswordLogin, Register, UserAccount
from app.db import transaction
from test_accounts import accounts, PASSWORD, register


@pytest.mark.parametrize("username", [" a ", " ab ", "\tA\t"])
def test_short_padded_signup_is_rejected_without_creating_identity(accounts, username):
    client, app, _, _ = accounts
    response = register(client, username, "TEST-PADDED")
    assert response.status_code == 422
    assert PASSWORD not in response.text and "TEST-PADDED" not in response.text
    with transaction(app.state.engine) as db:
        assert db.get(UserAccount, username.strip().lower()) is None


@pytest.mark.parametrize("username", [" AbC ", "a" * 40])
def test_valid_registration_preserves_canonical_identity(username):
    value = Register(username=username, password=PASSWORD, name="합성 사용자", employee_number="test-001")
    assert value.username == username.strip().lower()
    assert value.employee_number == "TEST-001"


def test_short_legacy_login_ids_remain_compatible():
    assert PasswordLogin(username=" A ", password=PASSWORD).username == "a"
