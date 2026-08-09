from rest_framework import permissions


class HasTableScope(permissions.BasePermission):
    """
    Ensures that the incoming request token has the required scope
    to access this table and action.
    """

    def has_permission(self, request, view):
        # Allow superusers (admin panel logins) to do anything
        if getattr(request.user, "is_superuser", False):
            return True

        if not hasattr(request, "auth") or not request.auth:
            return False

        token = request.auth
        if not hasattr(token, "scopes"):
            return False

        if "*" in token.scopes:
            return True

        table_name = getattr(view, "basename", "").lower()
        method = request.method.lower()

        action_map = {
            "get": "read",
            "head": "read",
            "options": "read",
            "post": "write",
            "put": "write",
            "patch": "write",
            "delete": "write",
        }
        action = action_map.get(method, "read")

        required_scope = f"{table_name}:{action}"
        return required_scope in token.scopes
