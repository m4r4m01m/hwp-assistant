"""Apply additive schemas 001/002 and explicitly assigned legacy ownership."""

from .main import app
from .schema import migrate

if __name__ == "__main__":
    migrate(app.state.engine, app.state.users)
    print("Schema 002: additive access, conversations, context, changes, proposals, templates ready")
