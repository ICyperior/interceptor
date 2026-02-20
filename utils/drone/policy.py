"""Drone Ops policy helpers."""

from __future__ import annotations


def required_approvals_for_action(action_type: str) -> int:
    """Return required approvals for a given action type."""
    action = (action_type or '').strip().lower()
    if action.startswith('passive_'):
        return 1
    return 2
