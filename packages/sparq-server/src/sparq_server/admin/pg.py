"""PostgreSQL and pgBackRest as the admin commands see them: run SQL as the superuser, list the
clusters, ask pgBackRest, and make sure a role, a database or an extension exists.

``install`` changes the machine through these; ``verify``, ``restore-drill`` and ``ingest-status``
only ask through them. The role, database and extension names are fixed by ``managed`` (never
taken from a user), which is why they can be written into the SQL.
"""

import json
from typing import Any, cast

from sparq_server.admin.system import Host, System
from sparq_server.admin.versions import POSTGRES_MAJOR


def clusters(host: System) -> dict[str, tuple[int, str]]:
    """The PostgreSQL clusters of this major version: name -> (port, status)."""
    _, out = host.run(["pg_lsclusters", "-h"])
    found: dict[str, tuple[int, str]] = {}
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 4 and parts[0] == str(POSTGRES_MAJOR) and parts[2].isdigit():
            found[parts[1]] = (int(parts[2]), parts[3])
    return found


def psql(host: Host, port: int, database: str, sql: str) -> str:
    """Run SQL as the superuser over the local socket (root maps to ``postgres``)."""
    argv = ["psql", "-p", str(port), "-U", "postgres", "-d", database]
    return host.run_checked([*argv, "-v", "ON_ERROR_STOP=1", "-At", "-q", "-f", "-"], sql)


def returns_rows(host: Host, port: int, database: str, sql: str) -> bool:
    return bool(psql(host, port, database, sql).strip())


def ensure_role(host: Host, port: int, role: str, options: str = "LOGIN") -> bool:
    """Create the role with ``options`` if it is missing; returns whether it was created."""
    if returns_rows(host, port, "postgres", f"SELECT 1 FROM pg_roles WHERE rolname = '{role}'"):  # noqa: S608 (fixed names)
        return False
    psql(host, port, "postgres", f"CREATE ROLE {role} {options}")
    return True


def ensure_database(
    host: Host, port: int, database: str, owner: str, *, private: bool = False
) -> bool:
    """Create the database owned by ``owner`` if it is missing; ``private`` takes away the right
    every role has by default to connect to it. Returns whether it was created."""
    found = f"SELECT 1 FROM pg_database WHERE datname = '{database}'"  # noqa: S608 (fixed names)
    if returns_rows(host, port, "postgres", found):
        return False
    psql(host, port, "postgres", f"CREATE DATABASE {database} OWNER {owner}")
    if private:
        psql(host, port, "postgres", f"REVOKE CONNECT ON DATABASE {database} FROM PUBLIC")
    return True


def ensure_timescaledb(host: Host, port: int, database: str) -> bool:
    found = "SELECT extversion FROM pg_extension WHERE extname = 'timescaledb'"
    if returns_rows(host, port, database, found):
        return False
    psql(host, port, database, "CREATE EXTENSION timescaledb")
    return True


def as_postgres(argv: list[str]) -> list[str]:
    """Run a command as the ``postgres`` account (pgBackRest and the share are its)."""
    return ["runuser", "-u", "postgres", "--", *argv]


def stanza_info(host: System, name: str) -> dict[str, Any] | None:
    """What pgBackRest says about one stanza, or None if it cannot be asked."""
    code, out = host.run(as_postgres(["pgbackrest", f"--stanza={name}", "info", "--output=json"]))
    try:
        found = json.loads(out) if code == 0 else []
    except json.JSONDecodeError:
        return None
    return cast(dict[str, Any], found[0]) if found else None
