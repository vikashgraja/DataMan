import hashlib
import secrets
import time

from django.contrib.auth.decorators import user_passes_test
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.db.models import Avg, Count
from django.shortcuts import render
from rest_framework import permissions, status, viewsets
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response

from .models import APILog, APIToken



class IsAdminOrLocal(permissions.BasePermission):
    """
    Allow access to dashboard APIs if the user is a superuser.
    """

    def has_permission(self, request, view):
        return bool(
            request.user and request.user.is_authenticated and request.user.is_superuser
        )


@api_view(["GET"])
@permission_classes([IsAdminOrLocal])
def analytics_summary(request):
    """Returns aggregated API usage metrics."""
    summary = (
        APILog.objects.values("path", "method")
        .annotate(count=Count("id"), avg_duration=Avg("duration_ms"))
        .order_by("-count")
    )

    total_requests = APILog.objects.count()
    total_errors = APILog.objects.filter(status_code__gte=400).count()
    avg_latency = APILog.objects.aggregate(Avg("duration_ms"))["duration_ms__avg"] or 0

    return Response(
        {
            "total_requests": total_requests,
            "total_errors": total_errors,
            "avg_latency": round(avg_latency, 2),
            "endpoints": list(summary),
        }
    )


@api_view(["GET"])
@permission_classes([IsAdminOrLocal])
def analytics_logs(request):
    """Returns the 50 most recent API logs."""
    logs = APILog.objects.all().order_by("-timestamp")[:50]
    data = [
        {
            "id": log.id,
            "timestamp": log.timestamp.isoformat(),
            "method": log.method,
            "path": log.path,
            "status_code": log.status_code,
            "duration_ms": log.duration_ms,
            "ip_address": log.ip_address,
        }
        for log in logs
    ]
    return Response(data)


class APITokenViewSet(viewsets.ViewSet):
    """Internal ViewSet to manage APITokens from the dashboard."""

    permission_classes = [IsAdminOrLocal]

    def list(self, request):
        tokens = APIToken.objects.all().order_by("-created_at")
        return Response(
            [
                {
                    "id": t.id,
                    "name": t.name,
                    "prefix": t.prefix,
                    "scopes": t.scopes,
                    "created_at": t.created_at,
                }
                for t in tokens
            ]
        )

    def create(self, request):
        name = request.data.get("name")
        scopes = request.data.get("scopes", [])

        if not name:
            return Response(
                {"error": "Name is required"}, status=status.HTTP_400_BAD_REQUEST
            )

        raw_secret = secrets.token_hex(16)
        prefix = raw_secret[:8]

        token = APIToken.objects.create(
            name=name,
            scopes=scopes,
            prefix=prefix,
            hashed_secret=hashlib.sha256(raw_secret.encode()).hexdigest(),
        )

        # We only return the raw token once!
        return Response(
            {
                "id": token.id,
                "name": token.name,
                "prefix": token.prefix,
                "scopes": token.scopes,
                "token": f"{prefix}_{raw_secret}",
            },
            status=status.HTTP_201_CREATED,
        )

    def destroy(self, request, pk=None):
        try:
            token = APIToken.objects.get(pk=pk)
            token.delete()
            return Response(status=status.HTTP_204_NO_CONTENT)
        except APIToken.DoesNotExist:
            return Response(status=status.HTTP_404_NOT_FOUND)


@user_passes_test(lambda u: u.is_superuser)
def dashboard_view(request):
    """Serves the dashboard HTML."""
    return render(request, "dashboard.html")


@api_view(["GET"])
@permission_classes([permissions.AllowAny])
def health_live(request):
    """Liveness probe: verifies that the web process is active and serving traffic."""
    return Response(
        {"status": "alive", "timestamp": time.time()},
        status=status.HTTP_200_OK,
    )


@api_view(["GET"])
@permission_classes([permissions.AllowAny])
def health_ready(request):
    """Readiness probe: verifies external dependencies (DB connection, migrations)."""
    checks = {}
    is_healthy = True

    # 1. Database connectivity check
    db_start = time.time()
    try:
        connection.ensure_connection()
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
        db_latency = round((time.time() - db_start) * 1000, 2)
        checks["database"] = {
            "status": "connected",
            "vendor": connection.vendor,
            "latency_ms": db_latency,
        }
    except Exception as e:
        is_healthy = False
        checks["database"] = {
            "status": "disconnected",
            "error": str(e),
        }

    # 2. Migrations check
    try:
        executor = MigrationExecutor(connection)
        targets = executor.loader.graph.leaf_nodes()
        unapplied_count = len(executor.migration_plan(targets))
        checks["migrations"] = {
            "status": "applied" if unapplied_count == 0 else "pending",
            "unapplied_count": unapplied_count,
        }
        if unapplied_count > 0:
            is_healthy = False
    except Exception as e:
        is_healthy = False
        checks["migrations"] = {
            "status": "error",
            "error": str(e),
        }

    resp_status = (
        status.HTTP_200_OK if is_healthy else status.HTTP_503_SERVICE_UNAVAILABLE
    )
    return Response(
        {
            "status": "ready" if is_healthy else "unhealthy",
            "timestamp": time.time(),
            "checks": checks,
        },
        status=resp_status,
    )


@api_view(["GET"])
@permission_classes([permissions.AllowAny])
def health_check(request):
    """General health check endpoint combining liveness and readiness."""
    return health_ready(request._request)

