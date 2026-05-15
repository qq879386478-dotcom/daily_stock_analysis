# -*- coding: utf-8 -*-
"""Tests for the DSA Extension Runtime MVP."""

from __future__ import annotations

import time
import unittest
from types import SimpleNamespace

from src.extensions import (
    ActionContext,
    ActionDefinition,
    ActionMode,
    ExtensionRegistry,
    ExtensionRuntime,
    create_builtin_extension_runtime,
)
from src.extensions.permissions import ActionPermissionGuard


def _echo(payload, context):
    return {"payload": payload, "caller": context.caller, "dry_run": context.dry_run}


class StubTaskRunner:
    def __init__(self):
        self.calls = []

    def submit(self, *, action, payload, context, run_id, run_callable):
        self.calls.append({"action_id": action.action_id, "payload": payload, "caller": context.caller})
        return SimpleNamespace(task_id="task_123")


class ExtensionRuntimeTestCase(unittest.TestCase):
    def _runtime(self, action=None, *, guard=None, task_runner=None):
        registry = ExtensionRegistry()
        registry.register_action(
            action
            or ActionDefinition("test.echo", "test", "Echo payload", _echo, timeout_seconds=1)
        )
        return ExtensionRuntime(registry=registry, permission_guard=guard, task_runner=task_runner)

    def test_action_context_contract_fields(self):
        context = ActionContext.from_mapping(
            {
                "caller": "agent",
                "trace_id": "trace_test",
                "session_id": "session_test",
                "idempotency_key": "dedupe",
                "dry_run": True,
                "budget": {"timeout_seconds": 5, "max_llm_calls": 2, "max_items": 7},
                "context": {"market": "cn"},
                "requires_confirmation": True,
            }
        ).to_dict()

        self.assertEqual(context["caller"], "agent")
        self.assertEqual(context["trace_id"], "trace_test")
        self.assertEqual(context["session_id"], "session_test")
        self.assertEqual(context["idempotency_key"], "dedupe")
        self.assertTrue(context["dry_run"])
        self.assertEqual(context["budget"], {"timeout_seconds": 5.0, "max_llm_calls": 2, "max_items": 7})
        self.assertEqual(context["context"], {"market": "cn"})
        self.assertTrue(context["requires_confirmation"])

    def test_execute_sync_action_success_and_hashes_input(self):
        result = self._runtime().execute_action(
            "test.echo",
            {"symbol": "600519"},
            {"caller": "web", "dry_run": True},
            async_mode=False,
        )

        self.assertTrue(result.ok)
        self.assertEqual(result.status, "completed")
        self.assertEqual(result.data["payload"], {"symbol": "600519"})
        self.assertEqual(result.data["caller"], "web")
        self.assertEqual(len(result.input_hash), 64)

    def test_structured_errors_for_missing_permission_depth_timeout_and_handler(self):
        missing = ExtensionRuntime().execute_action("missing.action")
        denied = self._runtime(guard=ActionPermissionGuard(allowed_actions={"other.action"})).execute_action("test.echo")
        depth = self._runtime(guard=ActionPermissionGuard(max_call_depth=1)).execute_action(
            "test.echo", context={"call_depth": 2}
        )

        def slow(payload, context):
            time.sleep(0.2)
            return {"done": True}

        timeout = self._runtime(ActionDefinition("test.slow", "test", "Slow", slow, timeout_seconds=0.01)).execute_action(
            "test.slow"
        )

        def bad(payload, context):
            raise RuntimeError("secret token should not be returned")

        handler = self._runtime(ActionDefinition("test.bad", "test", "Bad", bad)).execute_action("test.bad")

        self.assertEqual(missing.error.code, "action_not_found")
        self.assertEqual(denied.error.code, "permission_denied")
        self.assertEqual(depth.error.code, "call_depth_exceeded")
        self.assertEqual(timeout.error.code, "timeout")
        self.assertEqual(handler.error.code, "handler_error")
        self.assertNotIn("secret token", handler.error.message)

    def test_async_action_submits_to_task_runner(self):
        task_runner = StubTaskRunner()
        runtime = self._runtime(
            ActionDefinition("test.async_echo", "test", "Async echo", _echo, mode=ActionMode.ASYNC, subject_key="symbol"),
            task_runner=task_runner,
        )

        result = runtime.execute_action("test.async_echo", {"symbol": "600519"}, {"caller": "agent"})

        self.assertTrue(result.ok)
        self.assertEqual(result.status, "accepted")
        self.assertEqual(result.task_id, "task_123")
        self.assertEqual(task_runner.calls[0], {"action_id": "test.async_echo", "payload": {"symbol": "600519"}, "caller": "agent"})

    def test_builtin_runtime_registers_core_actions(self):
        runtime = create_builtin_extension_runtime()
        action_ids = {action.action_id for action in runtime.registry.list_actions()}
        self.assertEqual({"dsa.analyze_stock", "notification.send", "stock_pool.import"} - action_ids, set())

        dry_run = runtime.execute_action("notification.send", {"channel": "test"}, {"dry_run": True})
        blocked = runtime.execute_action("notification.send", {"channel": "test"})
        confirmed = runtime.execute_action("notification.send", {"channel": "test"}, {"requires_confirmation": True})

        self.assertTrue(dry_run.ok)
        self.assertEqual(dry_run.data["status"], "validated")
        self.assertEqual(blocked.error.code, "confirmation_required")
        self.assertTrue(confirmed.ok)


if __name__ == "__main__":
    unittest.main()
