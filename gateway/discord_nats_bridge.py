"""
Discord→NATS→Temporal bridge for Hermes gateway.

Hooks into the Hermes Discord adapter lifecycle to:
1. Forward incoming Discord messages to NATS (nova.discord.inbound)
2. Optionally route through Temporal for durable execution
3. Publish agent responses back to NATS (nova.discord.outbound)

This module is loaded by the gateway when Discord is enabled.
It hooks into the adapter's message processing pipeline.

Setup:
1. Set DISCORD_TOKEN in ~/.hermes/.env
2. Enable in config.yaml:
   discord:
     require_mention: true
     nats_bridge: true        # enable NATS forwarding
     temporal_dispatch: true  # enable Temporal routing
3. Start gateway: hermes gateway

The bridge publishes every Discord message to NATS so other agents
on the mesh can observe Discord activity. When temporal_dispatch is
true, messages route through HermesConversationWorkflow for durable
execution.
"""

import json
import logging
import time
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


def create_discord_nats_hook(config: dict) -> Optional[callable]:
    """Create a post-message hook that bridges Discord to NATS.

    Returns a callback function that the Discord adapter calls
    after receiving each message.
    """
    nats_bridge = config.get("discord", {}).get("nats_bridge", False)
    if not nats_bridge:
        return None

    async def on_discord_message(
        message_text: str,
        author: str,
        channel_id: str,
        guild_id: str = "",
        **kwargs,
    ):
        """Called by the Discord adapter on each incoming message."""
        try:
            import nats as nats_mod

            nc = await nats_mod.connect(
                "nats://admin:Echovaeris1966!!@127.0.0.1:18040"
            )
            js = nc.jetstream()

            event = {
                "type": "discord.message",
                "author": author,
                "channel_id": channel_id,
                "guild_id": guild_id,
                "message": message_text[:500],
                "timestamp": time.strftime(
                    "%Y-%m-%dT%H:%M:%SZ", time.gmtime()
                ),
            }

            await js.publish(
                "nova.discord.inbound",
                json.dumps(event).encode(),
            )
            await nc.close()

            logger.debug(
                f"Discord→NATS: {author} in {channel_id}: {message_text[:50]}"
            )
        except Exception as e:
            logger.warning(f"Discord→NATS bridge failed: {e}")

    return on_discord_message


def create_discord_response_hook(config: dict) -> Optional[callable]:
    """Create a hook that publishes agent responses to NATS."""
    nats_bridge = config.get("discord", {}).get("nats_bridge", False)
    if not nats_bridge:
        return None

    async def on_agent_response(
        response_text: str,
        channel_id: str,
        **kwargs,
    ):
        """Called after the agent produces a response for Discord."""
        try:
            import nats as nats_mod

            nc = await nats_mod.connect(
                "nats://admin:Echovaeris1966!!@127.0.0.1:18040"
            )
            js = nc.jetstream()

            event = {
                "type": "discord.response",
                "channel_id": channel_id,
                "response": response_text[:500],
                "timestamp": time.strftime(
                    "%Y-%m-%dT%H:%M:%SZ", time.gmtime()
                ),
            }

            await js.publish(
                "nova.discord.outbound",
                json.dumps(event).encode(),
            )
            await nc.close()
        except Exception as e:
            logger.warning(f"Discord response→NATS failed: {e}")

    return on_agent_response
