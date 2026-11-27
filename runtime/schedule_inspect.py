"""Inspect local scheduler state without starting CODA or running tasks."""

import argparse
import json
import sqlite3

from runtime.schedule_store import ScheduleStore


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("database", help="Path to the existing scheduler SQLite database")
    parser.add_argument("view", choices=("tasks", "history", "pending"))
    args = parser.parse_args(argv)
    try:
        rows = ScheduleStore.inspect(args.database, args.view)
    except (OSError, ValueError, sqlite3.Error):
        parser.exit(1, "Could not read the scheduler database. Check its path and schema.\n")
    print(json.dumps(rows, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
