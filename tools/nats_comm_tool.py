"""
Nova NATS Communication Tool for Hermes agents.

Enables any Hermes agent to participate in the Nova Collective NATS mesh:
- Send direct messages to other agents
- Post to project coordination streams
- Query recent messages from streams

Registered as the `nats_comm` tool in the `nats` toolset.

Connection: nats://admin:Echovaeris1966!!@127.0.0.1:18040
Persistence: JetStream (all messages durable)
"""

import asyncio
import json
import logging
import os
import time
import uuid
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

NATS_URL = os.environ.get(
    "NATS_URL", "nats://admin:Echovaeris1966!!@127.0.0.1:18040"
)
AGENT_NAME = os.environ.get("NOVA_AGENT_NAME", "hermes")


async def _get_nats():
    """Connect to NATS."""
    import nats
    return await nats.connect(NATS_URL)


async def _send_direct(args: dict) -> str:
    """Send a direct message to another agent."""
    to = args.get("to", "")
    message = args.get("message", "")
    subject_line = args.get("subject", "")
    sender = args.get("from", AGENT_NAME)

    if not to or not message:
        return json.dumps({"error": "both 'to' and 'message' are required"})

    nc = await _get_nats()
    js = nc.jetstream()

    msg_type = args.get("type", "direct")

    payload = {
        "from": sender,
        "to": to,
        "type": msg_type,
        "subject": subject_line,
        "message": message,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }

    nats_subject = f"nova.{to}.direct"
    try:
        ack = await js.publish(nats_subject, json.dumps(payload).encode())
        await nc.close()
        return json.dumps({
            "status": "sent",
            "to": to,
            "subject": nats_subject,
            "stream": ack.stream,
            "seq": ack.seq,
        })
    except Exception as e:
        await nc.close()
        return json.dumps({"error": str(e)})


async def _post_project(args: dict) -> str:
    """Post to the project coordination stream."""
    message = args.get("message", "")
    subject_line = args.get("subject", "")
    sender = args.get("from", AGENT_NAME)

    if not message:
        return json.dumps({"error": "'message' is required"})

    nc = await _get_nats()
    js = nc.jetstream()

    msg_type = args.get("type", "broadcast")

    payload = {
        "from": sender,
        "type": msg_type,
        "subject": subject_line,
        "message": message,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }

    try:
        ack = await js.publish("project.novacol.main", json.dumps(payload).encode())
        await nc.close()
        return json.dumps({
            "status": "posted",
            "stream": ack.stream,
            "seq": ack.seq,
        })
    except Exception as e:
        await nc.close()
        return json.dumps({"error": str(e)})


async def _read_recent(args: dict) -> str:
    """Read recent messages from a stream."""
    stream = args.get("stream", "direct")  # "direct" or "project"
    agent = args.get("agent", AGENT_NAME)
    limit = args.get("limit", 5)

    nc = await _get_nats()
    js = nc.jetstream()

    if stream == "direct":
        nats_subject = f"nova.{agent}.direct"
    elif stream == "project":
        nats_subject = "project.novacol.main"
    else:
        nats_subject = stream

    messages = []
    try:
        sub = await js.subscribe(nats_subject, ordered_consumer=True)
        for _ in range(limit):
            try:
                msg = await asyncio.wait_for(sub.next_msg(), timeout=2)
                await msg.ack()
                data = json.loads(msg.data.decode())
                messages.append(data)
            except asyncio.TimeoutError:
                break
        await nc.close()
    except Exception as e:
        await nc.close()
        return json.dumps({"error": str(e), "messages": []})

    return json.dumps({"messages": messages, "count": len(messages)})


# ── Action dispatch ──────────────────────────────────────────────────

ACTIONS = {
    "send": _send_direct,
    "post_project": _post_project,
    "read_recent": _read_recent,
}


def nats_comm(args: dict, **kwargs) -> str:
    """Main handler — dispatches to the appropriate async function."""
    action = args.get("action", "")
    if action not in ACTIONS:
        return json.dumps({
            "error": f"Unknown action: {action}",
            "available_actions": list(ACTIONS.keys()),
        })

    handler = ACTIONS[action]

    import concurrent.futures
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(asyncio.run, handler(args))
            return future.result(timeout=15)
    except concurrent.futures.TimeoutError:
        return json.dumps({"error": "NATS operation timed out"})
    except Exception as e:
        return json.dumps({"error": str(e)})


def check_nats_requirements() -> Optional[str]:
    """Check if NATS is reachable."""
    import socket
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(2)
        s.connect(("127.0.0.1", 18040))
        s.close()
        return None
    except Exception:
        return "NATS server not reachable at 127.0.0.1:18040"


# ── Schema ───────────────────────────────────────────────────────────

NATS_COMM_SCHEMA = {
    "name": "nats_comm",
    "description": (
        "Communicate with other Nova Collective agents via NATS mesh. "
        "Actions:\n"
        "- send: Send a direct message to a specific agent\n"
        "- post_project: Post to the project coordination stream\n"
        "- read_recent: Read recent messages from a stream\n\n"
        "All messages are persisted in JetStream and delivered in real-time."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["send", "post_project", "read_recent"],
                "description": "Which action to perform",
            },
            "to": {
                "type": "string",
                "description": "Target agent name (for send). e.g., 'echo', 'chronos', 'vertex'",
            },
            "message": {
                "type": "string",
                "description": "Message content (for send and post_project)",
            },
            "subject": {
                "type": "string",
                "description": "Subject line (optional)",
            },
            "from": {
                "type": "string",
                "description": "Sender name override (default: agent name from env)",
            },
            "stream": {
                "type": "string",
                "description": "Stream to read from (for read_recent): 'direct' or 'project'",
            },
            "agent": {
                "type": "string",
                "description": "Agent name for direct stream reading (default: self)",
            },
            "limit": {
                "type": "integer",
                "description": "Max messages to read (default: 5)",
            },
        },
        "required": ["action"],
    },
}


# ── Registry ─────────────────────────────────────────────────────────

from tools.registry import registry

registry.register(
    name="nats_comm",
    toolset="nats",
    schema=NATS_COMM_SCHEMA,
    handler=lambda args, **kw: nats_comm(args, **kw),
    check_fn=check_nats_requirements,
)
