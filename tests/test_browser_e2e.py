import asyncio
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest
from playwright.async_api import async_playwright


def get_free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("", 0))
        return s.getsockname()[1]


def wait_for_server(port, timeout=15):
    start = time.time()
    while time.time() - start < timeout:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=1):
                return True
        except (OSError, ConnectionRefusedError):
            time.sleep(0.3)
    return False


def test_playwright_browser_e2e_console_flow(tmp_path):
    """End-to-end browser automation testing DataMan Console using Playwright."""
    asyncio.run(_run_playwright_e2e(tmp_path))


async def _run_playwright_e2e(tmp_path):
    port = get_free_port()

    # 1. Initialize project
    subprocess.check_call(
        [sys.executable, "-m", "dataman.cli", "init"], cwd=str(tmp_path)
    )
    subprocess.check_call(
        [sys.executable, "-m", "dataman.cli", "create", "table", "Customer", "-o", "crud"],
        cwd=str(tmp_path),
    )

    # Allow test server hosts in .env
    env_file = tmp_path / ".env"
    if env_file.exists():
        env_file.write_text(env_file.read_text().replace("ALLOWED_HOSTS=\n", "ALLOWED_HOSTS=*\n"))

    # 2. Run migrations and create admin superuser
    setup_script = """
import os
os.environ["ALLOWED_HOSTS"] = "*"
import sys
from pathlib import Path
sys.path.insert(0, str(Path.cwd()))

from dataman import django_setup
django_setup.setup()

from django.core.management import call_command
call_command("makemigrations")
call_command("migrate")

from django.contrib.auth.models import User
if not User.objects.filter(username="admin").exists():
    User.objects.create_superuser("admin", "admin@example.com", "AdminPass123!")
print("SUPERUSER_CREATED")
"""
    (tmp_path / "setup_admin.py").write_text(setup_script)
    subprocess.check_call([sys.executable, "setup_admin.py"], cwd=str(tmp_path))

    # 3. Start DataMan server in background with ASGI
    server_log_file = tmp_path / "server_output.log"
    server_log = open(server_log_file, "w", encoding="utf-8")
    server_proc = subprocess.Popen(
        [sys.executable, "-m", "dataman.cli", "server", "start", "--port", str(port), "--host", "127.0.0.1", "--asgi"],
        cwd=str(tmp_path),
        stdout=server_log,
        stderr=subprocess.STDOUT,
    )

    try:
        assert wait_for_server(port, timeout=15), f"DataMan server failed to start on port {port}"

        # 4. Launch Playwright browser session
        async with async_playwright() as p:
            try:
                browser = await p.chromium.launch(channel="msedge", headless=True)
            except Exception:
                try:
                    browser = await p.chromium.launch(channel="chrome", headless=True)
                except Exception:
                    browser = await p.chromium.launch(headless=True)

            context = await browser.new_context(viewport={"width": 1440, "height": 900})
            page = await context.new_page()
            page.on("console", lambda msg: print(f"[BROWSER CONSOLE] {msg.type}: {msg.text}"))
            page.on("pageerror", lambda err: print(f"[BROWSER ERROR] {err}"))

            base_url = f"http://127.0.0.1:{port}"

            # Step A: Navigating to /admin/login/
            await page.goto(f"{base_url}/admin/login/", wait_until="domcontentloaded")
            await page.wait_for_selector("#id_username", timeout=5000)
            title = await page.title()
            assert "Sign In" in title or "DataMan" in title

            # Step B: Enter credentials and log in
            await page.fill("#id_username", "admin")
            await page.fill("#id_password", "AdminPass123!")
            await page.click("#login-submit-button")
            await page.wait_for_url("**/admin/", wait_until="domcontentloaded", timeout=10000)
            await page.wait_for_selector("#nav-analytics", timeout=10000)

            # Step C: Verify Analytics Tab
            assert await page.is_visible("#nav-analytics")
            assert await page.is_visible("#tab-analytics")

            # Step D: Switch to Tables Catalog Tab
            await page.evaluate("switchTab('tables')")
            await page.wait_for_timeout(500)
            assert await page.is_visible("#tab-tables")

            # Step E: Switch to API Tokens & RBAC Tab and generate a token
            await page.click("#nav-rbac", force=True)
            await page.wait_for_timeout(500)
            assert await page.is_visible("#tab-rbac")

            await page.click("button:has-text('Generate Token')", force=True)
            await page.wait_for_timeout(300)
            await page.fill("#new-token-name", "TestE2EToken")
            await page.fill("#new-token-scopes", "customer:read, customer:write")
            await page.click("button:has-text('Create Token')", force=True)
            await page.wait_for_timeout(800)

            # Verify token reveal key is generated and visible
            revealed_key = await page.inner_text("#revealed-token-key")
            assert len(revealed_key) > 10, f"Expected valid token string, got '{revealed_key}'"
            assert await page.is_visible("#copy-token-btn")
            await page.click("button:has-text('Done')", force=True)
            await page.wait_for_timeout(300)

            # Step F: Switch to Security & Audit Tab
            await page.click("#nav-audit", force=True)
            await page.wait_for_timeout(500)
            assert await page.is_visible("#tab-audit")

            # Step G: Switch to System Health Tab and Run Probe
            await page.click("#nav-health", force=True)
            await page.wait_for_timeout(500)
            assert await page.is_visible("#tab-health")

            await page.click("#run-probe-btn", force=True)
            await page.wait_for_timeout(1000)
            ready_text = await page.inner_text("#health-ready-val")
            assert "READY" in ready_text.upper()

            # Step H: Test Logout
            logout_btn = page.locator("button[title='Sign Out']")
            if await logout_btn.count() > 0:
                await logout_btn.click(force=True)
                await page.wait_for_url("**/admin/login/**", wait_until="domcontentloaded", timeout=10000)

            await browser.close()
    finally:
        server_proc.terminate()
        try:
            server_proc.wait(timeout=3)
        except Exception:
            server_proc.kill()
