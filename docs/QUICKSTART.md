# 🚀 DataMan Developer Quickstart Guide

Welcome to **DataMan**! This hands-on guide will take you from zero to a live, production-grade REST API backend in under 5 minutes.

---

## ⚡ What is DataMan?

DataMan is a headless backend engine built on top of Django and Django REST Framework. It allows you to:
- 🏗️ **Scaffold APIs instantly** from simple model definitions.
- ⚡ **Zero Boilerplate**: No manual serializers, views, or routing required.
- 🧩 **Inject Custom Logic**: Use lifecycle hooks (`service.py`) and data validators (`validation.py`).
- 🔐 **Granular Token Auth**: Per-table and per-operation API tokens out of the box.
- 🚀 **High Concurrency**: ASGI / Uvicorn support with sub-millisecond query execution.

---

## ⏱️ 5-Minute Tutorial: Build an E-Commerce Store API

### Step 1: Install DataMan

Ensure you have Python 3.12+ installed.

```bash
# Using uv (Recommended)
uv add dataman-engine

# Or using pip
pip install dataman-engine
```

---

### Step 2: Initialize Your Project

Create a new directory and initialize DataMan:

```bash
mkdir store-api
cd store-api
dataman init
```

This creates the project layout:
```text
store-api/
├── .env           # Environment variables & secrets
├── database.py    # Database connection (SQLite default, PostgreSQL/MySQL)
├── config.py      # CORS, pagination, throttling, and middleware settings
└── tables/        # Your API tables, models, and business logic
```

---

### Step 3: Scaffold Your First Tables

Create two tables: `Product` and `Order` with full CRUD operations (`-o crud`):

```bash
dataman create table Product -o crud
dataman create table Order -o crud
```

---

### Step 4: Define Your Schema

Open `tables/Product/models.py` and replace it with:

```python
from django.db import models


class Product(models.Model):
    title = models.CharField(max_length=200)
    sku = models.CharField(max_length=50, unique=True)
    price = models.DecimalField(max_digits=10, decimal_places=2)
    stock_quantity = models.IntegerField(default=0)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "store_product"
```

Open `tables/Order/models.py` and define the relational schema:

```python
from django.db import models


class Order(models.Model):
    STATUS_CHOICES = [
        ("pending", "Pending"),
        ("paid", "Paid"),
        ("shipped", "Shipped"),
    ]

    product = models.ForeignKey(
        "Product.Product", on_delete=models.CASCADE, related_name="orders"
    )
    customer_email = models.EmailField()
    quantity = models.PositiveIntegerField(default=1)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="pending")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "store_order"
```

---

### Step 5: Run Migrations and Start Server

```bash
# 1. Generate migrations
dataman makemigration

# 2. Apply migrations to database
dataman migrate

# 3. Start development server
dataman server start
```

Your API is now live at **`http://127.0.0.1:8000/api/`**! 🎉

---

## 📡 Testing Your Endpoints

### 1. Create a Product (`POST /api/product/`)
```bash
curl -X POST http://127.0.0.1:8000/api/product/ \
  -H "Content-Type: application/json" \
  -d '{
    "title": "Wireless Noise Cancelling Headphones",
    "sku": "AUDIO-WH1000",
    "price": "299.99",
    "stock_quantity": 50
  }'
```

**Response (`201 Created`):**
```json
{
  "id": 1,
  "title": "Wireless Noise Cancelling Headphones",
  "sku": "AUDIO-WH1000",
  "price": "299.99",
  "stock_quantity": 50,
  "is_active": true,
  "created_at": "2026-09-17T16:00:00Z"
}
```

### 2. Query Products with Filtering, Search & Ordering (`GET /api/product/`)
```bash
# Filter active products, search title, and order by price descending
curl "http://127.0.0.1:8000/api/product/?is_active=true&search=Wireless&ordering=-price"
```

### 3. Create an Order (`POST /api/order/`)
```bash
curl -X POST http://127.0.0.1:8000/api/order/ \
  -H "Content-Type: application/json" \
  -d '{
    "product": 1,
    "customer_email": "buyer@example.com",
    "quantity": 2
  }'
```

### 4. Fetch Nested Relations (`GET /api/order/?depth=1`)
```bash
curl "http://127.0.0.1:8000/api/order/1/?depth=1"
```

---

## 🧠 Adding Custom Business Logic & Validation

### 1. Data Validation (`tables/Product/validation.py`)
Add custom validation before records are saved to the database:

```python
from rest_framework.exceptions import ValidationError


def validate_product(data):
    if float(data.get("price", 0)) <= 0:
        raise ValidationError({"price": "Price must be strictly greater than $0.00."})
    if int(data.get("stock_quantity", 0)) < 0:
        raise ValidationError({"stock_quantity": "Stock quantity cannot be negative."})
    return data
```

### 2. Service Hooks (`tables/Order/service.py`)
Run lifecycle actions (e.g., auto-decrementing product inventory upon purchase):

```python
from rest_framework.exceptions import ValidationError
from tables.Product.models import Product


def before_create(data):
    product = Product.objects.get(id=data["product"])
    quantity = int(data.get("quantity", 1))

    if product.stock_quantity < quantity:
        raise ValidationError(
            {"quantity": f"Only {product.stock_quantity} units available in stock."}
        )

    # Decrement inventory
    product.stock_quantity -= quantity
    product.save()
    return data
```

---

## 🔐 Securing Endpoints with Scoped API Tokens

### 1. Create a Restricted API Token
```bash
# Generate a read-only token for the product catalog
dataman token create "Catalog Reader" --scopes "product:read"

# Generate a read-write token for order processing
dataman token create "Order Service" --scopes "order:read,order:write,product:read"
```

### 2. Use Token in Requests
```bash
curl http://127.0.0.1:8000/api/product/ \
  -H "Authorization: Bearer <YOUR_TOKEN>"
```

---

## 🩺 Production Health & Monitoring

DataMan includes built-in health probes:

```bash
# Liveness probe (Container vitality)
curl http://127.0.0.1:8000/health/live/

# Readiness probe (DB connection & migration sync)
curl http://127.0.0.1:8000/health/ready/
```

---

## 🚀 High-Concurrency ASGI Production Mode

To run DataMan with asynchronous ASGI workers (powered by Uvicorn):

```bash
dataman server start --asgi --host 0.0.0.0 --port 8000 --workers 4
```

---

## 📋 CLI Quick Reference

| Command | Description |
| :--- | :--- |
| `dataman init` | Initialize a new DataMan project in current directory |
| `dataman create table <Name> -o crud` | Scaffold a table with CRUD operations |
| `dataman makemigration` | Generate schema migrations |
| `dataman migrate` | Apply database migrations |
| `dataman server start` | Start development server |
| `dataman server start --asgi` | Start production ASGI server (Uvicorn) |
| `dataman token create "<Name>" --scopes "<Scopes>"` | Generate scoped API authentication token |
| `dataman token list` | View active tokens and scopes |
| `dataman token revoke <ID>` | Revoke an API token |
| `dataman db status` | Check connection and migration sync status |
