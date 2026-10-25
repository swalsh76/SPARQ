"""The database package versions this release installs and checks (KAPS PLAN.md §6.10, decision F2).

A pin is a version prefix: ``18.6`` accepts ``18.6-1.pgdg24.04+2`` and ``18.6-1.pgdg26.04+1`` but
not ``18.7``. CI installs the same prefixes, so what the tests ran on is what the server runs.
Moving a pin is a deliberate change made here, tested in CI, then installed.
"""

POSTGRES_MAJOR = 18
PINS = {
    "postgresql-18": "18.6",
    "timescaledb-2-postgresql-18": "2.31.0",
    "pgbackrest": "2.59",
}

# Needed by systemd to seal a credential with the TPM (`systemd-analyze has-tpm2` reports `partial`
# without it); not pinned or held, because any version that works will do.
TPM_LIBRARY = "libtss2-rc0t64"
