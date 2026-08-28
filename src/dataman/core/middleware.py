import time

from .models import APILog


class APILoggingMiddleware:
    """Middleware to log API request metrics for the dashboard."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        path = request.path
        # Only log requests to actual data endpoints,
        # exclude internal/dashboard/docs routes.
        if (
            not path.startswith("/api/")
            or path.startswith("/api/schema")
            or path.startswith("/api/docs")
            or path.startswith("/api/_internal/")
        ):
            return self.get_response(request)

        start_time = time.time()
        response = self.get_response(request)
        duration_ms = int((time.time() - start_time) * 1000)

        # Handle IP Address correctly behind proxies
        x_forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR")
        if x_forwarded_for:
            ip = x_forwarded_for.split(",")[0]
        else:
            ip = request.META.get("REMOTE_ADDR")

        APILog.objects.create(
            method=request.method,
            path=path,
            status_code=response.status_code,
            duration_ms=duration_ms,
            ip_address=ip,
        )

        return response
