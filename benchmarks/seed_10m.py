import sys
import time
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

def init_django():
    from dataman import django_setup
    django_setup.setup()


def seed_scale_data(target_records=10_000_000, batch_size=50_000):
    """
    High-performance synthetic data generator using Django bulk_create.
    Generates Customer, Order, and OrderItem records with foreign key joins.
    """
    init_django()
    from django.apps import apps
    from django.db import transaction

    try:
        Customer = apps.get_model("tables", "Customer")
        Order = apps.get_model("tables", "Order")
    except LookupError:
        print("[-] Tables 'Customer' or 'Order' not found in tables/ app.")
        print("[!] Run: dataman create table Customer && dataman create table Order")
        return

    print(f"[*] Starting bulk generation of {target_records:,} records...")
    start_time = time.time()

    # 1. Seed base customers if needed (e.g. 100,000 customers)
    num_customers = min(100_000, target_records // 10)
    existing_customers = Customer.objects.count()
    if existing_customers < num_customers:
        print(f"[*] Seeding {num_customers - existing_customers:,} Customer records...")
        customer_objs = []
        for i in range(existing_customers + 1, num_customers + 1):
            customer_objs.append(
                Customer(
                    name=f"Customer_{i}",
                    email=f"user_{i}@enterprise.com",
                    country="US" if i % 2 == 0 else "EU",
                )
            )
            if len(customer_objs) >= batch_size:
                Customer.objects.bulk_create(customer_objs, batch_size=batch_size)
                customer_objs = []
        if customer_objs:
            Customer.objects.bulk_create(customer_objs, batch_size=batch_size)
        print(f"[+] Customers populated: {Customer.objects.count():,}")

    # 2. Seed 10M Orders linked to Customers
    customer_ids = list(Customer.objects.values_list("id", flat=True)[:10000])
    if not customer_ids:
        print("[-] No customer IDs found to associate orders.")
        return

    statuses = ["pending", "processing", "completed", "cancelled"]
    existing_orders = Order.objects.count()
    needed_orders = target_records - existing_orders

    if needed_orders <= 0:
        print(
            f"[+] Database already has {existing_orders:,} records. Ready for benchmark!"
        )
        return

    print(
        f"[*] Seeding {needed_orders:,} Order records in batches of {batch_size:,}..."
    )
    total_inserted = 0

    while total_inserted < needed_orders:
        current_batch_size = min(batch_size, needed_orders - total_inserted)
        orders = []

        for i in range(current_batch_size):
            idx = total_inserted + i + 1
            orders.append(
                Order(
                    customer_id=customer_ids[i % len(customer_ids)],
                    status=statuses[i % len(statuses)],
                    total_amount=round((idx % 500) + 19.99, 2),
                )
            )

        with transaction.atomic():
            Order.objects.bulk_create(orders, batch_size=batch_size)

        total_inserted += current_batch_size
        elapsed = time.time() - start_time
        rate = total_inserted / elapsed if elapsed > 0 else 0
        print(
            f"  -> Inserted {total_inserted:,}/{needed_orders:,} orders "
            f"({(total_inserted / needed_orders) * 100:.1f}%) "
            f"[{rate:,.0f} rows/sec]"
        )

    total_time = time.time() - start_time
    print(
        f"[+] Done! Successfully populated {target_records:,} records in {total_time:.2f}s "
        f"({target_records / total_time:,.0f} rows/sec average)."
    )


if __name__ == "__main__":
    count = int(sys.argv[1]) if len(sys.argv) > 1 else 10_000_000
    seed_scale_data(target_records=count)
