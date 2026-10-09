from rest_framework import permissions

from dataman.core.audit import log_audit_event


class HasTableScope(permissions.BasePermission):
    """
    Ensures that the incoming request token has the required scope
    to access this table and action.
    """

    def has_permission(self, request, view):
        # Allow superusers (admin panel logins) to do anything
        if getattr(request.user, "is_superuser", False):
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

        if not hasattr(request, "auth") or not request.auth:
            log_audit_event(
                event_type="PERMISSION_DENIED",
                request=request,
                details={
                    "table": table_name,
                    "action": action,
                    "required_scope": required_scope,
                    "reason": "No authentication token provided",
                },
                severity="WARNING",
                status_code=403,
            )
            return False

        token = request.auth
        if not hasattr(token, "scopes"):
            log_audit_event(
                event_type="PERMISSION_DENIED",
                actor=f"Token:{getattr(token, 'prefix', 'unknown')}",
                request=request,
                details={
                    "table": table_name,
                    "action": action,
                    "required_scope": required_scope,
                    "reason": "Token object missing scopes",
                },
                severity="WARNING",
                status_code=403,
            )
            return False

        from dataman.core.models import TABLE_REGISTRY

        if "-" in table_name:
            db_name, clean_table = table_name.split("-", 1)
        elif ":" in table_name:
            db_name, clean_table = table_name.split(":", 1)
        else:
            db_name = "default"
            clean_table = table_name

        required_scope = f"{db_name}:{clean_table}:{action}"
        possible_scopes = {
            required_scope,
            f"{db_name}:{clean_table}:*",
            f"{db_name}-{clean_table}:{action}",
            f"{db_name}-{clean_table}:*",
            f"{db_name}:{action}",
            f"{db_name}:*",
        }

        # Allow unqualified scopes ONLY when the table/model is unique across all databases
        matching_dbs = {
            k[0]
            for k in TABLE_REGISTRY
            if isinstance(k, tuple) and len(k) == 2 and k[1] == clean_table.lower()
        }
        if len(matching_dbs) <= 1:
            possible_scopes.add(f"{clean_table}:{action}")
            possible_scopes.add(f"{clean_table}:*")

        if "*" in token.scopes:
            return True

        has_scope = any(s in token.scopes for s in possible_scopes)
        if not has_scope:
            log_audit_event(
                event_type="PERMISSION_DENIED",
                actor=f"Token:{getattr(token, 'prefix', 'unknown')}",
                request=request,
                details={
                    "table": table_name,
                    "action": action,
                    "required_scope": required_scope,
                    "token_scopes": token.scopes,
                    "reason": "Missing required scope",
                },
                severity="WARNING",
                status_code=403,
            )
        return has_scope
