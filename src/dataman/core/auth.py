from rest_framework import authentication, exceptions

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

        try:
            prefix, secret = auth[1].split("_", 1)
            token = APIToken.objects.get(prefix=prefix)

            import hashlib

            hashed_input = hashlib.sha256(secret.encode()).hexdigest()
            if hashed_input != token.hashed_secret:
                raise exceptions.AuthenticationFailed("Invalid token.")
        except (ValueError, APIToken.DoesNotExist) as e:
            raise exceptions.AuthenticationFailed("Invalid token.") from e

        return (ServiceUser(), token)
