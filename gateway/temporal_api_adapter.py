"""
Temporal adapter for the Hermes API server.

Converts API server requests (/v1/responses, /v1/chat/completions)
into Temporal workflow submissions. Each API request becomes a durable
workflow that survives process death.

Usage in gateway/platforms/api_server.py:
    from gateway.temporal_api_adapter import (
        temporal_api_submit, is_temporal_api_enabled
    )

    if is_temporal_api_enabled():
        result = await temporal_api_submit(request_data)
    else:
        # existing direct execution path

Enable via config.yaml:
    api_server:
      temporal_backend: true

Or environment variable:
    HERMES_TEMPORAL_API=1
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


def is_temporal_api_enabled() -> bool:
    """Check if Temporal API backend is enabled."""
    global _enabled
    if _enabled is not None:
        return _enabled

    if os.environ.get("HERMES_TEMPORAL_API", "").lower() in ("1", "true", "yes"):
        _enabled = True
        return True

    try:
        import yaml
        from hermes_cli.config import get_config_path
        config_path = get_config_path()
        if os.path.exists(config_path):
            with open(config_path) as f:
                cfg = yaml.safe_load(f) or {}
            api_cfg = cfg.get("api_server", {})
            if isinstance(api_cfg, dict) and api_cfg.get("temporal_backend"):
                _enabled = True
                return True
    except Exception:
        pass

    _enabled = False
    return False


async def _get_client():
    global _client
    if _client is None:
        from temporalio.client import Client
        _client = await Client.connect(TEMPORAL_ADDRESS, namespace=NAMESPACE)
    return _client


async def temporal_api_submit(
    messages: list,
    model: str = "",
    session_id: str = "",
    timeout_seconds: int = 900,
    metadata: dict = None,
) -> Dict[str, Any]:
    """Submit an API request as a Temporal workflow.

    Converts /v1/chat/completions or /v1/responses request into a
    HermesConversationWorkflow submission.

    Args:
        messages: OpenAI-format message list
        model: model name (informational)
        session_id: optional session ID for continuity
        timeout_seconds: max wait time
        metadata: additional metadata

    Returns:
        OpenAI-compatible response dict
    """
    client = await _get_client()

    # Extract the latest user message
    user_message = ""
    conversation_history = []
    for msg in messages:
        if msg.get("role") == "user":
            user_message = msg.get("content", "")
        conversation_history.append(msg)

    if not user_message:
        return {
            "error": {"message": "No user message found in messages array"},
            "type": "invalid_request_error",
        }

    input_json = json.dumps({
        "user_message": user_message,
        "conversation_history": conversation_history[:-1],  # exclude last user msg
        "task_id": session_id or str(uuid.uuid4()),
        "metadata": {
            "source": "api_server",
            "model": model,
            **(metadata or {}),
        },
    })

    workflow_id = f"api-{session_id or uuid.uuid4().hex[:8]}"

    try:
        handle = await client.start_workflow(
            "HermesConversationWorkflow",
            input_json,
            id=workflow_id,
            task_queue=TASK_QUEUE,
        )

        result_json = await asyncio.wait_for(
            handle.result(),
            timeout=timeout_seconds,
        )

        result = json.loads(result_json) if isinstance(result_json, str) else result_json

        # Map to OpenAI response format
        return {
            "id": f"chatcmpl-{workflow_id}",
            "object": "chat.completion",
            "model": model or "hermes-temporal",
            "choices": [{
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": result.get("final_response", ""),
                },
                "finish_reason": "stop" if not result.get("error") else "error",
            }],
            "usage": {
                "total_tokens": result.get("total_tokens", 0),
            },
            "_temporal": {
                "workflow_id": workflow_id,
                "duration_seconds": result.get("duration_seconds"),
                "tool_calls": result.get("tool_calls_count", 0),
            },
        }

    except asyncio.TimeoutError:
        return {
            "error": {"message": f"Workflow timed out after {timeout_seconds}s"},
            "type": "timeout_error",
            "_temporal": {"workflow_id": workflow_id},
        }
    except Exception as e:
        return {
            "error": {"message": str(e)},
            "type": "server_error",
            "_temporal": {"workflow_id": workflow_id},
        }
