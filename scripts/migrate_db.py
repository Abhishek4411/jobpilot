"""Idempotent database migration: creates all tables if they do not exist."""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.db import init_db, DB_PATH
from core.logger import get_logger

log = get_logger(__name__)


def main() -> None:
    """Run database migrations."""
    import os
    os.makedirs("data", exist_ok=True)
    init_db()
    print(f"Database ready at {DB_PATH}")


if __name__ == "__main__":
    main()
