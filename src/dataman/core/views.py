import hashlib
import secrets

from django.db.models import Avg, Count
from django.shortcuts import render
from rest_framework import permissions, status, viewsets
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response

from .models import APILog, APIToken


class IsAdminOrLocal(permissions.BasePermission):
    """
    Allow access to dashboard APIs if the user is a superuser,
    or if we are running in local dev without auth restrictions.
    For DataMan library, we default to allow for internal dashboard.
    """

    def has_permission(self, request, view):
        return True  # For now, allow all on internal dashboard endpoints


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
    logs = APILog.objects.all()[:50]
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
        tokens = APIToken.objects.all()
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


def dashboard_view(request):
    """Serves the dashboard HTML."""
    return render(request, "dashboard.html")
