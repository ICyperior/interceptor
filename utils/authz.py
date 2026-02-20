"""Authorization helpers for role-based and arming-gated operations."""

from __future__ import annotations

from functools import wraps
from typing import Any, Callable

from flask import jsonify, session

ROLE_LEVELS: dict[str, int] = {
    'viewer': 10,
    'analyst': 20,
    'operator': 30,
    'supervisor': 40,
    'admin': 50,
}


def current_username() -> str:
    """Get current username from session."""
    return str(session.get('username') or 'anonymous')


def current_role() -> str:
    """Get current role from session with safe default."""
    role = str(session.get('role') or 'viewer').strip().lower()
    return role if role in ROLE_LEVELS else 'viewer'


def has_role(required_role: str) -> bool:
    """Return True if current session role satisfies required role."""
    required = ROLE_LEVELS.get(required_role, ROLE_LEVELS['admin'])
    actual = ROLE_LEVELS.get(current_role(), ROLE_LEVELS['viewer'])
    return actual >= required


def require_role(required_role: str) -> Callable:
    """Decorator enforcing minimum role."""

    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args: Any, **kwargs: Any):
            if not has_role(required_role):
                return jsonify({
                    'status': 'error',
                    'message': f'{required_role} role required',
                    'required_role': required_role,
                    'current_role': current_role(),
                }), 403
            return func(*args, **kwargs)

        return wrapper

    return decorator


def require_armed(func: Callable) -> Callable:
    """Decorator enforcing armed state for active actions."""

    @wraps(func)
    def wrapper(*args: Any, **kwargs: Any):
        from utils.drone import get_drone_ops_service

        service = get_drone_ops_service()
        policy = service.get_policy_state()
        if not policy.get('armed'):
            return jsonify({
                'status': 'error',
                'message': 'Action plane is not armed',
                'policy': policy,
            }), 403
        return func(*args, **kwargs)

    return wrapper
