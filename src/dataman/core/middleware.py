import atexit
import logging
import queue
import threading
import time

from django.db import close_old_connections

from .models import APILog

logger = logging.getLogger("dataman.telemetry")

_log_queue = queue.Queue(maxsize=50000)
_shutdown_event = threading.Event()


def _telemetry_batch_worker():
    """
    High-performance background batch worker. Drains telemetry log queue
    and persists records using bulk_create to eliminate DB lock contention.
    """
    while not _shutdown_event.is_set() or not _log_queue.empty():
        batch = []
        try:
            item = _log_queue.get(timeout=0.05)
            batch.append(item)
            while len(batch) < 100:
                try:
                    batch.append(_log_queue.get_nowait())
                except queue.Empty:
                    break
        except queue.Empty:
            continue

        if not batch:
            continue

        close_old_connections()
        try:
            objs = [
                APILog(
                    method=m,
                    path=p,
                    status_code=s,
                    duration_ms=d,
                    ip_address=ip,
                )
                for (m, p, s, d, ip) in batch
            ]
            APILog.objects.bulk_create(objs, batch_size=100)
        except Exception as e:
            logger.error(f"Failed to batch write APILogs: {e}")
        finally:
            close_old_connections()
            for _ in batch:
                _log_queue.task_done()


_worker_thread = threading.Thread(
    target=_telemetry_batch_worker, name="dataman-telemetry-batch", daemon=True
)
_worker_thread.start()


def flush_telemetry_logs(timeout=5):
    """Barrier helper to ensure all queued background logs are flushed to database."""
    try:
        _log_queue.join()
    except Exception as e:
        logger.warning(f"Timeout flushing telemetry queue: {e}")


def _shutdown_telemetry():
    _shutdown_event.set()
    flush_telemetry_logs()


atexit.register(_shutdown_telemetry)


class APILoggingMiddleware:
    """Middleware to log API request metrics asynchronously for high concurrency."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        path = request.path
        # Only log requests to actual data endpoints,
        # exclude internal/dashboard/docs/health routes.
        if (
            not path.startswith("/api/")
            or path.startswith("/api/schema")
            or path.startswith("/api/docs")
            or path.startswith("/api/redoc")
            or path.startswith("/api/_internal/")
            or path.startswith("/api/health")
            or path.startswith("/health")
        ):
            return self.get_response(request)

        start_time = time.perf_counter()
        response = self.get_response(request)
        duration_ms = int((time.perf_counter() - start_time) * 1000)

        # Handle IP Address correctly behind proxies
        x_forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR")
        ip = (
            x_forwarded_for.split(",")[0]
            if x_forwarded_for
            else request.META.get("REMOTE_ADDR")
        )

        try:
            _log_queue.put_nowait(
                (request.method, path, response.status_code, duration_ms, ip)
            )
        except queue.Full:
            logger.warning("Telemetry log queue full, dropping record under extreme load.")

        return response
