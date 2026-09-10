from django.contrib.auth.signals import (
    user_logged_in,
    user_logged_out,
    user_login_failed,
)
from django.dispatch import receiver

from .audit import log_audit_event


@receiver(user_logged_in)
def audit_user_logged_in(sender, request, user, **kwargs):
    """Audit successful user login."""
    log_audit_event(
        event_type="ADMIN_LOGIN_SUCCESS",
        actor=getattr(user, "username", str(user)),
        request=request,
        details={
            "email": getattr(user, "email", ""),
            "is_superuser": getattr(user, "is_superuser", False),
        },
        severity="INFO",
        status_code=200,
    )


@receiver(user_login_failed)
def audit_user_login_failed(sender, credentials, request, **kwargs):
    """Audit failed login attempt."""
    username = (
        credentials.get("username", "<unknown>")
        if isinstance(credentials, dict)
        else "<unknown>"
    )
    log_audit_event(
        event_type="ADMIN_LOGIN_FAILED",
        actor=username,
        request=request,
        details={"attempted_username": username},
        severity="WARNING",
        status_code=401,
    )


@receiver(user_logged_out)
def audit_user_logged_out(sender, request, user, **kwargs):
    """Audit user logout."""
    if user:
        log_audit_event(
            event_type="ADMIN_LOGOUT",
            actor=getattr(user, "username", str(user)),
            request=request,
            details={"email": getattr(user, "email", "")},
            severity="INFO",
            status_code=200,
        )
