# Helios

Event-store service. Ingests raw events and serves recent-event / count
queries. Storage is **SQLite** today (`helios.db`), accessed through
`db.connect()`; `ingest.py` is the write path and `schema.sql` is the table
definition.

The architecture review flagged SQLite as the scaling bottleneck at current
volume (~50GB/day) and proposed migrating the analytical query paths to
**DuckDB**. Any refactor starts here: `db.py` (connection + read queries),
`ingest.py` (write path + `VACUUM`), `schema.sql` (DDL).
