"""
Temporal dispatch adapter for the Hermes gateway.

Provides an async function that submits agent conversations as Temporal
workflows instead of running them directly via run_in_executor. This
makes every gateway message a durable, retryable operation.

Usage in gateway/run.py:
    from gateway.temporal_dispatch import temporal_run_conversation, is_temporal_enabled

    if is_temporal_enabled():
        result = await temporal_run_conversation(prompt, task_id, config_path, env_path)
    else:
        result = await loop.run_in_executor(None, run_sync)

Enable via config.yaml:
    gateway:
      temporal_dispatch: true

Or environment variable:
    HERMES_TEMPORAL_DISPATCH=1
"""

import asyncio
import json
import logging
import os
import uuid
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

TEMPORAL_ADDRESS = os.environ.get("TEMPORAL_ADDRESS", "localhost:7233")
NAMESPACE = os.environ.get("TEMPORAL_NAMESPACE", "novamesh")
TASK_QUEUE = os.environ.get("TEMPORAL_TASK_QUEUE", "novamesh")

_client = None
_enabled: Optional[bool] = None


def is_temporal_enabled() -> bool:
    """Check if Temporal dispatch is enabled."""
    global _enabled
    if _enabled is not None:
        return _enabled

    # Check environment variable
    if os.environ.get("HERMES_TEMPORAL_DISPATCH", "").lower() in ("1", "true", "yes"):
        _enabled = True
        return True

    # Check config.yaml
    try:
        import yaml
        from hermes_cli.config import get_config_path
        config_path = get_config_path()
        if os.path.exists(config_path):
            with open(config_path) as f:
                cfg = yaml.safe_load(f) or {}
            gw_cfg = cfg.get("gateway", {})
            if isinstance(gw_cfg, dict) and gw_cfg.get("temporal_dispatch"):
                _enabled = True
                return True
    except Exception:
        pass

    _enabled = False
    return False


async def _get_client():
    """Lazy-init Temporal client."""
    global _client
    if _client is None:
        from temporalio.client import Client
        _client = await Client.connect(TEMPORAL_ADDRESS, namespace=NAMESPACE)
    return _client


async def temporal_run_conversation(
    user_message: str,
    task_id: str,
    config_path: str = "",
    env_path: str = "",
    profile: str = "default",
    system_message: str = None,
    conversation_history: list = None,
    metadata: dict = None,
    timeout_seconds: int = 900,
) -> Dict[str, Any]:
    """Submit a conversation as a Temporal workflow and wait for result.

    This replaces loop.run_in_executor(None, run_sync) in the gateway.
    The conversation runs as a durable Temporal activity with heartbeats,
    retry policy, and survives worker process death.

    Returns the same dict shape as AIAgent.run_conversation().
    """
    client = await _get_client()

    input_json = json.dumps({
        "user_message": user_message,
        "config_path": config_path or os.path.expanduser("~/.hermes/config.yaml"),
        "env_path": env_path or os.path.expanduser("~/.hermes/.env"),
        "profile": profile,
        "system_message": system_message,
        "conversation_history": conversation_history,
        "task_id": task_id,
        "metadata": metadata or {},
    })

    workflow_id = f"gw-conv-{task_id or uuid.uuid4().hex[:8]}"

    try:
        handle = await client.start_workflow(
            "HermesConversationWorkflow",
            input_json,
            id=workflow_id,
            task_queue=TASK_QUEUE,
        )

        logger.info(f"temporal_dispatch: submitted {workflow_id} for task {task_id}")

        # Wait for result with timeout
        result_json = await asyncio.wait_for(
            handle.result(),
            timeout=timeout_seconds,
        )

        result = json.loads(result_json) if isinstance(result_json, str) else result_json

        # Map back to the expected gateway result format
        return {
            "final_response": result.get("final_response", ""),
            "messages": result.get("conversation_history", []),
            "api_calls": result.get("tool_calls_count", 0),
            "completed": result.get("error") is None,
            "error": result.get("error"),
            "_temporal_workflow_id": workflow_id,
            "_temporal_duration": result.get("duration_seconds"),
        }

    except asyncio.TimeoutError:
        logger.warning(f"temporal_dispatch: {workflow_id} timed out after {timeout_seconds}s")
        return {
            "final_response": "",
            "messages": [],
            "completed": False,
            "error": f"Temporal workflow timed out after {timeout_seconds}s",
            "_temporal_workflow_id": workflow_id,
        }
    except Exception as e:
        logger.error(f"temporal_dispatch: {workflow_id} failed: {e}")
        # Fall through to direct execution? No — if Temporal is enabled,
        # we committed to durable execution. Return the error.
        return {
            "final_response": "",
            "messages": [],
            "completed": False,
            "error": f"Temporal dispatch failed: {e}",
            "_temporal_workflow_id": workflow_id,
        }
