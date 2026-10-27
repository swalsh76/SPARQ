"""The answer file (``install.toml``) and the role model (KAPS PLAN.md §6.1).

``sparq-admin install --answers install.toml`` runs from this file without a question, so an
install, a reinstall or a recovery can be repeated exactly. The file holds no secrets: keys and the
share credential are named elsewhere and set with ``sparq-admin secrets set``.

The role model is fixed here, ahead of the wizard that will write the file (K8):

* ``database_host``: the server host (PostgreSQL + TimescaleDB, ingest, API, web).
* ``analytics_node``: the analytics node (installer built in K2).
* ``standby_database``: parked (PLAN.md §9). It is listed, and refused with a message that names
  the parking-lot entry, so the file format carries it from the first version.
"""

import tomllib
from enum import StrEnum
from ipaddress import IPv4Address, IPv6Address
from pathlib import Path
from typing import Annotated, Literal, cast

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

ABSOLUTE_PATH = Annotated[str, Field(pattern=r"^/[^\s]*$", max_length=200)]


class AnswersError(Exception):
    """The answer file is missing, unreadable or invalid."""


class RoleUnavailable(Exception):
    """The role exists in the model but cannot be installed yet."""


class Role(StrEnum):
    DATABASE_HOST = "database_host"
    ANALYTICS_NODE = "analytics_node"
    STANDBY_DATABASE = "standby_database"


UNAVAILABLE_ROLES = {
    Role.ANALYTICS_NODE: "the analytics node installer is built in K2 (KAPS PLAN.md §6.10)",
    Role.STANDBY_DATABASE: "the standby database is parked (PLAN.md §9) and is not yet available",
}


class _Section(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Backup(_Section):
    """Where backups go: a mounted share carrying the marker file (``backup.target``)."""

    target: ABSOLUTE_PATH
    retention_full_weeks: int = Field(default=5, ge=1, le=52)  # full backups kept (one a week)
    encrypt: bool = True  # the repository on the share is encrypted (PLAN.md decision B2)


class Storage(_Section):
    budget_gb: int = Field(default=700, ge=50, le=800)


class Postgres(_Section):
    port: int = Field(default=5432, ge=1024, le=65535)
    # PostgreSQL accepts connections from localhost and from these addresses only
    allowed_clients: list[IPv4Address | IPv6Address] = Field(default_factory=list, max_length=8)


class Tls(_Section):
    mode: Literal["new", "restore"] = "new"
    restore_from: ABSOLUTE_PATH | None = None

    @model_validator(mode="after")
    def _restore_needs_a_source(self) -> "Tls":
        if (self.mode == "restore") != (self.restore_from is not None):
            raise ValueError("restore_from is required for mode 'restore' and forbidden otherwise")
        return self


class Web(_Section):
    port: int = Field(default=443, ge=1, le=65535)


class Recovery(_Section):
    from_backup: bool = False
    backup_set: str | None = Field(default=None, pattern=r"^[A-Za-z0-9._-]{1,80}$")

    @model_validator(mode="after")
    def _recovery_needs_a_backup_set(self) -> "Recovery":
        if self.from_backup != (self.backup_set is not None):
            raise ValueError("backup_set is required for a recovery and forbidden otherwise")
        return self


class ScratchCluster(_Section):
    """A second cluster, on its own port, for running the database tests from the workstation.

    It is never production: its database is ``sparq_test`` and the tests refuse any other name.
    """

    enabled: bool = False
    port: int = Field(default=5433, ge=1024, le=65535)
    allowed_clients: list[IPv4Address | IPv6Address] = Field(default_factory=list, max_length=8)


class Ingest(_Section):
    """The ingest service (K1a.8). ``checkout`` and ``uv`` are used only to build its environment
    until release bundles exist (K1b)."""

    enabled: bool = True
    checkout: ABSOLUTE_PATH | None = None  # a checkout of the repository at the release to run
    uv: ABSOLUTE_PATH | None = None  # the uv program
    disk_stop_percent: int = Field(default=90, ge=50, le=99)

    @model_validator(mode="after")
    def _building_needs_both_paths(self) -> "Ingest":
        if self.enabled and (self.checkout is None or self.uv is None):
            raise ValueError("an enabled ingest needs checkout and uv (or set enabled = false)")
        return self


class Alerts(_Section):
    """The ingest watchdog's push alerts (K1b.3a). The topic itself is a secret and is stored as a
    ``systemd`` credential, never in this file."""

    enabled: bool = False
    server: str = Field(
        default="https://ntfy.sh", pattern=r"^https?://[A-Za-z0-9.-]+(:[0-9]{1,5})?$"
    )
    silence_seconds: int = Field(default=600, ge=60, le=86_400)
    disk_warn_percent: int = Field(default=80, ge=50, le=99)


class DatabaseHost(_Section):
    backup: Backup
    storage: Storage = Storage()
    postgres: Postgres = Postgres()
    tls: Tls = Tls()
    web: Web = Web()
    recovery: Recovery = Recovery()
    test_cluster: ScratchCluster = ScratchCluster()
    ingest: Ingest = Ingest(enabled=False)
    alerts: Alerts = Alerts()

    @model_validator(mode="after")
    def _the_clusters_have_different_ports(self) -> "DatabaseHost":
        if self.test_cluster.enabled and self.test_cluster.port == self.postgres.port:
            raise ValueError("the test cluster needs a port of its own")
        if self.alerts.enabled and not self.ingest.enabled:
            raise ValueError("the alerts watch the ingest, which is not enabled")
        return self


class AnalyticsNode(_Section):
    """Placeholder section for the analytics node, validated now so the format does not change."""

    database_address: IPv4Address | IPv6Address
    pinned_certificate_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    external_ssd: ABSOLUTE_PATH
    training_default: Literal["analytics_node", "workstation"] = "analytics_node"
    share_jobs: ABSOLUTE_PATH
    share_models: ABSOLUTE_PATH


class Answers(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    role: Role
    database_host: DatabaseHost | None = None
    analytics_node: AnalyticsNode | None = None

    @model_validator(mode="after")
    def _the_roles_section_is_present_and_alone(self) -> "Answers":
        sections = {
            Role.DATABASE_HOST: self.database_host,
            Role.ANALYTICS_NODE: self.analytics_node,
        }
        for role, section in sections.items():
            if self.role == role and section is None:
                raise ValueError(f"a [{role.value}] section is required for role {role.value}")
            if self.role != role and section is not None:
                raise ValueError(f"a [{role.value}] section does not belong to role {self.role}")
        return self

    def installable_host(self) -> DatabaseHost:
        """The database host's answers; raises RoleUnavailable (with the reason) for any role that
        cannot be installed now."""
        reason = UNAVAILABLE_ROLES.get(self.role)
        if reason is not None:
            raise RoleUnavailable(f"role {self.role.value}: {reason}")
        return cast(DatabaseHost, self.database_host)  # the validator guarantees it for this role


def parse_answers(text: str) -> Answers:
    """Parse an answer file's text (TOML)."""
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as error:
        raise AnswersError(f"not valid TOML: {error}") from error
    try:
        return Answers.model_validate(data)
    except ValidationError as error:
        details = "; ".join(
            f"{'.'.join(str(p) for p in e['loc']) or 'file'}: {e['msg']}" for e in error.errors()
        )
        raise AnswersError(details) from error


def load_answers(path: Path) -> Answers:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as error:
        raise AnswersError(f"cannot read {path.name}: {error.strerror or error}") from error
    return parse_answers(text)
