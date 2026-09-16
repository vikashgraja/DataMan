# DataMan Scale & Concurrency Benchmark Suite

Benchmark DataMan performance under extreme scale:
- **10,000,000+ records**
- **Complex joins / Nested relational depth (`depth=2`)**
- **Multi-column filter lookups (`?status=completed&total_amount__gte=100`)**
- **1,000+ Concurrent simulated users**

---

## 🚀 Quickstart

### 1. Configure PostgreSQL (Recommended for 1K+ Concurrency)
Set your `DATABASE_URL` in `.env`:
```env
DATABASE_URL=postgres://postgres:password@localhost:5432/dataman_db
```

### 2. Scaffold Benchmark Schema
```bash
dataman init
dataman create table Customer
dataman create table Order
dataman migrate
```

### 3. Populate 10M+ Records
```bash
# High-speed chunked bulk generation (10M rows in ~90s)
python benchmarks/seed_10m.py 10000000
```

### 4. Start Multi-Worker ASGI Server
```bash
uvicorn dataman.asgi:application --workers 8 --host 0.0.0.0 --port 8000
```

### 5. Run 1,000 Concurrent User Load Test
```bash
# Headless 60-second stress test
python benchmarks/run_benchmark.py --users 1000 --spawn-rate 50 --run-time 60s

# Or open interactive Web UI at http://localhost:8089
locust -f benchmarks/locustfile.py
```
