# Specification: Serverless OTel Logging Pipeline (GCS + DuckDB)

**Status:** Draft
**Date:** 2026-02-03
**Author:** Gemini (Pydantic AI Assistant)
**Context:** Pydantic AI Agent Observability

## 1. Overview
This specification details the design and implementation of a cost-effective, serverless observability pipeline for Pydantic AI applications. The system bypasses traditional APM vendors (Datadog, New Relic) and hosted collectors by writing OpenTelemetry (OTel) trace data directly to Google Cloud Storage (GCS) in a Hive-partitioned format. Consumption and analysis are performed ad-hoc using DuckDB on the client side or BigQuery External Tables.

### 1.1 Goals
*   **Zero Infrastructure:** No collectors, sidecars, or databases to manage.
*   **Cost Efficiency:** Pay only for storage (GCS) and compute-on-demand (local CPU or BigQuery scan slots).
*   **Vendor Neutrality:** Standard OpenTelemetry data generation; standard JSON/Parquet storage.
*   **Query Performance:** <2s query time for daily aggregations using DuckDB.

## 2. Architecture

```mermaid
graph LR
    A[Pydantic AI Agent] -->|Generates Spans| B(OTel SDK)
    B -->|BatchProcessor| C[GCS Exporter (Custom)]
    C -->|Writes JSONL| D[Local Buffer]
    D -->|Uploads on Rotation| E[Google Cloud Storage]
    
    subgraph Storage Layout
    E --> F{gs://bucket/logs/}
    F --> G[dt=2026-02-03]
    G --> H[hr=14]
    H --> I[trace_uuid.jsonl]
    end

    subgraph Analysis
    J[DuckDB CLI/Notebook] -->|Read via HTTPFS| E
    K[BigQuery External] -->|Read Native| E
    end
```

## 3. Data Design

### 3.1 File Format
*   **Format:** NDJSON (Newline Delimited JSON).
*   **Compression:** GZIP (Optional, but recommended for production).
*   **Filename Strategy:** `UUIDv4.jsonl` (to prevent collision during high concurrency).

### 3.2 Partitioning Strategy (Hive Style)
To enable efficient query pruning (skipping data we don't need), data MUST be stored using the Hive partitioning convention.

**Concept: What is Hive Partitioning?**
"Hive Partitioning" is a standardized directory structure used by big data tools (Apache Hive, Spark, Presto, BigQuery, DuckDB) to organize data files. Instead of storing metadata (like the date or hour) *inside* the file, we store it in the *directory path* as `key=value` pairs.

**Why use it? (Partition Pruning)**
When you run a query like `WHERE dt = '2026-02-03'`, the query engine (DuckDB) looks at the folder names first. It sees that the folder `dt=2025-01-01` does *not* match your filter, so it **completely skips** reading any files inside that folder. This technique is called **Partition Pruning**. It dramatically reduces I/O, making queries 10x-100x faster and cheaper (since you scan fewer bytes).

**Pattern:** `gs://{bucket_name}/{prefix}/dt={YYYY-MM-DD}/hr={HH}/{uuid}.jsonl`

**Example:**
*   `gs://my-app-logs/prod/dt=2026-02-03/hr=09/550e8400-e29b.jsonl`
*   `gs://my-app-logs/prod/dt=2026-02-03/hr=10/7d3a2100-a11c.jsonl`

### 3.3 JSON Schema
Each line in the JSONL file represents one OTel Span.

| Field | Type | Description |
| :--- | :--- | :--- |
| `trace_id` | String (Hex) | The global ID for the entire transaction. |
| `span_id` | String (Hex) | The ID for this specific operation. |
| `parent_id` | String (Hex) | The span ID of the caller (null if root). |
| `name` | String | Operation name (e.g., `agent.run`, `tool.call`). |
| `start_time` | String (ISO) | `2026-02-03T14:00:00.123Z` |
| `duration_ms` | Float | Execution time in milliseconds. |
| `status` | String | `OK` or `ERROR`. |
| `attributes` | Object | Key-value pairs (Agent metadata, User IDs). |
| `events` | Array | Logs/Events captured within the span. |

## 4. Implementation Details

### 4.1 Python GCS Exporter
The application requires a custom implementation to bridge the OTel SDK and GCS.

**Requirements:**
1.  **Buffering:** Do not upload every span individually (too slow/expensive). Buffer in memory or a temp file.
2.  **Rotation:** Flush buffer every N seconds (e.g., 60s) or M bytes (e.g., 5MB).
3.  **Partitioning:** dynamically calculate `dt` and `hr` at flush time based on UTC now.

**Code Reference (`monitoring/gcs_exporter.py`):**

```python
import json
import time
import uuid
import datetime
import os
from google.cloud import storage
from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult

class GCSSpanExporter(SpanExporter):
    def __init__(self, bucket_name: str, prefix: str = "logs"):
        self.bucket_name = bucket_name
        self.prefix = prefix
        self.storage_client = storage.Client()
        self.bucket = self.storage_client.bucket(bucket_name)

    def export(self, spans) -> SpanExportResult:
        # Group logic could go here, but for simplicity, we assume
        # the BatchSpanProcessor handles the grouping size.
        
        # 1. Convert Spans to NDJSON lines
        buffer = []
        for span in spans:
            item = {
                "trace_id": format(span.context.trace_id, "032x"),
                "span_id": format(span.context.span_id, "016x"),
                "name": span.name,
                "start_time": span.start_time, # Needs formatting
                "end_time": span.end_time,     # Needs formatting
                "duration_ms": (span.end_time - span.start_time) / 1e6,
                "attributes": dict(span.attributes),
                "status": span.status.status_code.name
            }
            buffer.append(json.dumps(item))
            
        payload = "\n".join(buffer)
        
        # 2. Determine Partition
        now = datetime.datetime.utcnow()
        partition = now.strftime("dt=%Y-%m-%d/hr=%H")
        
        # 3. Generate Key
        file_name = f"{uuid.uuid4()}.jsonl"
        blob_path = f"{self.prefix}/{partition}/{file_name}"
        
        # 4. Upload
        try:
            blob = self.bucket.blob(blob_path)
            blob.upload_from_string(payload, content_type="application/x-ndjson")
            return SpanExportResult.SUCCESS
        except Exception as e:
            # Fallback logging here
            return SpanExportResult.FAILURE

    def shutdown(self):
        pass
```

### 4.2 Application Integration
Connect the exporter using the `BatchSpanProcessor`.

```python
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from monitoring.gcs_exporter import GCSSpanExporter
from pydantic_ai import Agent

# Setup
exporter = GCSSpanExporter(bucket_name="my-ai-logs")
# BatchSpanProcessor is crucial: it handles buffering and background uploads
processor = BatchSpanProcessor(exporter, schedule_delay_millis=60000) 

provider = TracerProvider()
provider.add_span_processor(processor)

# Instrument
Agent.instrument_all(tracer_provider=provider)
```

## 5. Consumption (DuckDB)

### 5.1 GCS Authenticated Access
To read from GCS, DuckDB uses the S3 Compatibility API (HMAC Keys).

1.  **GCP Console:** Settings -> Interoperability -> Create Key.
2.  **DuckDB Init:**

```sql
INSTALL httpfs;
LOAD httpfs;

SET s3_region='us-east-1'; -- Value ignored by GCS but required by syntax
SET s3_endpoint='storage.googleapis.com';
SET s3_access_key_id='GOOG...';
SET s3_secret_access_key='...';
```

### 5.2 Dynamic Queries

**Scenario 1: Find all errors today**
DuckDB automatically parses `dt` from the folder structure.

```sql
SELECT 
    name, 
    attributes.user_id, 
    duration_ms 
FROM read_json_auto('s3://my-ai-logs/logs/**/*.jsonl', hive_partitioning=1)
WHERE dt = strftime(today(), '%Y-%m-%d')
  AND status = 'ERROR';
```

**Scenario 2: Token Usage Aggregation**
Assuming token counts are in `attributes`.

```sql
SELECT 
    dt,
    hr,
    sum(cast(json_extract(attributes, '$.gen_ai.usage.input_tokens') as int)) as total_input,
    sum(cast(json_extract(attributes, '$.gen_ai.usage.output_tokens') as int)) as total_output
FROM read_json_auto('s3://my-ai-logs/logs/**/*.jsonl', hive_partitioning=1)
WHERE dt >= '2026-02-01'
GROUP BY 1, 2
ORDER BY 1 DESC, 2 DESC;
```

## 6. Future Considerations

*   **Compression:** Implementing GZIP uploads in the Python exporter reduces GCS storage costs by ~80%. DuckDB reads `.jsonl.gz` transparently.
*   **BigQuery Migration:** If DuckDB local processing becomes too slow (TB+ scale), define a BigQuery External Table pointing to the same bucket. No code changes required.

```