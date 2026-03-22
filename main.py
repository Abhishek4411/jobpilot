"""JobPilot entry point: initializes database, starts orchestrator and dashboard."""

import sys
import threading

from core.config_loader import load_config
from core.db import init_db, DB_PATH
from core.logger import get_logger
from core import orchestrator

log = get_logger(__name__)


def main() -> None:
    """Load config, initialize the database, start all agents, and serve the dashboard."""
    cfg = load_config()
    port = cfg.get("settings", {}).get("dashboard", {}).get("port", 5000)
    host = cfg.get("settings", {}).get("dashboard", {}).get("host", "127.0.0.1")

    init_db()
    log.info("Database ready at %s", DB_PATH)

    orchestrator.start(cfg)
    log.info("Orchestrator started")

    from dashboard.app import create_app
    app = create_app()

    print("\n" + "=" * 50)
    print("  JobPilot is running!")
    print(f"  Dashboard: http://{host}:{port}")
    print("  Press Ctrl+C to stop.")
    print("=" * 50 + "\n")

    try:
        app.run(host=host, port=port, debug=False, use_reloader=False)
    except KeyboardInterrupt:
        print("\nShutting down JobPilot... bye!")
        sys.exit(0)


if __name__ == "__main__":
    main()
