"""Credentials & Integrations from the command line: the table, the import from the environment, key rotation.

Poster does all of this by itself on first use (it creates poster_integration_settings and imports the
environment's settings once SETTINGS_ENCRYPTION_KEY is set), so this script is for operators who want to do it
ahead of time, rotate the key, or look at what is stored. It reads the same environment and .env file as app.py.

    python tools/migrate_settings.py generate-key   a new SETTINGS_ENCRYPTION_KEY
    python tools/migrate_settings.py migrate        create the table and import the environment's settings
    python tools/migrate_settings.py status         each integration's provider, source and status (no secrets)
    python tools/migrate_settings.py rotate         encrypt every saved setting again under the first key in
                                                    SETTINGS_ENCRYPTION_KEY="new,old" (then drop the old one)
    python tools/migrate_settings.py sql            the table's SQL, to create it by hand
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from poster.activity import ActivityLog  # noqa: E402
from poster.config import Config  # noqa: E402
from poster.crypto import generate_key  # noqa: E402
from poster.errors import ServiceError  # noqa: E402
from poster.services.settings_service import TYPES, SettingsService  # noqa: E402
from poster.store import POSTGRES_ONLY, SCHEMA, PostStore  # noqa: E402


def service(config: Config | None = None) -> SettingsService:
    config = config or Config.from_env()
    store = PostStore(config)
    store.require_configured()
    return SettingsService(config, store, ActivityLog(store))


def migrate(config: Config | None = None) -> int:
    settings = service(config)
    if settings.box.problem:
        print(f"Not imported: {settings.box.problem}")
        return 1
    imported = settings.import_from_env()
    print("Imported: " + ", ".join(imported) if imported else "Nothing to import: the database already has settings for "
                                                             "every integration the environment configures.")
    return 0


def status(config: Config | None = None) -> int:
    settings = service(config)
    for key, kind in TYPES.items():
        described = settings.describe_type(key)
        for provider in described["providers"]:
            marker = "*" if provider["active"] else " "
            source = provider["source"] or "environment"
            print(f"{marker} {kind.label:<22} {provider['label']:<22} {source:<12} {provider['status']:<10} "
                  f"{provider['problem'] or 'ready'}")
    print("\n* active provider. Secrets are never printed.")
    return 0


def rotate(config: Config | None = None) -> int:
    settings = service(config)
    count = settings.rotate_key()
    print(f"Encrypted {count} saved settings again under the first key. Remove the old key from SETTINGS_ENCRYPTION_KEY.")
    return 0


def sql(_config: Config | None = None) -> int:
    statement = next(s for s in SCHEMA if "poster_integration_settings" in s)
    print(f"{statement};")
    print(f"-- Postgres only:\n{next(s for s in POSTGRES_ONLY if 'poster_integration_settings' in s)};")
    return 0


def main(argv: list[str] | None = None, config: Config | None = None) -> int:
    """Runs a command. ``config`` replaces the environment (for tests)."""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=["generate-key", "migrate", "status", "rotate", "sql"])
    command = parser.parse_args(argv).command
    if command == "generate-key":
        print(generate_key())
        return 0
    try:
        return {"migrate": migrate, "status": status, "rotate": rotate, "sql": sql}[command](config)
    except ServiceError as error:
        print(f"{error.message} {error.hint}".strip(), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
