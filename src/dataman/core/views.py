import contextlib
import csv
import hashlib
import io
import json
import logging
import secrets
import time
from datetime import timedelta

from django.contrib.auth.decorators import user_passes_test
from django.db.migrations.executor import MigrationExecutor
from django.db.models import Avg, Count, Q
from django.http import HttpResponse
from django.shortcuts import render
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from rest_framework import permissions, status, viewsets
from rest_framework.decorators import (
    action,
    api_view,
    authentication_classes,
    permission_classes,
    renderer_classes,
)
from rest_framework.renderers import BaseRenderer, BrowsableAPIRenderer, JSONRenderer
from rest_framework.response import Response

from .audit import log_audit_event
from .auth import CsrfExemptSessionAuthentication, ServiceTokenAuthentication
from .models import APILog, APIToken, AuditLog

logger = logging.getLogger(__name__)


class CSVRenderer(BaseRenderer):
    """Renderer to allow DRF format_suffix negotiation for CSV downloads."""

    media_type = "text/csv"
    format = "csv"

    def render(self, data, accepted_media_type=None, renderer_context=None):
        return data


class PlainJSONRenderer(BaseRenderer):
    """Renderer to allow DRF format_suffix negotiation for JSON file exports."""

    media_type = "application/json"
    format = "json"

    def render(self, data, accepted_media_type=None, renderer_context=None):
        return data


class IsAdminOrLocal(permissions.BasePermission):
    """
    Allow access to dashboard APIs if the user is a superuser.
    """

    def has_permission(self, request, view):
        return bool(
            request.user and request.user.is_authenticated and request.user.is_superuser
        )


@api_view(["GET"])
@authentication_classes([CsrfExemptSessionAuthentication, ServiceTokenAuthentication])
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
@authentication_classes([CsrfExemptSessionAuthentication, ServiceTokenAuthentication])
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


@api_view(["GET"])
@authentication_classes([CsrfExemptSessionAuthentication, ServiceTokenAuthentication])
@permission_classes([IsAdminOrLocal])
@renderer_classes([CSVRenderer, PlainJSONRenderer, JSONRenderer])
def analytics_export(request):
    """Exports API telemetry logs as CSV or JSON."""
    fmt = request.query_params.get("format", "csv").lower()
    limit = min(int(request.query_params.get("limit", 10000)), 50000)
    logs = APILog.objects.all().order_by("-timestamp")[:limit]

    timestamp_str = timezone.now().strftime("%Y%m%d_%H%M%S")

    if fmt == "json":
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
        response = HttpResponse(
            json.dumps(data, indent=2), content_type="application/json"
        )
        response["Content-Disposition"] = (
            f'attachment; filename="api_telemetry_{timestamp_str}.json"'
        )
        return response

    # Default CSV
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(
        [
            "id",
            "timestamp",
            "method",
            "path",
            "status_code",
            "duration_ms",
            "ip_address",
        ]
    )
    for log in logs:
        writer.writerow(
            [
                log.id,
                log.timestamp.isoformat(),
                log.method,
                log.path,
                log.status_code,
                log.duration_ms,
                log.ip_address or "",
            ]
        )

    response = HttpResponse(output.getvalue(), content_type="text/csv")
    response["Content-Disposition"] = (
        f'attachment; filename="api_telemetry_{timestamp_str}.csv"'
    )
    return response


def parse_token_expiration(val):
    """Helper to parse expiration strings/integers/dates."""
    if not val or val == "never":
        return None
    now = timezone.now()
    if isinstance(val, int):
        return now + timedelta(days=val)
    if isinstance(val, str):
        val_clean = val.strip().lower()
        if val_clean == "never":
            return None
        if val_clean.endswith("d"):
            return now + timedelta(days=int(val_clean[:-1]))
        elif val_clean.endswith("h"):
            return now + timedelta(hours=int(val_clean[:-1]))
        elif val_clean.endswith("y"):
            return now + timedelta(days=int(val_clean[:-1]) * 365)
        elif val_clean.isdigit():
            return now + timedelta(days=int(val_clean))
        # Try ISO parsing
        parsed = parse_datetime(val)
        if parsed:
            if timezone.is_naive(parsed):
                parsed = timezone.make_aware(parsed)
            return parsed
    return None


class APITokenViewSet(viewsets.ViewSet):
    """Internal ViewSet to manage APITokens from the dashboard."""

    authentication_classes = [
        CsrfExemptSessionAuthentication,
        ServiceTokenAuthentication,
    ]
    permission_classes = [IsAdminOrLocal]

    def list(self, request):
        tokens = APIToken.objects.all().order_by("-created_at")
        now = timezone.now()
        return Response(
            [
                {
                    "id": t.id,
                    "name": t.name,
                    "prefix": t.prefix,
                    "scopes": t.scopes,
                    "created_at": t.created_at,
                    "expires_at": t.expires_at,
                    "is_active": getattr(t, "is_active", True),
                    "is_expired": bool(t.expires_at and now > t.expires_at),
                }
                for t in tokens
            ]
        )

    def create(self, request):
        name = request.data.get("name")
        scopes = request.data.get("scopes", [])
        expires_in = (
            request.data.get("expires_in")
            or request.data.get("expires_in_days")
            or request.data.get("expires_at")
        )

        if not name:
            return Response(
                {"error": "Name is required"}, status=status.HTTP_400_BAD_REQUEST
            )

        raw_secret = secrets.token_hex(16)
        prefix = raw_secret[:8]
        expires_at = parse_token_expiration(expires_in)

        token = APIToken.objects.create(
            name=name,
            scopes=scopes,
            prefix=prefix,
            hashed_secret=hashlib.sha256(raw_secret.encode()).hexdigest(),
            expires_at=expires_at,
            is_active=True,
        )

        actor_name = (
            str(request.user)
            if (request.user and request.user.is_authenticated)
            else "Admin"
        )
        log_audit_event(
            event_type="TOKEN_GENERATED",
            actor=actor_name,
            request=request,
            details={
                "token_id": token.id,
                "name": token.name,
                "prefix": token.prefix,
                "scopes": token.scopes,
                "expires_at": token.expires_at.isoformat()
                if token.expires_at
                else None,
            },
            severity="INFO",
            status_code=201,
        )

        # We only return the raw token once!
        return Response(
            {
                "id": token.id,
                "name": token.name,
                "prefix": token.prefix,
                "scopes": token.scopes,
                "token": f"{prefix}_{raw_secret}",
                "expires_at": token.expires_at,
            },
            status=status.HTTP_201_CREATED,
        )

    def destroy(self, request, pk=None):
        try:
            token = APIToken.objects.get(pk=pk)
            token_id = token.id
            token_name = token.name
            token_prefix = token.prefix
            token.delete()

            actor_name = (
                str(request.user)
                if (request.user and request.user.is_authenticated)
                else "Admin"
            )
            log_audit_event(
                event_type="TOKEN_REVOKED",
                actor=actor_name,
                request=request,
                details={
                    "token_id": token_id,
                    "name": token_name,
                    "prefix": token_prefix,
                },
                severity="WARNING",
                status_code=204,
            )

            return Response(status=status.HTTP_204_NO_CONTENT)
        except APIToken.DoesNotExist:
            return Response(status=status.HTTP_404_NOT_FOUND)


class AuditLogViewSet(viewsets.ViewSet):
    """Internal ViewSet to query and filter Audit Logs."""

    authentication_classes = [
        CsrfExemptSessionAuthentication,
        ServiceTokenAuthentication,
    ]
    permission_classes = [IsAdminOrLocal]
    renderer_classes = [
        JSONRenderer,
        BrowsableAPIRenderer,
        CSVRenderer,
        PlainJSONRenderer,
    ]

    def list(self, request):
        qs = AuditLog.objects.all().order_by("-timestamp")

        event_type = request.query_params.get("event_type")
        if event_type:
            qs = qs.filter(event_type=event_type)

        severity = request.query_params.get("severity")
        if severity:
            qs = qs.filter(severity__iexact=severity)

        actor = request.query_params.get("actor")
        if actor:
            qs = qs.filter(actor__icontains=actor)

        search = request.query_params.get("search")
        if search:
            qs = qs.filter(
                Q(event_type__icontains=search)
                | Q(actor__icontains=search)
                | Q(ip_address__icontains=search)
            )

        limit = min(int(request.query_params.get("limit", 100)), 500)
        logs = qs[:limit]

        return Response(
            [
                {
                    "id": log.id,
                    "timestamp": log.timestamp.isoformat(),
                    "event_type": log.event_type,
                    "actor": log.actor,
                    "ip_address": log.ip_address,
                    "user_agent": log.user_agent,
                    "status_code": log.status_code,
                    "severity": log.severity,
                    "details": log.details,
                }
                for log in logs
            ]
        )

    @action(detail=False, methods=["get"])
    def export(self, request):
        """Exports filtered Audit Logs as CSV or JSON."""
        qs = AuditLog.objects.all().order_by("-timestamp")

        event_type = request.query_params.get("event_type")
        if event_type:
            qs = qs.filter(event_type=event_type)

        severity = request.query_params.get("severity")
        if severity:
            qs = qs.filter(severity__iexact=severity)

        actor = request.query_params.get("actor")
        if actor:
            qs = qs.filter(actor__icontains=actor)

        search = request.query_params.get("search")
        if search:
            qs = qs.filter(
                Q(event_type__icontains=search)
                | Q(actor__icontains=search)
                | Q(ip_address__icontains=search)
            )

        limit = min(int(request.query_params.get("limit", 10000)), 50000)
        logs = qs[:limit]

        fmt = request.query_params.get("format", "csv").lower()
        timestamp_str = timezone.now().strftime("%Y%m%d_%H%M%S")

        if fmt == "json":
            data = [
                {
                    "id": log.id,
                    "timestamp": log.timestamp.isoformat(),
                    "event_type": log.event_type,
                    "actor": log.actor,
                    "ip_address": log.ip_address,
                    "user_agent": log.user_agent,
                    "status_code": log.status_code,
                    "severity": log.severity,
                    "details": log.details,
                }
                for log in logs
            ]
            response = HttpResponse(
                json.dumps(data, indent=2, default=str),
                content_type="application/json",
            )
            response["Content-Disposition"] = (
                f'attachment; filename="audit_logs_{timestamp_str}.json"'
            )
            return response

        # Default CSV
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(
            [
                "id",
                "timestamp",
                "severity",
                "event_type",
                "actor",
                "ip_address",
                "status_code",
                "user_agent",
                "details",
            ]
        )
        for log in logs:
            writer.writerow(
                [
                    log.id,
                    log.timestamp.isoformat(),
                    log.severity,
                    log.event_type,
                    log.actor,
                    log.ip_address or "",
                    log.status_code if log.status_code is not None else "",
                    log.user_agent or "",
                    json.dumps(log.details, default=str),
                ]
            )

        response = HttpResponse(output.getvalue(), content_type="text/csv")
        response["Content-Disposition"] = (
            f'attachment; filename="audit_logs_{timestamp_str}.csv"'
        )
        return response


@api_view(["GET"])
@authentication_classes([CsrfExemptSessionAuthentication, ServiceTokenAuthentication])
@permission_classes([IsAdminOrLocal])
def catalog_summary(request):
    """Returns all registered tables grouped by database, along with fields and row counts."""
    from django.conf import settings

    from dataman.core.models import TABLE_REGISTRY

    databases = list(getattr(settings, "DATABASES", {"default": {}}).keys())
    db_tables = {db: [] for db in databases}

    seen_models = set()
    for _name, entry in TABLE_REGISTRY.items():
        model = entry.get("model")
        if not model or model in seen_models:
            continue
        seen_models.add(model)
        db = entry.get("database", "default")
        if db not in db_tables:
            db_tables[db] = []

        fields = [
            {
                "name": f.name,
                "type": f.get_internal_type(),
                "null": f.null,
                "primary_key": f.primary_key,
            }
            for f in model._meta.fields
        ]

        count = 0
        with contextlib.suppress(Exception):
            count = model.objects.using(db).count()

        db_tables[db].append(
            {
                "model_name": model.__name__,
                "table_name": entry.get("table_name", model.__name__),
                "db_table": model._meta.db_table,
                "database": db,
                "fields": fields,
                "row_count": count,
            }
        )

    return Response(
        {
            "databases": databases,
            "tables_by_db": db_tables,
            "total_tables": len(seen_models),
        }
    )


@api_view(["GET"])
@authentication_classes([CsrfExemptSessionAuthentication, ServiceTokenAuthentication])
@permission_classes([IsAdminOrLocal])
def table_records_view(request, table_name):
    """Returns paginated rows for a registered table."""
    from dataman.core.models import TABLE_REGISTRY

    entry = TABLE_REGISTRY.get(table_name) or TABLE_REGISTRY.get(table_name.lower())
    if not entry or "model" not in entry:
        return Response(
            {"error": f"Table '{table_name}' not found"},
            status=status.HTTP_404_NOT_FOUND,
        )

    model = entry["model"]
    db = entry.get("database", "default")
    limit = min(int(request.query_params.get("limit", 50)), 200)

    try:
        qs = model.objects.using(db).all()
        total = qs.count()
        rows = list(qs.values()[:limit])
        return Response(
            {
                "table": table_name,
                "database": db,
                "total_records": total,
                "records": rows,
            }
        )
    except Exception as e:
        return Response({"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@user_passes_test(lambda u: u.is_superuser, login_url="/admin/login/")
def dashboard_view(request):
    """Serves the Prefect-inspired DataMan Console."""
    return render(request, "admin/dashboard.html")


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
    from django.db import connections

    db_checks = {}
    default_db_info = None

    for alias in connections:
        conn = connections[alias]
        db_start = time.time()
        try:
            conn.ensure_connection()
            with conn.cursor() as cursor:
                cursor.execute("SELECT 1")
                cursor.fetchone()
            db_latency = round((time.time() - db_start) * 1000, 2)
            info = {
                "status": "connected",
                "vendor": conn.vendor,
                "db_engine": conn.vendor,
                "latency_ms": db_latency,
            }
            db_checks[alias] = info
            if alias == "default" or default_db_info is None:
                default_db_info = info
        except Exception as e:
            logger.error(
                "Health check DB connection failed for alias '%s': %s", alias, e
            )
            is_healthy = False
            info = {
                "status": "disconnected",
            }
            db_checks[alias] = info
            if alias == "default" or default_db_info is None:
                default_db_info = info

    checks["databases"] = db_checks
    checks["database"] = default_db_info or {
        "status": "disconnected",
    }

    # 2. Migrations check
    total_unapplied = 0
    all_migrations = {}
    for alias in connections:
        conn = connections[alias]
        try:
            executor = MigrationExecutor(conn)
            targets = executor.loader.graph.leaf_nodes()
            unapplied = len(executor.migration_plan(targets))
            total_unapplied += unapplied
            all_migrations[alias] = {
                "status": "applied" if unapplied == 0 else "pending",
                "unapplied_count": unapplied,
            }
            if unapplied > 0:
                is_healthy = False
        except Exception as e:
            logger.error(
                "Health check migration scan failed for alias '%s': %s", alias, e
            )
            is_healthy = False
            all_migrations[alias] = {
                "status": "error",
            }

    checks["all_migrations"] = all_migrations
    checks["migrations"] = {
        "status": "applied"
        if total_unapplied == 0 and is_healthy
        else ("pending" if total_unapplied > 0 else "error"),
        "unapplied_count": total_unapplied,
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


@api_view(["POST"])
@permission_classes([permissions.IsAuthenticated])
def admin_change_password_view(request):
    """Allows logged-in admin users to update their password securely from the dashboard."""
    user = request.user
    if not user or not user.is_authenticated:
        return Response(
            {"error": "Authentication required"},
            status=status.HTTP_401_UNAUTHORIZED,
        )

    old_password = request.data.get("old_password", "")
    new_password = request.data.get("new_password", "")
    confirm_password = request.data.get("confirm_password", "")

    if not old_password or not new_password:
        return Response(
            {"error": "Current password and new password are required."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    if new_password != confirm_password:
        return Response(
            {"error": "New password and confirmation password do not match."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    if len(new_password) < 8:
        return Response(
            {"error": "New password must be at least 8 characters long."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    if not user.check_password(old_password):
        return Response(
            {"error": "Current password is incorrect."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    user.set_password(new_password)
    user.save()

    from django.contrib.auth import update_session_auth_hash

    update_session_auth_hash(request._request, user)

    log_audit_event(
        event_type="AUTH_PASSWORD_CHANGE",
        actor=user.username,
        ip_address=request.META.get("REMOTE_ADDR"),
        user_agent=request.META.get("HTTP_USER_AGENT", ""),
        status_code=200,
        severity="INFO",
        details={"user_id": user.id, "username": user.username},
    )

    return Response({"status": "success", "message": "Password changed successfully."})
