"""The database layer: the schema, its migrations and the guard against touching production.

PostgreSQL 18 with TimescaleDB is the system of record (KAPS decision 2). Money is ``bigint`` in
millionths of a dollar (``_e6``) and hundredths of a contract (``_e2``), as everywhere in SPARQ.
"""
