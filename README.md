# DataMan (Data MiddleMan)

**DataMan** is a dynamic, CLI-driven backend framework built on top of Django and Django REST Framework. It eliminates the boilerplate of writing standard CRUD APIs, routing, and serializers by allowing you to scaffold endpoints instantly from the command line while preserving your ability to inject custom business logic and strict validation whenever you need it.

---

## Features
- **Instant CRUD APIs**: Automatically generate RESTful APIs from simple model definitions.
- **CLI Scaffolding**: Setup projects and table structures with simple commands.
- **Hook-Based Business Logic**: Inject custom logic via `service.py` (`before_create`, `after_delete`, etc.) without touching serializers or viewsets.
- **Validation Injection**: Run custom data validators before database commits via `validation.py`.
- **Fine-Grained Authentication**: Lock down endpoints using granular, table-and-operation specific scopes (e.g., `customer:read`, `order:write`).
- **Dynamic Routing & Pagination**: Built-in DRF integration with default pagination and dynamic URL mappings.
- **Production Health Probes**: Built-in `/health/live/` and `/health/ready/` endpoints for Kubernetes/Docker container monitoring, database vitality, and migration checks.
- **Multi-Database Routing**: Organize tables by database directory (`<database>/<table>`) and route traffic, migrations, and health checks across isolated databases.
- **ASGI High-Concurrency Engine**: Built-in `uvicorn` server execution mode (`dataman server start --asgi`) for high throughput asynchronous performance.

---

## Installation

Ensure you have Python 3.10+ installed.

You can install DataMan (if published to PyPI) or clone the repository and install it using `uv` or `pip`:

```bash
# Using uv (Recommended)
uv pip install -e .

# Using pip
pip install -e .
```

---

## Quick Start

Get a full REST API running in under a minute!

### 1. Initialize a Project
Run the following in an empty directory to scaffold the necessary environment:
```bash
dataman init
```
This generates your project configuration:
```text
my-project/
├── .env           # Environment variables & secrets
├── database.py    # Database connection & pooling (SQLite default, Postgres, MySQL)
├── config.py      # Project settings (Hosts, CORS, pagination, custom middleware)
└── tables/        # API tables & database migrations
```

### 2. Configure Database & Project Settings (Optional)
Easily customize your database backend in `database.py` (e.g., PostgreSQL or MySQL) and global settings in `config.py`:
```python
# database.py
DATABASES = {
    "default": dj_database_url.config(
        default="postgres://user:pass@localhost:5432/my_db",
        conn_max_age=600,
    )
}
```

### 3. Create a Table
Scaffold a new table (e.g., `Customer`) with full CRUD operations (`-o crud`):
```bash
dataman create table Customer -o crud
```

### 4. Define Your Fields
Open the generated `tables/Customer/models.py` and define your Django fields:
```python
from django.db import models


class Customer(models.Model):
    name = models.CharField(max_length=255)
    email = models.EmailField(unique=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "customer"
```

### 4. Migrate and Run
Apply the database migrations and start the server!
```bash
dataman makemigration
dataman migrate

# Start development WSGI server
dataman server start

# OR start high-concurrency production ASGI server with Uvicorn
dataman server start --asgi --host 0.0.0.0 --port 8000 --workers 4
```
*Your API is now live at `http://127.0.0.1:8000/api/customer/`!*

---

## Advanced Usage

DataMan abstracts away the boring parts but leaves you full control over the important logic. Every table generated under `tables/<TableName>/` comes with four critical files:

### 1. `config.py` (API Settings)
Control exactly what HTTP methods are exposed and whether the table requires authentication.
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
}  # Or simple list: ["name", "email"]
SEARCH_FIELDS = ["name", "email"]
ORDERING_FIELDS = ["created_at", "price"]
```

### 2. Authentication & Scopes
If `REQUIRE_AUTH = True`, clients must provide a token in the `Authorization` header. You can generate fine-grained access tokens directly from the CLI:
```bash
dataman users create-token MyFrontendService --scopes customer:read,customer:create
```
Use the token in your requests:
```http
Authorization: Token <your_generated_token_key>
```

### 3. `validation.py` (Data Validation)
Validate incoming JSON payloads before they are passed to the database. Raise `ValidationError` to immediately return a `400 Bad Request`.
```python
# tables/Customer/validation.py
from rest_framework.exceptions import ValidationError


def validate(data):
    if "admin" in data.get("name", "").lower():
        raise ValidationError({"name": "Reserved keyword used."})

    # You can also mutate incoming data
    data["name"] = data["name"].strip().title()
    return data
```

### 4. `service.py` (Pre/Post Hooks)
Run business logic right before or after the database commits a transaction. Available hooks: `before_create`, `after_create`, `before_update`, `after_update`, `before_destroy`, `after_destroy`.

```python
# tables/Customer/service.py
def before_create(data):
    # E.g., hash a password, trigger a background task, or enforce rules
    if not data.get("email"):
        raise ValueError("Email is strictly required")


def after_create(instance):
    # instance is the saved Django model object
    print(f"Successfully created customer: {instance.name}")
```

### 5. Health Checks & Readiness Probes
DataMan comes with built-in health check endpoints designed for cloud platforms, load balancers, and orchestrators (Kubernetes, AWS ECS, Docker):
* `GET /health/live/` (or `/api/health/live/`): Liveness probe returning `200 OK` indicating the process is alive.
* `GET /health/ready/` (or `/api/health/ready/`): Readiness probe validating active database connection integrity and unapplied migrations (returns `200 OK` or `503 Service Unavailable`).
* `GET /health/` (or `/api/health/`): Unified health status with database latency metrics.

### 6. Multi-Database Architecture
Organize large projects with isolated physical databases:

```text
my_project/
├── database.py
├── config.py
├── analytics_db/
│   ├── events/
│   │   ├── models.py
│   │   └── config.py
│   └── metrics/
│       ├── models.py
│       └── config.py
└── core_db/
    └── users/
        ├── models.py
        └── config.py
```

1. Create a database:
```bash
dataman create database analytics_db
```
2. Scaffold a table bound to that database:
```bash
dataman create table events --database analytics_db
```
3. Run migrations across all databases (or target a single database):
```bash
dataman migrate
# or
dataman migrate --database analytics_db
```
Endpoints are automatically registered at both `api/<table_name>/` and namespaced `api/<database_name>/<table_name>/`.

---

## Testing

DataMan is rigorously tested with **95%+ branch coverage**, verifying extreme edge cases, token validations, and dynamic hook executions.

To run the test suite:
```bash
uv run pytest tests/ -v
```

---

## License
MIT License
