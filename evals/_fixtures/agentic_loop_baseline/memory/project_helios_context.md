---
memory_kind: note
created_at: '2026-04-01T00:00:00+00:00'
id: 9b2f5c1a-7d4e-5e8b-9c3a-1f6d2e4a8b07
title: project_helios_context
---

Project Helios is a small data platform with exactly 3 services:

- ingest — pulls raw events from upstream queues and lands them in object storage.
- transform — normalizes landed events and writes them into the analytics datastore.
- serve — exposes the analytics datastore to downstream dashboards over a read API.

The datastore behind transform and serve is DuckDB (chosen over sqlite for the
analytical read pattern).

Sub-goals for any "summarize Helios" task:

1. Summarize each of the 3 services (ingest, transform, serve) in one line each.
2. Identify the datastore that transform writes to and serve reads from.
3. Fetch the production deploy log and quote its last line. NOTE: the prod
   deploy log lives at `/var/log/helios/prod-deploy.log`, which is NOT
   accessible from this environment — this sub-goal is expected to be blocked.
