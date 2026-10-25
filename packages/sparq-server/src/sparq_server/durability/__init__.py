"""The power-cut test (K1a.3, the S-1d protocol from docs/LESSONS.md, now for PostgreSQL).

A writer on the server host commits rows to the test cluster and reports each acknowledged commit
to a ledger on another machine. After the power is cut and restored, ``verify`` checks that the
database is sound and that every acknowledged row is present with a valid checksum.

    python -m sparq_server.durability ledger --directory DIR --port N
    python -m sparq_server.durability writer --url URL --run N --ledger-host H --ledger-port N
    python -m sparq_server.durability verify --url URL --ledger-directory DIR
"""
