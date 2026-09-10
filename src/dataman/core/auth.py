import hashlib
from django.utils import timezone
from rest_framework import authentication, exceptions

from dataman.core.audit import log_audit_event
from dataman.core.models import APIToken


class ServiceUser:
    """A dummy user class to represent a Service Token."""

    is_authenticated = True
    is_active = True
    is_staff = False
    is_superuser = False

    def __str__(self):
        return "ServiceTokenUser"


class ServiceTokenAuthentication(authentication.BaseAuthentication):
    """Authenticates requests using the dataman APIToken model."""

    def authenticate(self, request):
        auth = request.headers.get("Authorization", "").split()

        if not auth or auth[0].lower() != "token":
            return None

        if len(auth) == 1 or len(auth) > 2:
            raise exceptions.AuthenticationFailed(
                "Invalid token header. No credentials provided."
            )

        prefix = ""
        try:
            prefix, secret = auth[1].split("_", 1)
            token = APIToken.objects.get(prefix=prefix)

            hashed_input = hashlib.sha256(secret.encode()).hexdigest()
            if hashed_input != token.hashed_secret:
                log_audit_event(
                    event_type="TOKEN_AUTH_FAILED",
                    actor=f"Token:{prefix}",
                    request=request,
                    details={"prefix": prefix, "reason": "Secret hash mismatch"},
                    severity="WARNING",
                    status_code=401,
                )
                raise exceptions.AuthenticationFailed("Invalid token.")

            # Check if token is active
            if not getattr(token, "is_active", True):
                log_audit_event(
                    event_type="TOKEN_AUTH_FAILED",
                    actor=f"Token:{prefix}",
                    request=request,
                    details={"prefix": prefix, "token_name": token.name, "reason": "Token is disabled"},
                    severity="WARNING",
                    status_code=401,
                )
                raise exceptions.AuthenticationFailed("Token is inactive.")

            # Check if token has expired
            if token.expires_at and timezone.now() > token.expires_at:
                log_audit_event(
                    event_type="TOKEN_EXPIRED",
                    actor=f"Token:{prefix}",
                    request=request,
                    details={
                        "prefix": prefix,
                        "token_name": token.name,
                        "expired_at": token.expires_at.isoformat(),
                    },
                    severity="WARNING",
                    status_code=401,
                )
                raise exceptions.AuthenticationFailed("Token has expired.")

        except (ValueError, APIToken.DoesNotExist) as e:
            log_audit_event(
                event_type="TOKEN_AUTH_FAILED",
                actor=f"Token:{prefix}" if prefix else "Unknown",
                request=request,
                details={"prefix": prefix, "reason": "Token prefix not found or invalid format"},
                severity="WARNING",
                status_code=401,
            )
            raise exceptions.AuthenticationFailed("Invalid token.") from e

        return (ServiceUser(), token)


class CsrfExemptSessionAuthentication(authentication.SessionAuthentication):
    """SessionAuthentication that does not enforce CSRF checks for internal dashboard APIs."""

    def enforce_csrf(self, request):
        return  # Skip CSRF check for authenticated dashboard session


