# -*- coding: utf-8 -*-
"""Permission and risk guardrails for Extension Runtime actions."""

from __future__ import annotations

from typing import Iterable, Optional, Set

from src.extensions.actions import ActionContext, ActionDefinition


class ActionRuntimeError(Exception):
    def __init__(self, code: str, message: str, **details):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details


class ActionPermissionGuard:
    def __init__(self, *, allowed_actions: Optional[Iterable[str]] = None, max_call_depth: int = 3):
        self.allowed_actions: Optional[Set[str]] = set(allowed_actions) if allowed_actions is not None else None
        self.max_call_depth = max_call_depth

    def check(self, action: ActionDefinition, context: ActionContext) -> None:
        if self.allowed_actions is not None and action.action_id not in self.allowed_actions:
            raise ActionRuntimeError("permission_denied", "Action is not allowed for this runtime.", action_id=action.action_id)
        if context.call_depth > self.max_call_depth:
            raise ActionRuntimeError(
                "call_depth_exceeded",
                "Action call depth exceeded runtime guard.",
                action_id=action.action_id,
                call_depth=context.call_depth,
                max_call_depth=self.max_call_depth,
            )
        if action.requires_confirmation and not context.dry_run and not context.requires_confirmation:
            raise ActionRuntimeError("confirmation_required", "Action requires explicit confirmation.", action_id=action.action_id)
