# Relay Load Tests & Performance Benchmarking

This directory contains the reproducible benchmark suite for the Relay notification platform.

The benchmark runner is located at [`load-tests/run-benchmark.js`](file:///c:/Users/USER/Desktop/System%20Design%20Task/load-tests/run-benchmark.js). It evaluates:
1. **HTTP Acceptance Ingestion:** Measures Fastify acceptance throughput (`POST /v1/notifications`), status distribution, and latency percentiles over loopback TCP sockets using keep-alive connections.
2. **Outbox Worker Throughput:** Measures asynchronous batch claim and fake provider delivery rates.
3. **End-to-End Pipeline:** Measures concurrent ingestion and background outbox worker drain.

## Prerequisites

1. Compile the TypeScript application:
   ```bash
   npm run build
   ```

## Running Benchmarks

### 1. Smoke Verification (Fast sanity check)
Executes a quick 100-request run to verify server initialization and zero-error acceptance:
```bash
node load-tests/run-benchmark.js --mode=smoke
```

### 2. Full Benchmark Suite
Executes smoke check, sustained HTTP acceptance test (2,000 requests, concurrency 25), worker drain test, and concurrent pipeline test:
```bash
node load-tests/run-benchmark.js --mode=full --requests=2000 --concurrency=25
```

### 3. Component-Specific Benchmarks
You can also run individual benchmarks:
```bash
# HTTP API acceptance only
node load-tests/run-benchmark.js --mode=api --requests=2000 --concurrency=25

# Worker drain throughput only
node load-tests/run-benchmark.js --mode=worker --requests=2000

# Pipeline saturation only
node load-tests/run-benchmark.js --mode=pipeline --requests=1000 --concurrency=20
```

## Available Options

| Flag | Default | Description |
| :--- | :--- | :--- |
| `--mode=<smoke\|api\|worker\|pipeline\|full>` | `full` | Benchmark execution mode |
| `--requests=<number>` | `2000` | Total number of requests/events |
| `--concurrency=<number>` | `25` | Concurrent client worker connections |
| `--transport=<http\|inject>` | `http` | Transport: `http` (real TCP loopback) or `inject` (Fastify in-process) |
| `--output=<filepath>` | Mode-specific | Path to write machine-readable JSON results (defaults: `load-test-results.json` for `full`, `load-test-smoke.json` for `smoke`, `load-test-<mode>.json` for component benchmarks, `load-test-<sim>-failure.json` for failure simulations) |

## Output and Evidence

- **Full Suite Evidence:** Saved automatically to [`evidence/load-test-results.json`](file:///c:/Users/USER/Desktop/System%20Design%20Task/evidence/load-test-results.json).
- **Smoke Evidence:** Saved to [`evidence/load-test-smoke.json`](file:///c:/Users/USER/Desktop/System%20Design%20Task/evidence/load-test-smoke.json) (prevents smoke runs from overwriting full-suite evidence).
- **Failure Simulations:** Saved to `evidence/load-test-<mode>-failure.json` when `--simulate-failure` is active.
- **Formal Evaluation Report:** Documented with full analysis in [`evidence/LOAD-TEST-REPORT.md`](file:///c:/Users/USER/Desktop/System%20Design%20Task/evidence/LOAD-TEST-REPORT.md).

### Machine-Readable JSON Schema Fields

#### 1. API Benchmark Metrics (`smoke`, `apiHttp`, `api`)
- `requests`: Total number of requests attempted.
- `concurrency`: Concurrency level.
- `transport`: `http` (persistent TCP keep-alive loopback) or `inject` (Fastify in-memory).
- `durationSec`: Duration in seconds.
- `completedCount`: Count of requests receiving `202 Accepted`.
- `errorCount`: Count of failed requests (status !== 202 or connection error).
- `throughputRps`: Achieved requests per second (`completedCount / durationSec`).
- `statusCounts`: Map of HTTP status codes to frequency (e.g. `{"202": 2000}`).
- `percentiles`: Latency percentiles in milliseconds (`min`, `p50`, `p90`, `p95`, `p99`, `max`, `mean`).
- `heapDeltaMb`: Memory heap delta before and after the run.
- `failed`: Boolean indicating if `errorCount > 0`.
- `failureReason`: Diagnostic string if failed.

#### 2. Worker Delivery Metrics (`worker`)
- `totalEvents`: Total notifications pre-seeded in the outbox.
- `batchSize`: Batch size per sweep.
- `totalDelivered`: Count of notifications successfully dispatched to provider and transitioned to `sent`.
- `errorCount`: Count of failed or timed-out worker attempts (`totalFailed + totalTimeout + totalFencedOut`).
- `retriedCount`: Count of transient failures rescheduled for retry.
- `suppressedCount`: Count of notifications suppressed due to missing opt-in preferences.
- `fencedOutCount`: Count of attempts rejected due to lease token expiry/fencing.
- `timeoutCount`: Count of ambiguous provider timeouts held for review.
- `durationSec`: Total drain duration in seconds.
- `throughputItemsPerSec`: Delivered items per second.
- `batchPercentiles`: Batch processing latencies in milliseconds.
- `failed`: Boolean indicating if `totalDelivered !== totalEvents || errorCount > 0`.

#### 3. Pipeline Metrics (`pipeline`)
- `count`: Total requests ingested.
- `concurrency`: Concurrency level.
- `completedIngest`: Ingestion requests successfully accepted with `202`.
- `errorCount`: Total pipeline errors (`(count - completedIngest) + (count - deliveredCount) + unfinishedOutboxCount`).
- `ingestDurationSec`: Ingestion duration in seconds.
- `ingestRps`: Ingestion requests per second.
- `totalDurationSec`: Total duration from first request until outbox drain.
- `deliveredCount`: Final notifications verified in state `sent`.
- `fencedOutCount`: Worker attempts rejected by fencing tokens (0 expected).
- `unfinishedOutboxCount`: Outbox events remaining in non-completed state (0 expected).
- `failed`: Boolean indicating if any ingestion or delivery errors occurred.
