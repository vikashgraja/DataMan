import contextlib
import json
import logging
from typing import Any

audit_logger = logging.getLogger("dataman.audit")

SENSITIVE_EXACT_KEYS = {
    "password",
    "passwd",
    "secret",
    "token",
    "auth",
    "authorization",
    "credit_card",
    "api_key",
    "access_token",
    "refresh_token",
    "hash",
    "hashed_secret",
    "private_key",
    "secret_key",
    "cvv",
    "cvv2",
    "pin",
    "ssn",
    "social_security",
    "card_number",
    "passcode",
}

SAFE_KEY_SUFFIXES = (
    "_id",
    "_name",
    "_prefix",
    "_type",
    "_count",
    "_scopes",
    "_status",
    "_at",
)


def is_sensitive_key(key: str) -> bool:
    """Checks if a field key corresponds to a secret credential or sensitive PII."""
    k_lower = key.lower()
    if any(k_lower.endswith(suffix) for suffix in SAFE_KEY_SUFFIXES):
        return False
    if k_lower in SENSITIVE_EXACT_KEYS:
        return True
    if any(
        s_key in k_lower
        for s_key in ("password", "secret", "credit_card", "api_key", "authorization")
    ):
        return True
    return k_lower.endswith(("_token", "_secret", "_key"))


def sanitize_payload(data: Any) -> Any:
    """Recursively masks sensitive keys in payloads and dictionaries."""
    if isinstance(data, dict):
        sanitized = {}
        for k, v in data.items():
            if is_sensitive_key(k):
                sanitized[k] = "********"
            else:
                sanitized[k] = sanitize_payload(v)
        return sanitized
    elif isinstance(data, list):
        return [sanitize_payload(item) for item in data]
    return data


def get_client_ip(request: Any) -> str | None:
    """Resolves client IP address taking proxies and load balancers into account."""
    if not request:
        return None

    # Check Cloudflare
    cf_ip = request.META.get("HTTP_CF_CONNECTING_IP")
    if cf_ip:
        return cf_ip.strip()

    # Check X-Forwarded-For
    x_forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR")
    if x_forwarded_for:
        return x_forwarded_for.split(",")[0].strip()

    # Check X-Real-IP
    x_real_ip = request.META.get("HTTP_X_REAL_IP")
    if x_real_ip:
        return x_real_ip.strip()

    return request.META.get("REMOTE_ADDR")


def get_user_agent(request: Any) -> str:
    """Extracts User-Agent string from request headers."""
    if not request:
        return ""
    return request.META.get("HTTP_USER_AGENT", "")[:500]


def log_audit_event(
    event_type: str,
    actor: str | None = None,
    ip_address: str | None = None,
    user_agent: str | None = None,
    details: dict | None = None,
    severity: str = "INFO",
    status_code: int | None = None,
    request: Any = None,
) -> Any | None:
    """
    Enterprise Audit Logging function:
    1. Extracts actor, IP, and User-Agent from request if available.
    2. Redacts sensitive PII keys.
    3. Persists record in database AuditLog model.
    4. Emits structured JSON log via logging.getLogger('dataman.audit').
    """
    if request:
        if not ip_address:
            ip_address = get_client_ip(request)
        if not user_agent:
            user_agent = get_user_agent(request)
        if not actor:
            if (
                hasattr(request, "auth")
                and request.auth
                and hasattr(request.auth, "prefix")
            ):
                actor = f"Token:{request.auth.prefix}"
            else:
                user = getattr(request, "user", None)
                if user and getattr(user, "is_authenticated", False):
                    actor = str(user)

    actor = actor or "System"
    sanitized_details = sanitize_payload(details or {})

    # 1. Structured log stream
    log_payload = {
        "event_type": event_type,
        "actor": actor,
        "ip_address": ip_address,
        "user_agent": user_agent,
        "status_code": status_code,
        "severity": severity,
        "details": sanitized_details,
    }

    log_level = getattr(logging, severity.upper(), logging.INFO)
    with contextlib.suppress(Exception):
        audit_logger.log(log_level, json.dumps(log_payload, default=str))

    # 2. Database model persistence
    try:
        from dataman.core.models import AuditLog

        return AuditLog.objects.create(
            event_type=event_type,
            actor=actor,
            ip_address=ip_address,
            user_agent=user_agent or "",
            status_code=status_code,
            severity=severity.upper(),
            details=sanitized_details,
        )
    except Exception as e:
        audit_logger.error(f"Failed to persist AuditLog to DB: {e}")
        return None
