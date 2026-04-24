"""
Temporal adapter for Hermes cron jobs.

Provides a drop-in replacement for cron job execution that submits
jobs as Temporal scheduled workflows instead of running them directly
via APScheduler. Each cron job becomes a Temporal schedule with
durable execution.

Usage:
    from cron.temporal_adapter import create_temporal_schedule, is_temporal_cron_enabled

    if is_temporal_cron_enabled():
        await create_temporal_schedule(job)
    else:
        scheduler.add_job(...)

Enable via config.yaml:
    cron:
      temporal_backend: true

Or environment variable:
    HERMES_TEMPORAL_CRON=1
"""

import asyncio
import json
import logging
import os
import uuid
from datetime import timedelta
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

TEMPORAL_ADDRESS = os.environ.get("TEMPORAL_ADDRESS", "localhost:7233")
NAMESPACE = os.environ.get("TEMPORAL_NAMESPACE", "novamesh")
TASK_QUEUE = os.environ.get("TEMPORAL_TASK_QUEUE", "novamesh")

_client = None
_enabled: Optional[bool] = None


def is_temporal_cron_enabled() -> bool:
    """Check if Temporal cron backend is enabled."""
    global _enabled
    if _enabled is not None:
        return _enabled

    if os.environ.get("HERMES_TEMPORAL_CRON", "").lower() in ("1", "true", "yes"):
        _enabled = True
        return True

    try:
        import yaml
        from hermes_cli.config import get_config_path
        config_path = get_config_path()
        if os.path.exists(config_path):
            with open(config_path) as f:
                cfg = yaml.safe_load(f) or {}
            cron_cfg = cfg.get("cron", {})
            if isinstance(cron_cfg, dict) and cron_cfg.get("temporal_backend"):
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


def cron_to_interval(cron_expression: str) -> Optional[timedelta]:
    """Convert simple cron expressions to Temporal schedule intervals.

    Handles common patterns:
      "*/5 * * * *"  → every 5 minutes
      "0 * * * *"    → every hour
      "0 9 * * *"    → every day at 9am (approximated as 24h)
      "0 0 * * 0"    → every week (approximated as 7d)
    """
    parts = cron_expression.strip().split()
    if len(parts) != 5:
        return None

    minute, hour, dom, month, dow = parts

    # Every N minutes
    if minute.startswith("*/") and hour == "*":
        try:
            return timedelta(minutes=int(minute[2:]))
        except ValueError:
            pass

    # Every hour
    if minute.isdigit() and hour == "*":
        return timedelta(hours=1)

    # Daily
    if minute.isdigit() and hour.isdigit() and dom == "*" and month == "*" and dow == "*":
        return timedelta(days=1)

    # Weekly
    if minute.isdigit() and hour.isdigit() and dom == "*" and month == "*" and dow.isdigit():
        return timedelta(weeks=1)

    return None


async def create_temporal_schedule(
    job_id: str,
    job_config: dict,
) -> dict:
    """Create a Temporal schedule from a Hermes cron job config.

    Args:
        job_id: unique job identifier
        job_config: dict with keys: prompt, cron, deliver, etc.

    Returns:
        dict with schedule_id and status
    """
    from temporalio.client import (
        Schedule,
        ScheduleActionStartWorkflow,
        ScheduleSpec,
        ScheduleIntervalSpec,
    )

    client = await _get_client()

    cron_expr = job_config.get("cron", "*/5 * * * *")
    interval = cron_to_interval(cron_expr)
    if interval is None:
        interval = timedelta(minutes=5)
        logger.warning(f"Could not parse cron '{cron_expr}', defaulting to 5 min")

    schedule_id = f"cron-{job_id}"
    workflow_id = f"cron-job-{job_id}"

    # The workflow input is the job config itself — the cron migration
    # activity knows how to execute it
    workflow_input = {
        "job_id": job_id,
        "prompt": job_config.get("prompt", ""),
        "deliver": job_config.get("deliver", "local"),
        "silent": job_config.get("silent", False),
        "metadata": job_config.get("metadata", {}),
    }

    try:
        await client.create_schedule(
            schedule_id,
            Schedule(
                action=ScheduleActionStartWorkflow(
                    "HermesConversationWorkflow",
                    json.dumps({
                        "user_message": workflow_input["prompt"],
                        "task_id": job_id,
                        "metadata": {"cron_job": True, **workflow_input},
                    }),
                    id=workflow_id,
                    task_queue=TASK_QUEUE,
                ),
                spec=ScheduleSpec(
                    intervals=[ScheduleIntervalSpec(every=interval)],
                ),
            ),
        )

        logger.info(f"Created Temporal schedule {schedule_id} (every {interval})")
        return {
            "schedule_id": schedule_id,
            "interval": str(interval),
            "status": "created",
        }

    except Exception as e:
        if "already" in str(e).lower():
            return {"schedule_id": schedule_id, "status": "already_exists"}
        logger.error(f"Failed to create schedule {schedule_id}: {e}")
        return {"schedule_id": schedule_id, "status": "error", "error": str(e)}


async def delete_temporal_schedule(job_id: str) -> dict:
    """Delete a Temporal schedule for a cron job."""
    client = await _get_client()
    schedule_id = f"cron-{job_id}"

    try:
        handle = client.get_schedule_handle(schedule_id)
        await handle.delete()
        return {"schedule_id": schedule_id, "status": "deleted"}
    except Exception as e:
        return {"schedule_id": schedule_id, "status": "error", "error": str(e)}
