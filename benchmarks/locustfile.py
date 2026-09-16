import random
from locust import HttpUser, between, task


class DataManScaleUser(HttpUser):
    wait_time = between(0.05, 0.15)

    def on_start(self):
        self.headers = {
            "Accept": "application/json",
        }
        self.max_customer_id = 500

    @task(5)
    def test_multi_column_filter(self):
        """
        Tests multi-column indexed filter lookups across records
        using standard REST query parameters.
        """
        status = random.choice(["pending", "processing", "completed"])
        min_amount = random.randint(20, 250)
        self.client.get(
            f"/api/order/?status={status}&total_amount__gte={min_amount}&ordering=-created_at&page_size=25",
            headers=self.headers,
            name="GET /api/order/ [Multi-Column Filter + Order]",
        )

    @task(3)
    def test_complex_join_depth(self):
        """
        Tests complex join resolution (Customer -> Orders)
        using depth=2 serialization.
        """
        customer_id = random.randint(1, self.max_customer_id)
        self.client.get(
            f"/api/customer/{customer_id}/",
            headers=self.headers,
            name="GET /api/customer/{id}/ [Nested Relations]",
        )

    @task(2)
    def test_search_and_ordering(self):
        """
        Tests search & ordering across indexed tables.
        """
        search_term = random.choice(["Customer_1", "Customer_2", "user_1", "user_2"])
        self.client.get(
            f"/api/customer/?search={search_term}&ordering=-id&page_size=20",
            headers=self.headers,
            name="GET /api/customer/ [Search + Order]",
        )

    @task(1)
    def test_concurrent_writes(self):
        """
        Tests order creation throughput.
        """
        payload = {
            "customer": random.randint(1, self.max_customer_id),
            "status": "pending",
            "total_amount": f"{round(random.uniform(20.0, 500.0), 2):.2f}",
        }
        self.client.post(
            "/api/order/",
            json=payload,
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            name="POST /api/order/ [Concurrent Create]",
        )
