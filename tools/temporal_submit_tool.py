"""
NovaMesh Temporal workflow submission tool.

Lets any Hermes agent submit, query, and manage Temporal workflows from chat.
Supports all NovaMesh workflows: MultiAgentDispatch, DAGDispatch,
AgentWorkLoop, FleetHealthCheck. Also queries the FleetSemaphore.

Registered as the `temporal_submit` tool in the `temporal` toolset.
"""

import asyncio
import json
import logging
import uuid
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

TEMPORAL_ADDRESS = "localhost:7233"
NAMESPACE = "novamesh"
TASK_QUEUE = "novamesh"

# Company name → ID lookup
COMPANY_IDS = {
    "teamadapt": "92d5b79c-ca3c-4d30-b730-15ae0b00227c",
    "agency": "79fc9cb0-1362-4ae8-84b5-77685ad897fe",
    "novamesh": "59a2f84f-8522-4a79-ad25-d5cc3b864c60",
    "blitzkernels": "f5a1c989-c74b-4d61-9707-e37aa795b902",
}


def _resolve_company_id(company: str) -> str:
    """Resolve company name or ID."""
    if not company:
        return COMPANY_IDS["teamadapt"]
    lower = company.lower().strip()
    if lower in COMPANY_IDS:
        return COMPANY_IDS[lower]
    # Assume it's already an ID
    return company


async def _get_client():
    from temporalio.client import Client
    return await Client.connect(TEMPORAL_ADDRESS, namespace=NAMESPACE)


async def _submit_multi_agent_dispatch(args: dict) -> str:
    """Submit a MultiAgentDispatchWorkflow."""
    client = await _get_client()

    company_id = _resolve_company_id(args.get("company", ""))
    title = args.get("title", "Untitled project")
    description = args.get("description", "")
    max_sub_tasks = args.get("max_sub_tasks", 5)
    parallelism = args.get("parallelism", 3)
    parent_issue_id = args.get("parent_issue_id")

    from dataclasses import dataclass

    spec = {
        "company_id": company_id,
        "project_title": title,
        "project_description": description,
        "max_sub_tasks": max_sub_tasks,
        "sub_task_parallelism": parallelism,
        "parent_issue_id": parent_issue_id,
    }

    workflow_id = f"dispatch-{uuid.uuid4().hex[:8]}"
    handle = await client.start_workflow(
        "MultiAgentDispatchWorkflow",
        spec,
        id=workflow_id,
        task_queue=TASK_QUEUE,
    )

    return json.dumps({
        "status": "submitted",
        "workflow_id": workflow_id,
        "workflow_type": "MultiAgentDispatchWorkflow",
        "title": title,
        "company_id": company_id,
        "max_sub_tasks": max_sub_tasks,
        "parallelism": parallelism,
    })


async def _submit_dag_dispatch(args: dict) -> str:
    """Submit a DAGDispatchWorkflow."""
    client = await _get_client()

    company_id = _resolve_company_id(args.get("company", ""))
    title = args.get("title", "Untitled project")
    description = args.get("description", "")
    max_sub_tasks = args.get("max_sub_tasks", 6)
    parallelism = args.get("parallelism", 3)
    parent_issue_id = args.get("parent_issue_id")

    spec = {
        "company_id": company_id,
        "project_title": title,
        "project_description": description,
        "max_sub_tasks": max_sub_tasks,
        "sub_task_parallelism": parallelism,
        "parent_issue_id": parent_issue_id,
    }

    workflow_id = f"dag-{uuid.uuid4().hex[:8]}"
    handle = await client.start_workflow(
        "DAGDispatchWorkflow",
        spec,
        id=workflow_id,
        task_queue=TASK_QUEUE,
    )

    return json.dumps({
        "status": "submitted",
        "workflow_id": workflow_id,
        "workflow_type": "DAGDispatchWorkflow",
        "title": title,
        "company_id": company_id,
        "max_sub_tasks": max_sub_tasks,
        "parallelism": parallelism,
    })


async def _submit_agent_work_loop(args: dict) -> str:
    """Submit an AgentWorkLoopWorkflow."""
    client = await _get_client()

    company_id = _resolve_company_id(args.get("company", ""))
    agent_id = args.get("agent_id", "")
    issue_id = args.get("issue_id", "")
    max_run_seconds = args.get("max_run_seconds", 600)
    max_retries = args.get("max_retries", 1)

    if not agent_id or not issue_id:
        return json.dumps({"error": "agent_id and issue_id are required"})

    item = {
        "company_id": company_id,
        "agent_id": agent_id,
        "issue_id": issue_id,
        "max_run_seconds": max_run_seconds,
        "max_retries": max_retries,
    }

    workflow_id = f"work-{issue_id[:8]}-{uuid.uuid4().hex[:4]}"
    handle = await client.start_workflow(
        "AgentWorkLoopWorkflow",
        item,
        id=workflow_id,
        task_queue=TASK_QUEUE,
    )

    return json.dumps({
        "status": "submitted",
        "workflow_id": workflow_id,
        "workflow_type": "AgentWorkLoopWorkflow",
        "agent_id": agent_id,
        "issue_id": issue_id,
    })


async def _submit_health_check(args: dict) -> str:
    """Submit an ad-hoc FleetHealthCheckWorkflow."""
    client = await _get_client()

    config = {
        "check_vllm": args.get("check_vllm", True),
        "check_temporal": args.get("check_temporal", True),
        "check_dragonfly": args.get("check_dragonfly", True),
        "check_nats": args.get("check_nats", True),
        "check_paperclip": args.get("check_paperclip", True),
        "vllm_url": "http://127.0.0.1:10001/health",
        "temporal_url": "http://127.0.0.1:7243/api/v1/namespaces",
        "dragonfly_host": "x100-gpu",
        "dragonfly_port": 18000,
        "nats_host": "127.0.0.1",
        "nats_port": 4222,
        "paperclip_url": "http://127.0.0.1:3100/api/companies",
        "state_key": "novamesh:fleet:health",
    }

    workflow_id = f"health-adhoc-{uuid.uuid4().hex[:8]}"
    handle = await client.start_workflow(
        "FleetHealthCheckWorkflow",
        config,
        id=workflow_id,
        task_queue=TASK_QUEUE,
    )

    # Wait for result (health checks are fast)
    try:
        result = await asyncio.wait_for(handle.result(), timeout=20)
        return json.dumps({
            "status": "completed",
            "workflow_id": workflow_id,
            "result": result,
        })
    except asyncio.TimeoutError:
        return json.dumps({
            "status": "submitted",
            "workflow_id": workflow_id,
            "note": "Health check still running. Query later.",
        })


async def _query_workflow(args: dict) -> str:
    """Query a workflow's status and optionally its result."""
    client = await _get_client()
    workflow_id = args.get("workflow_id", "")

    if not workflow_id:
        return json.dumps({"error": "workflow_id is required"})

    handle = client.get_workflow_handle(workflow_id)

    try:
        desc = await handle.describe()
        result_data = {
            "workflow_id": workflow_id,
            "status": desc.status.name,
            "workflow_type": desc.workflow_type,
            "start_time": str(desc.start_time) if desc.start_time else None,
            "close_time": str(desc.close_time) if desc.close_time else None,
        }

        # If completed, get the result
        if desc.status.name in ("COMPLETED",):
            try:
                result = await asyncio.wait_for(handle.result(), timeout=5)
                if isinstance(result, dict):
                    result_data["result"] = result
                else:
                    result_data["result"] = str(result)
            except Exception:
                pass

        return json.dumps(result_data, default=str)
    except Exception as e:
        return json.dumps({"error": f"Failed to query workflow: {e}"})


async def _query_semaphore(args: dict) -> str:
    """Query the fleet semaphore state."""
    client = await _get_client()
    resource = args.get("resource", "vllm")
    # Try versioned ID first, fall back to base
    semaphore_id = f"fleet-semaphore-{resource}-v2"

    try:
        handle = client.get_workflow_handle(semaphore_id)
        state = await handle.query("semaphore_state")
        return json.dumps(state)
    except Exception as e:
        return json.dumps({"error": f"Failed to query semaphore: {e}"})


async def _list_workflows(args: dict) -> str:
    """List recent workflows."""
    client = await _get_client()
    limit = args.get("limit", 10)
    status_filter = args.get("status", "")  # RUNNING, COMPLETED, FAILED, etc.

    query = f"TaskQueue = '{TASK_QUEUE}'"
    if status_filter:
        query += f" AND ExecutionStatus = '{status_filter}'"

    try:
        workflows = []
        async for wf in client.list_workflows(query=query):
            workflows.append({
                "workflow_id": wf.id,
                "type": wf.workflow_type,
                "status": wf.status.name if wf.status else "?",
                "start_time": str(wf.start_time) if wf.start_time else None,
            })
            if len(workflows) >= limit:
                break

        return json.dumps({"workflows": workflows, "count": len(workflows)})
    except Exception as e:
        return json.dumps({"error": f"Failed to list workflows: {e}"})


# ── Main dispatch ──────────────────────────────────────────────────────

async def _submit_pipeline(args: dict) -> str:
    """Submit a PipelineWorkflow."""
    client = await _get_client()

    spec = {
        "name": args.get("name", "Unnamed pipeline"),
        "company_id": _resolve_company_id(args.get("company", "")),
        "stages": args.get("stages", []),
        "parent_issue_id": args.get("parent_issue_id"),
        "metadata": args.get("metadata", {}),
    }

    if not spec["stages"]:
        return json.dumps({"error": "stages list is required"})

    wf_id = f"pipeline-{uuid.uuid4().hex[:8]}"
    handle = await client.start_workflow(
        "PipelineWorkflow",
        spec,
        id=wf_id,
        task_queue=TASK_QUEUE,
    )

    return json.dumps({
        "status": "submitted",
        "workflow_id": wf_id,
        "workflow_type": "PipelineWorkflow",
        "name": spec["name"],
        "stages": len(spec["stages"]),
    })


async def _debug_workflow(args: dict) -> str:
    """Fetch and format workflow event history as a step-by-step replay."""
    client = await _get_client()
    workflow_id = args.get("workflow_id", "")

    if not workflow_id:
        return json.dumps({"error": "workflow_id is required"})

    handle = client.get_workflow_handle(workflow_id)

    try:
        desc = await handle.describe()
        history = await handle.fetch_history()

        steps = []
        for event in history.events:
            event_type = event.event_type
            event_time = str(event.event_time) if event.event_time else "?"
            step = {"event_type": event_type, "time": event_time[:19]}

            # Extract useful details from common event types
            if hasattr(event, "activity_task_scheduled_event_attributes"):
                attrs = event.activity_task_scheduled_event_attributes
                if attrs:
                    step["activity_type"] = attrs.activity_type.name if attrs.activity_type else "?"
                    step["detail"] = "scheduled"

            if hasattr(event, "activity_task_completed_event_attributes"):
                attrs = event.activity_task_completed_event_attributes
                if attrs and attrs.result and attrs.result.payloads:
                    try:
                        result_data = json.loads(attrs.result.payloads[0].data)
                        if isinstance(result_data, dict):
                            step["result_preview"] = str(result_data)[:200]
                        else:
                            step["result_preview"] = str(result_data)[:200]
                    except Exception:
                        step["result_preview"] = "(binary)"
                    step["detail"] = "completed"

            if hasattr(event, "activity_task_failed_event_attributes"):
                attrs = event.activity_task_failed_event_attributes
                if attrs and attrs.failure:
                    step["error"] = attrs.failure.message[:200] if attrs.failure.message else "unknown"
                    step["detail"] = "failed"

            if hasattr(event, "child_workflow_execution_started_event_attributes"):
                attrs = event.child_workflow_execution_started_event_attributes
                if attrs:
                    step["child_workflow_type"] = attrs.workflow_type.name if attrs.workflow_type else "?"
                    step["detail"] = "child_started"

            if hasattr(event, "child_workflow_execution_completed_event_attributes"):
                step["detail"] = "child_completed"

            if hasattr(event, "child_workflow_execution_failed_event_attributes"):
                step["detail"] = "child_failed"

            steps.append(step)

        # Filter to interesting events only
        interesting = [s for s in steps if s.get("detail") or s.get("error")]
        if not interesting:
            interesting = steps[:20]  # fallback: first 20 raw events

        return json.dumps({
            "workflow_id": workflow_id,
            "workflow_type": desc.workflow_type,
            "status": desc.status.name,
            "start_time": str(desc.start_time)[:19] if desc.start_time else None,
            "close_time": str(desc.close_time)[:19] if desc.close_time else None,
            "total_events": len(steps),
            "steps": interesting[:30],  # cap at 30 to fit context
        }, default=str)

    except Exception as e:
        return json.dumps({"error": f"Failed to debug workflow: {e}"})


ACTIONS = {
    "submit_dispatch": _submit_multi_agent_dispatch,
    "submit_dag": _submit_dag_dispatch,
    "submit_work_loop": _submit_agent_work_loop,
    "submit_health_check": _submit_health_check,
    "submit_pipeline": _submit_pipeline,
    "query_workflow": _query_workflow,
    "query_semaphore": _query_semaphore,
    "list_workflows": _list_workflows,
    "debug_workflow": _debug_workflow,
}


def temporal_submit(args: dict, **kwargs) -> str:
    """Main handler — dispatches to the appropriate async function."""
    action = args.get("action", "")
    if action not in ACTIONS:
        return json.dumps({
            "error": f"Unknown action: {action}",
            "available_actions": list(ACTIONS.keys()),
        })

    handler = ACTIONS[action]

    # Run async handler — always use a fresh event loop in a thread to avoid
    # conflicts with Hermes's own event loop.
    import concurrent.futures
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(asyncio.run, handler(args))
            return future.result(timeout=30)
    except concurrent.futures.TimeoutError:
        return json.dumps({"error": "Temporal operation timed out after 30s"})
    except Exception as e:
        return json.dumps({"error": str(e)})


def check_temporal_requirements() -> Optional[str]:
    """Check if Temporal is reachable."""
    try:
        import socket
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(2)
        s.connect(("localhost", 7233))
        s.close()
        return None  # available
    except Exception:
        return "Temporal server not reachable at localhost:7233"


# ── Schema ─────────────────────────────────────────────────────────────

TEMPORAL_SUBMIT_SCHEMA = {
    "name": "temporal_submit",
    "description": (
        "Submit, query, and manage NovaMesh Temporal workflows. "
        "Actions:\n"
        "- submit_dispatch: Multi-agent project dispatch (decompose → assign → execute)\n"
        "- submit_dag: DAG-aware project dispatch (dependency-ordered execution)\n"
        "- submit_work_loop: Single agent work loop (claim → wake → poll → QA)\n"
        "- submit_health_check: Ad-hoc fleet health probe (returns immediately)\n"
        "- query_workflow: Get status/result of any workflow by ID\n"
        "- query_semaphore: Check fleet semaphore state (who holds vLLM slots)\n"
        "- list_workflows: List recent workflows on the novamesh queue\n"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": [
                    "submit_dispatch", "submit_dag", "submit_work_loop",
                    "submit_health_check", "submit_pipeline",
                    "query_workflow", "query_semaphore", "list_workflows",
                    "debug_workflow",
                ],
                "description": "Which action to perform",
            },
            "title": {
                "type": "string",
                "description": "Project title (for submit_dispatch, submit_dag)",
            },
            "description": {
                "type": "string",
                "description": "Project description (for submit_dispatch, submit_dag)",
            },
            "company": {
                "type": "string",
                "description": "Company name or ID (default: teamadapt)",
            },
            "max_sub_tasks": {
                "type": "integer",
                "description": "Max sub-tasks for decomposition (default: 5)",
            },
            "parallelism": {
                "type": "integer",
                "description": "Max concurrent child workflows (default: 3)",
            },
            "agent_id": {
                "type": "string",
                "description": "Agent ID (for submit_work_loop)",
            },
            "issue_id": {
                "type": "string",
                "description": "Issue ID (for submit_work_loop)",
            },
            "parent_issue_id": {
                "type": "string",
                "description": "Existing parent issue ID (skip creation)",
            },
            "max_run_seconds": {
                "type": "integer",
                "description": "Max run time in seconds (default: 600)",
            },
            "max_retries": {
                "type": "integer",
                "description": "Max retry attempts (default: 1)",
            },
            "workflow_id": {
                "type": "string",
                "description": "Workflow ID (for query_workflow)",
            },
            "resource": {
                "type": "string",
                "description": "Resource name for semaphore query (default: vllm)",
            },
            "limit": {
                "type": "integer",
                "description": "Max results for list_workflows (default: 10)",
            },
            "status": {
                "type": "string",
                "description": "Status filter for list_workflows (RUNNING, COMPLETED, FAILED)",
            },
        },
        "required": ["action"],
    },
}


# ── Registry ───────────────────────────────────────────────────────────

from tools.registry import registry

registry.register(
    name="temporal_submit",
    toolset="temporal",
    schema=TEMPORAL_SUBMIT_SCHEMA,
    handler=lambda args, **kw: temporal_submit(args, **kw),
    check_fn=check_temporal_requirements,
)
