# DataMan High-Performance Scale Benchmark Report

**Generated**: October 09, 2026 - 21:30:00
**Commit**: `d189caa`
**Environment**: Windows 11 (AMD64) | 12 vCPUs
**Database Engine**: PostgreSQL
**Framework**: `dataman-engine` v0.4.0 (ASGI / Uvicorn)
**Scale Target**: 1,000,000 Records | 20 Concurrent Users | 10 Spawn Rate | 10s Duration

---

## 1. Database & ORM Engine Scale Performance (1,000,000+ Records)

Measures raw query execution and index lookup performance directly on the database engine.

| Workload / Query Type | Query Scope | Average Latency | Performance Status |
| :--- | :--- | :--- | :--- |
| **Point Lookup (Indexed PK)** | 1 row | **0.720 ms** | Sub-millisecond (DB Query) |
| **Multi-Column Filter + Order** | 25 rows | **1.459 ms** | High-Speed (DB Query) |
| **FK Join (`select_related`)** | 50 rows | **2.533 ms** | Optimized (DB Query) |
| **Reverse Join (`prefetch_related`)** | 25 parents + children | **37.404 ms** | Batch Joined (DB Query) |
| **Scale Aggregation (AVG + COUNT)** | 1,000,000+ rows | **88.505 ms** | Parallel Scan (DB Query) |

---

## 2. High-Concurrency API Stress Test Summary

| Metric | Measured Result | Benchmark Target | Status |
| :--- | :--- | :--- | :--- |
| **Database Engine** | **PostgreSQL** | PostgreSQL / SQLite | Active |
| **Database Row Baseline** | **0 initial -> 1,000,000 total** (1,000,000 inserted) | Pre/Post Counts | Verified |
| **Database Bulk Seeding Throughput** | **42,350 rows/sec** (1,000,000 rows in 23.61s) | Database `bulk_create` | Passed |
| **HTTP API Peak Throughput** | **24.85 req/sec** | > 15 req/sec | Passed |
| **HTTP Success Rate** | **100.00%** (225/225) | > 99.0% | Flawless |
| **Median API Latency** | **580 ms** | < 800 ms | Passed |
| **95th Percentile Latency** | **1000 ms** | < 2,000 ms | Passed |

---

## 3. Endpoint Latency & Throughput Breakdown

| HTTP Method | Endpoint | Query Workload | Req/s | Median | Avg Latency | 95th % | Failure Rate |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| `GET` | `GET /api/default/customer/ [Search + Order]` | Workload Task | 4.53 | **930 ms** | 914 ms | 1100 ms | **0.00%** |
| `GET` | `GET /api/default/customer/{id}/ [Nested Relations]` | Workload Task | 6.85 | **440 ms** | 427 ms | 630 ms | **0.00%** |
| `GET` | `GET /api/default/order/ [Multi-Column Filter + Order]` | Workload Task | 11.71 | **620 ms** | 631 ms | 1000 ms | **0.00%** |
| `POST` | `POST /api/default/order/ [Concurrent Create]` | Workload Task | 1.77 | **410 ms** | 436 ms | 850 ms | **0.00%** |

---

## Key Architectural Enhancements

1. **Automatic Relation Pre-fetching (`select_related` / `prefetch_related`)**:
   - Derives relations from serializer fields to eliminate N+1 queries without unbounded traversal.
2. **Non-Blocking Telemetry (`APILog`)**:
   - Dedicated background thread pool worker prevents request execution from blocking on logging I/O.
3. **Transaction Safety**:
   - Wraps lifecycle hooks in explicit `transaction.atomic()` blocks and defers webhooks to `transaction.on_commit()`.
4. **Optimized Connection Lifecycle (`CONN_MAX_AGE=0`)**:
   - Prevents connection exhaustion across massive concurrency spikes on PostgreSQL.

---

## Reproduction Command
```bash
uv run python benchmarks/run_benchmark.py --database-url postgres://postgres:postgres@localhost:5432/dataman_db --users 20 --spawn-rate 10 --run-time 10s --records 1000000
```
