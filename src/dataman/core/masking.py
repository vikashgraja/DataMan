from collections.abc import Callable
from typing import Any


def mask_partial(val: Any, keep_last: int = 4, mask_char: str = "*") -> str:
    """
    Masks all alphanumeric characters except the last `keep_last` characters,
    preserving formatting characters such as hyphens and spaces.
    Example: '123-45-6789' -> '***-**-6789'
    """
    if val is None:
        return None
    s = str(val)
    if not s:
        return s

    # Find indices of alphanumeric characters
    alnum_indices = [i for i, c in enumerate(s) if c.isalnum()]
    if len(alnum_indices) <= keep_last:
        return mask_char * len(s)

    mask_set = set(alnum_indices[:-keep_last])
    return "".join(mask_char if i in mask_set else c for i, c in enumerate(s))


def mask_last4(val: Any, mask_char: str = "*") -> str:
    """
    Masks payment cards / identifiers, retaining only the last 4 digits.
    Example: '4111-2222-3333-4444' -> '****-****-****-4444'
    """
    return mask_partial(val, keep_last=4, mask_char=mask_char)


def mask_email(val: Any, mask_char: str = "*") -> str:
    """
    Masks email addresses while retaining the domain and outer username boundaries.
    Example: 'vikash@example.com' -> 'v***h@example.com'
    """
    if val is None:
        return None
    s = str(val).strip()
    if "@" not in s:
        return mask_partial(s, keep_last=2, mask_char=mask_char)

    parts = s.split("@", 1)
    username, domain = parts[0], parts[1]

    if len(username) <= 1:
        masked_user = mask_char * 3
    elif len(username) == 2:
        masked_user = f"{username[0]}{mask_char * 3}"
    elif len(username) <= 4:
        masked_user = f"{username[0]}{mask_char * 3}{username[-1]}"
    else:
        masked_user = f"{username[0]}{mask_char * (len(username) - 2)}{username[-1]}"

    return f"{masked_user}@{domain}"


def mask_phone(val: Any, mask_char: str = "*") -> str:
    """
    Masks telephone numbers, keeping the last 4 digits and country prefix if present.
    Example: '+1 (555) 234-5678' -> '+1 (***) ***-5678'
    """
    if val is None:
        return None
    s = str(val)
    return mask_partial(s, keep_last=4, mask_char=mask_char)


def mask_full(val: Any, mask_char: str = "*") -> str:
    """
    Completely masks the value with asterisks.
    Example: 'sensitive_secret' -> '********'
    """
    if val is None:
        return None
    return mask_char * 8


STRATEGY_MAP: dict[str, Callable[[Any], str]] = {
    "partial": mask_partial,
    "ssn": mask_partial,
    "last4": mask_last4,
    "card": mask_last4,
    "credit_card": mask_last4,
    "email": mask_email,
    "email_mask": mask_email,
    "phone": mask_phone,
    "full": mask_full,
    "redact": mask_full,
}


def mask_value(val: Any, strategy: str | Callable[[Any], str] = "partial") -> Any:
    """
    Applies the requested masking strategy to a value.
    """
    if val is None or val == "":
        return val

    if callable(strategy):
        return strategy(val)

    strategy_key = str(strategy).lower().strip()
    mask_fn = STRATEGY_MAP.get(strategy_key, mask_partial)
    return mask_fn(val)
