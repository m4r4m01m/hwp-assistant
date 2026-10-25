"""Local operator-only account maintenance. No HTTP admin endpoint or password args.

Examples (inside API container):
  python -m app.account_admin create --user yoseob --name NAME --employee TEST-001
  python -m app.account_admin password --user guest
  python -m app.account_admin disable --user sampleuser

Only additive creation; resets never alter identity, employee number or documents.
"""
import argparse
import getpass
import os
import secrets
import time
from sqlalchemy import select, text
from .accounts import PasswordLogin, Register, UserAccount, hash_password
from .auth import credentials
from .db import make_engine, transaction
from .schema import migrate


def maintain(engine, action, user, *, password=None, name=None, employee=None):
    # Names follow browser normalization; do not silently rename legacy IDs.
    canonical = PasswordLogin(username=user, password="validation-only").username
    if user != canonical or user == "local":
        raise ValueError("Use a canonical lowercase account ID; local is reserved")
    encoded = hash_password(password) if action in ("create", "password") else None
    if action == "create" and user != "guest":
        value = Register(username=user, password=password, name=name, employee_number=employee)
        name, employee = value.name, value.employee_number
    with transaction(engine) as db:
        if engine.dialect.name == "postgresql":
            db.execute(text("SELECT pg_advisory_xact_lock(706251025)"))
        row = db.scalar(select(UserAccount).where(UserAccount.user_id == user).with_for_update())
        if action == "create":
            if row:
                raise ValueError("Account already exists; create never overwrites it")
            db.add(UserAccount(user_id=user, password_hash=encoded,
                name="공용 체험" if user == "guest" else name,
                employee_number=None if user == "guest" else employee,
                kind="guest" if user == "guest" else "member", is_active=True,
                session_revision=secrets.token_hex(32), created_at=time.time()))
        elif not row:
            raise ValueError("Account does not exist")
        elif action == "password":
            row.password_hash = encoded
            row.session_revision = secrets.token_hex(32)
        elif action in ("enable", "disable"):
            row.is_active = action == "enable"
            row.session_revision = secrets.token_hex(32)
        else:
            raise ValueError("Unsupported account action")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("create", "password", "enable", "disable"))
    parser.add_argument("--user", required=True)
    parser.add_argument("--name")
    parser.add_argument("--employee")
    args = parser.parse_args()
    password = None
    if args.action in ("create", "password"):
        if not os.isatty(0):
            raise SystemExit("An interactive terminal is required; passwords are not accepted as arguments")
        password = getpass.getpass("Password: ")
        if password != getpass.getpass("Confirm password: "):
            raise SystemExit("Passwords differ; nothing changed")
    engine = make_engine(os.environ["DATABASE_URL"])
    try:
        migrate(engine, credentials())
        maintain(engine, args.action, args.user, password=password, name=args.name, employee=args.employee)
        print("Account change committed. Document ownership unchanged. Password sessions are invalidated on reset.")
    except Exception:
        # SQL/validation exceptions may contain values. Do not log them.
        raise SystemExit("Account change failed. Check existing ID, unique employee number and input; no credentials were printed.") from None
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
