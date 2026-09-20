# DataMan (Data MiddleMan)

[![Quickstart Guide](https://img.shields.io/badge/Docs-Quickstart_Guide-blue.svg)](https://github.com/vikashgraja/DataMan/blob/main/docs/QUICKSTART.md)
[![Databases](https://img.shields.io/badge/Databases-PostgreSQL%20%7C%20MySQL%20%7C%20SQLite-success.svg)](#supported-databases)
[![ASGI Engine](https://img.shields.io/badge/ASGI-Uvicorn%20Ready-purple.svg)](#asgi-production-mode)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://github.com/vikashgraja/DataMan/blob/main/LICENSE)

**DataMan** is a dynamic, headless backend engine built on top of Django and Django REST Framework. It eliminates the boilerplate of writing standard CRUD APIs, routing, and serializers by allowing you to scaffold endpoints instantly from the command line while preserving your ability to inject custom business logic and strict validation whenever you need it.

Looking to build an API in 5 minutes? Check out the [Developer Quickstart Tutorial](https://github.com/vikashgraja/DataMan/blob/main/docs/QUICKSTART.md).

---

## 30-Second Demo: Instant REST API

Get a fully functional, paginated, and documented REST API running in 4 commands:

```bash
# 1. Install DataMan
pip install dataman-engine

# 2. Initialize project in current directory
dataman init

# 3. Scaffold an Employee table with full CRUD operations
dataman create table Employee -o crud

# 4. Apply migrations and start the server
dataman makemigration && dataman migrate
dataman server start
```

### What You Get Instantly:
* **`GET    /api/default/employee/`** — Paginated list with multi-column filtering, search, and ordering.
* **`POST   /api/default/employee/`** — Create records with validation and lifecycle hooks.
* **`GET    /api/default/employee/{id}/`** — Point lookup with automatic relational joins (`?depth=1`).
* **`PUT    /api/default/employee/{id}/`** — Full update.
* **`PATCH  /api/default/employee/{id}/`** — Partial update.
* **`DELETE /api/default/employee/{id}/`** — Delete record.
* **Interactive Swagger UI**: [`http://127.0.0.1:8000/api/docs/`](http://127.0.0.1:8000/api/docs/)
* **OpenAPI 3.0 Schema**: [`http://127.0.0.1:8000/api/schema/`](http://127.0.0.1:8000/api/schema/)

---

## Why DataMan?

Building standard CRUD backends with existing frameworks requires boilerplate code:

```text
Without DataMan:
Model ---> Serializer ---> ViewSet ---> Router ---> FilterSet ---> Permissions

With DataMan:
Model ---> Done! (Instant REST Endpoints + Swagger Docs + Auth)
```

### Framework Comparison

| Framework | CRUD API Setup Requirements |
| :--- | :--- |
| **Django REST Framework (DRF)** | Model + Serializer + ViewSet + Router + FilterSet + Permissions |
| **FastAPI** | SQLAlchemy Model + Pydantic Schema + Router + Endpoints + Dependency Injection |
| **DataMan** | **Django Model Only (Instant Endpoints, Swagger, Auth & Hooks)** |

---

## Out-of-the-Box API Features

Every scaffolded endpoint automatically delivers production response envelopes:

```json
// GET /api/default/employee/?is_active=true&search=Engineering&ordering=-created_at
{
  "count": 48,
  "next": "http://127.0.0.1:8000/api/default/employee/?page=2",
  "previous": null,
  "results": [
    {
      "id": 101,
      "name": "Jane Doe",
      "department": "Engineering",
      "email": "jane.doe@example.com",
      "is_active": true,
      "created_at": "2026-09-19T18:00:00Z"
    }
  ]
}
```

* **Filtering**: Filter by query params (e.g., `?is_active=true&salary__gte=80000`).
* **Fast B-Tree Prefix Search**: Full-text prefix seeks on indexed columns (e.g., `?search=Jane`).
* **Multi-Column Ordering**: Sort descending or ascending (e.g., `?ordering=-created_at,salary`).
* **Fast Pagination**: Sliced pagination without expensive full-table `COUNT(*)` overhead on million-row tables.
* **Relational Expansion**: Expand foreign keys automatically (e.g., `?depth=1`).

---

## Supported Databases

DataMan provides native connection pooling, health probes, and migration routing for:

| Database | Support Level | Recommended Use |
| :--- | :--- | :--- |
| **PostgreSQL** | Primary / Production | 1M+ scale workloads, multi-database routing, high-concurrency ASGI |
| **MySQL / MariaDB** | Production | Enterprise relational storage with connection lifecycle pooling |
| **SQLite** | Prototyping | Zero-configuration local development and rapid test suites |

---

## Enterprise Adoption & Target Use Cases

DataMan is designed for data-intensive enterprise architectures:
* Continuous Control Monitoring (CCM) & internal audit platforms.
* SAP & ERP Data Middleware: Expose transactional SQL stores as structured REST APIs.
* Master Data Management (MDM): Instant CRUD data maintenance portals.
* Approval Workflows & Vendor Portals: Scoped, token-authenticated transactional APIs.

---

## Advanced Features

Every table generated under `tables/<TableName>/` gives you modular files for fine-grained control:

### 1. `config.py` (API Settings)
```python
# tables/Customer/config.py
ALLOWED_OPERATIONS = ["C", "R"]  # Only allow Create (POST) and Read (GET)
REQUIRE_AUTH = True  # Lock down this endpoint
DEPTH = 1  # Automatically serialize nested Foreign Key relationships on read (GET)

# Advanced Filtering, Search & Ordering
FILTER_FIELDS = {
    "price": ["gte", "lte", "exact"],
    "name": ["icontains", "exact"],
    "is_active": ["exact"],
}
SEARCH_FIELDS = ["^name", "^email"]  # Prefix seek for optimal B-Tree index utilization
ORDERING_FIELDS = ["created_at", "price"]
```

### 2. Authentication & Scoped Access Tokens
Generate restricted, fine-grained access tokens directly from the CLI:
```bash
dataman token create "Frontend Service" --scopes "customer:read,order:create"
```
Use the token with standard `Bearer` or `Token` headers:
```http
Authorization: Bearer <your_generated_token_key>
```

### 3. `validation.py` (Data Validation)
Validate incoming JSON payloads before database execution. Raise `ValidationError` to immediately return `400 Bad Request`.
```python
# tables/Customer/validation.py
from rest_framework.exceptions import ValidationError


def validate_customer(data):
    if "admin" in data.get("name", "").lower():
        raise ValidationError({"name": "Reserved keyword used."})
    data["name"] = data["name"].strip().title()
    return data
```

### 4. `service.py` (Pre/Post Lifecycle Hooks)
Run transactional business logic before or after database commits (`before_create`, `after_create`, `before_update`, `after_update`, `before_destroy`, `after_destroy`):
```python
# tables/Order/service.py
from rest_framework.exceptions import ValidationError
from tables.Product.models import Product


def before_create(data):
    product = Product.objects.get(id=data["product"])
    quantity = int(data.get("quantity", 1))
    if product.stock_quantity < quantity:
        raise ValidationError({"quantity": "Insufficient inventory available."})
    product.stock_quantity -= quantity
    product.save()
    return data
```

### 5. Multi-Database Architecture
Organize large projects across isolated physical databases:
```bash
# 1. Create a database namespace
dataman create database analytics_db

# 2. Scaffold a table bound to that database
dataman create table events --database analytics_db

# 3. Run migrations across all databases (or target a single database)
dataman migrate --database analytics_db
```
Endpoints are automatically registered at `api/<database_name>/<table_name>/` (e.g. `api/default/employee/` or `api/analytics_db/events/`).

---

## Production Health & Monitoring

DataMan comes with built-in health check endpoints for Kubernetes, Docker, and AWS ECS:
* `GET /health/live/`: Process liveness probe returning `200 OK`.
* `GET /health/ready/`: Sanitized readiness probe validating database connectivity and migration synchronization (returns `200 OK` or `503 Service Unavailable`).
* `GET /health/`: Unified health status with database latency metrics.

---

## ASGI Production Mode

Start high-concurrency production server powered by Uvicorn:
```bash
dataman server start --asgi --host 0.0.0.0 --port 8000 --workers 4
```

---

## Testing

DataMan is tested with **95%+ branch coverage**, verifying authentication edge cases, multi-database routing, and dynamic lifecycle hooks.

```bash
uv run pytest tests/ -v
```

---

## License
MIT License
