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
This generates your `tables/` directory and `.env` file.

### 2. Create a Table
Scaffold a new table (e.g., `Customer`) with full CRUD operations (`-o crud`):
```bash
dataman create table Customer -o crud
```

### 3. Define Your Fields
Open the generated `tables/Customer/model.py` and define your Django fields:
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
dataman server start
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
