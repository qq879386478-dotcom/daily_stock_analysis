# -*- coding: utf-8 -*-
"""Minimal Extension Runtime for built-in DSA actions."""

from __future__ import annotations

import concurrent.futures
from typing import Any, Dict, Optional

from src.extensions.actions import (
    ActionContext,
    ActionDefinition,
    ActionError,
    ActionMode,
    ActionResult,
    new_run_id,
    stable_input_hash,
)
from src.extensions.permissions import ActionPermissionGuard, ActionRuntimeError
from src.extensions.registry import ExtensionRegistry
from src.extensions.tasks import ActionTaskRunner


class ExtensionRuntime:
    """Register and execute auditable DSA actions."""

    def __init__(self, *, registry=None, permission_guard=None, task_runner=None):
        self.registry: ExtensionRegistry = registry or ExtensionRegistry()
        self.permission_guard = permission_guard or ActionPermissionGuard()
        self.task_runner = task_runner or ActionTaskRunner()

    def register_action(self, action: ActionDefinition) -> None:
        self.registry.register_action(action)

    def execute_action(
        self,
        action_id: str,
        payload: Optional[Dict[str, Any]] = None,
        context: Optional[Dict[str, Any] | ActionContext] = None,
        *,
        async_mode: Optional[bool] = None,
    ) -> ActionResult:
        payload = dict(payload or {})
        context_obj = ActionContext.from_mapping(context)
        run_id = new_run_id()
        input_hash = stable_input_hash(payload)
        action = self.registry.get_action(action_id)
        if action is None:
            return self._failure(action_id, run_id, "action_not_found", "Action is not registered.", input_hash)

        run_async = async_mode if async_mode is not None else action.mode == ActionMode.ASYNC
        if run_async:
            return self._submit_async(action, payload, context_obj, run_id, input_hash)
        return self._execute_now(action, payload, context_obj, run_id, input_hash)

    def _submit_async(self, action, payload, context, run_id, input_hash):
        try:
            self.permission_guard.check(action, context)
        except ActionRuntimeError as exc:
            return self._failure(action.action_id, run_id, exc.code, exc.message, input_hash, exc.details)

        task = self.task_runner.submit(
            action=action,
            payload=payload,
            context=context,
            run_id=run_id,
            run_callable=lambda: self._execute_now(action, payload, context, run_id, input_hash).to_dict(),
        )
        return ActionResult(
            action.action_id,
            run_id,
            True,
            "accepted",
            task_id=task.task_id,
            input_hash=input_hash,
            metadata={"task_type": "plugin", "caller": context.caller},
        )

    def _execute_now(self, action, payload, context, run_id, input_hash):
        try:
            self.permission_guard.check(action, context)
            result = self._call_with_timeout(action, payload, context)
        except ActionRuntimeError as exc:
            return self._failure(action.action_id, run_id, exc.code, exc.message, input_hash, exc.details)
        except concurrent.futures.TimeoutError:
            return self._failure(
                action.action_id,
                run_id,
                "timeout",
                "Action execution timed out.",
                input_hash,
                {"timeout_seconds": self._timeout_seconds(action, context)},
            )
        except Exception as exc:
            return self._failure(
                action.action_id,
                run_id,
                "handler_error",
                "Action handler failed.",
                input_hash,
                {"exception_type": exc.__class__.__name__},
            )

        if isinstance(result, ActionResult):
            return result
        if not isinstance(result, dict):
            result = {"value": result}
        return ActionResult(
            action.action_id,
            run_id,
            True,
            "completed",
            data=result,
            input_hash=input_hash,
            metadata={"caller": context.caller},
        )

    def _call_with_timeout(self, action, payload, context):
        timeout_seconds = self._timeout_seconds(action, context)
        if timeout_seconds <= 0:
            return action.handler(payload, context)
        executor = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="extension_action_")
        try:
            return executor.submit(action.handler, payload, context).result(timeout=timeout_seconds)
        finally:
            executor.shutdown(wait=False, cancel_futures=True)

    @staticmethod
    def _timeout_seconds(action, context):
        budget_timeout = context.budget.timeout_seconds
        return action.timeout_seconds if budget_timeout <= 0 else min(action.timeout_seconds, budget_timeout)

    @staticmethod
    def _failure(action_id, run_id, code, message, input_hash, details=None):
        return ActionResult(action_id, run_id, False, "failed", error=ActionError(code, message, details or {}), input_hash=input_hash)
