"""NovaMesh context engine — live fleet/issue context injection.

Wraps the built-in ContextCompressor for token management while injecting
NovaMesh-specific context at session start and on each turn:
  - Active PC issues assigned to this agent
  - Fleet health state (agent count, vLLM status, service health)
  - Cross-agent coordination (recent musketeers channel activity)
  - Domain context from Router matches

The engine also exposes tools the agent can call:
  - query_fleet_state: live snapshot of fleet health
  - query_my_issues: current PC assignments

Config (config.yaml):
  context:
    engine: novamesh
    novamesh:
      pc_api: "http://127.0.0.1:3100/api"
      dragonfly_host: "x100-gpu"
      dragonfly_port: 18000
      company_id: "92d5b79c-ca3c-4d30-b730-15ae0b00227c"
      agent_id: null  # auto-detect from PAPERCLIP_AGENT_ID or config
      inject_issues: true
      inject_fleet_health: true
      inject_comms: true
      max_context_injection_tokens: 2000
"""

from __future__ import annotations

import json
import logging
import os
import time
import urllib.request
from typing import Any, Dict, List, Optional

from agent.context_engine import ContextEngine

logger = logging.getLogger(__name__)

_CHARS_PER_TOKEN = 4  # rough estimate for injection budget


class NovaMeshContextEngine(ContextEngine):
    """Context engine that injects NovaMesh fleet state into agent turns."""

    @property
    def name(self) -> str:
        return "novamesh"

    def __init__(self):
        # These get populated from config in on_session_start or update_model
        self._compressor = None
        self._config: Dict[str, Any] = {}
        self._pc_api = "http://127.0.0.1:3100/api"
        self._dragonfly_host = "x100-gpu"
        self._dragonfly_port = 18000
        self._company_id = ""
        self._agent_id = ""
        self._max_injection_chars = 2000 * _CHARS_PER_TOKEN  # ~2000 tokens
        self._inject_issues = True
        self._inject_fleet_health = True
        self._inject_comms = True

        # Cache to avoid hammering APIs every turn
        self._issues_cache: Optional[str] = None
        self._issues_cache_ts: float = 0
        self._fleet_cache: Optional[str] = None
        self._fleet_cache_ts: float = 0
        self._cache_ttl: float = 60  # seconds

    # -- Core interface (delegated to compressor) --------------------------

    def update_from_response(self, usage: Dict[str, Any]) -> None:
        if self._compressor:
            self._compressor.update_from_response(usage)
            # Mirror token state for run_agent.py reads
            self.last_prompt_tokens = self._compressor.last_prompt_tokens
            self.last_completion_tokens = self._compressor.last_completion_tokens
            self.last_total_tokens = self._compressor.last_total_tokens
            self.threshold_tokens = self._compressor.threshold_tokens
            self.context_length = self._compressor.context_length
            self.compression_count = self._compressor.compression_count

    def should_compress(self, prompt_tokens: int = None) -> bool:
        if self._compressor:
            return self._compressor.should_compress(prompt_tokens)
        return False

    def compress(
        self,
        messages: List[Dict[str, Any]],
        current_tokens: int = None,
    ) -> List[Dict[str, Any]]:
        if self._compressor:
            return self._compressor.compress(messages, current_tokens)
        return messages

    def should_compress_preflight(self, messages: List[Dict[str, Any]]) -> bool:
        if self._compressor:
            return self._compressor.should_compress_preflight(messages)
        return False

    # -- Session lifecycle -------------------------------------------------

    def on_session_start(self, session_id: str, **kwargs) -> None:
        # Load config from kwargs or environment
        hermes_home = kwargs.get("hermes_home", os.environ.get("HERMES_HOME", "~/.hermes"))
        self._load_config(hermes_home)

        # Lazy-init the wrapped compressor
        if self._compressor is None:
            self._init_compressor(kwargs)

        if self._compressor and hasattr(self._compressor, "on_session_start"):
            self._compressor.on_session_start(session_id, **kwargs)

    def on_session_end(self, session_id: str, messages: List[Dict[str, Any]]) -> None:
        if self._compressor:
            self._compressor.on_session_end(session_id, messages)

    def on_session_reset(self) -> None:
        super().on_session_reset()
        self._issues_cache = None
        self._fleet_cache = None
        if self._compressor:
            self._compressor.on_session_reset()

    def update_model(self, model: str, context_length: int, **kwargs) -> None:
        super().update_model(model, context_length, **kwargs)
        if self._compressor:
            self._compressor.update_model(model, context_length, **kwargs)

    # -- Tools -------------------------------------------------------------

    def get_tool_schemas(self) -> List[Dict[str, Any]]:
        return [
            {
                "type": "function",
                "function": {
                    "name": "novamesh_fleet_state",
                    "description": (
                        "Get live NovaMesh fleet state: active agents, service health "
                        "(vLLM, Temporal, NATS, Dragonfly), and recent cross-agent activity."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {},
                        "required": [],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "novamesh_my_issues",
                    "description": (
                        "Get current Paperclip issue assignments for this agent: "
                        "todo, in_progress, blocked issues with titles and priorities."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {},
                        "required": [],
                    },
                },
            },
        ]

    def handle_tool_call(self, name: str, args: Dict[str, Any], **kwargs) -> str:
        if name == "novamesh_fleet_state":
            return self._get_fleet_state_json()
        elif name == "novamesh_my_issues":
            return self._get_issues_json()
        return json.dumps({"error": f"Unknown tool: {name}"})

    # -- Context injection (the main value-add) ----------------------------

    def get_context_injection(self) -> str:
        """Build the context block to inject into the system prompt.

        Called by the NovaMesh system prompt builder (or can be manually
        prepended to system messages). Returns a concise markdown block
        within the token budget.
        """
        parts = []
        budget = self._max_injection_chars

        if self._inject_issues and self._agent_id:
            issues_block = self._get_issues_summary()
            if issues_block and len(issues_block) < budget:
                parts.append(issues_block)
                budget -= len(issues_block)

        if self._inject_fleet_health:
            fleet_block = self._get_fleet_summary()
            if fleet_block and len(fleet_block) < budget:
                parts.append(fleet_block)
                budget -= len(fleet_block)

        if self._inject_comms:
            comms_block = self._get_recent_comms()
            if comms_block and len(comms_block) < budget:
                parts.append(comms_block)

        if not parts:
            return ""

        return (
            "\n\n---\n## NovaMesh Context (live, auto-injected)\n\n"
            + "\n\n".join(parts)
            + "\n---\n"
        )

    # -- Internal: data fetchers -------------------------------------------

    def _get_issues_summary(self) -> str:
        now = time.time()
        if self._issues_cache and (now - self._issues_cache_ts) < self._cache_ttl:
            return self._issues_cache

        try:
            url = (
                f"{self._pc_api}/companies/{self._company_id}/issues"
                f"?assigneeAgentId={self._agent_id}"
                f"&status=todo,in_progress,in_review,blocked"
            )
            req = urllib.request.Request(url, headers={"Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=5) as resp:
                issues = json.loads(resp.read())

            if not issues:
                result = "### My Assignments\nNo active issues."
            else:
                lines = [f"### My Assignments ({len(issues)} active)"]
                for i in issues[:10]:  # cap at 10
                    ident = i.get("identifier", "?")
                    title = i.get("title", "?")[:60]
                    status = i.get("status", "?")
                    priority = i.get("priority", "?")
                    lines.append(f"- **{ident}** [{status}] {priority}: {title}")
                result = "\n".join(lines)

            self._issues_cache = result
            self._issues_cache_ts = now
            return result
        except Exception as e:
            logger.debug("Failed to fetch issues: %s", e)
            return ""

    def _get_fleet_summary(self) -> str:
        now = time.time()
        if self._fleet_cache and (now - self._fleet_cache_ts) < self._cache_ttl:
            return self._fleet_cache

        try:
            import redis
            r = redis.Redis(
                host=self._dragonfly_host,
                port=self._dragonfly_port,
                decode_responses=True,
                socket_connect_timeout=2,
            )

            # Read from FleetHealthCheckWorkflow's cached state in Dragonfly
            raw = r.get("novamesh:fleet:health")
            if raw:
                state = json.loads(raw)
                lines = ["### Fleet Health"]
                for svc in state.get("services", []):
                    name = svc.get("name", "?")
                    status = svc.get("status", "?")
                    latency = svc.get("latency_ms", 0)
                    indicator = "green" if status == "up" else "RED"
                    lines.append(f"- **{name}**: {indicator} ({latency:.0f}ms)")
                checked = state.get("checked_at", "")[:19]
                if checked:
                    lines.append(f"- *checked: {checked}*")
                result = "\n".join(lines)
            else:
                # Fallback: direct probe if health workflow hasn't run yet
                result = "### Fleet Health\n- *no health data (workflow not yet fired)*"

            self._fleet_cache = result
            self._fleet_cache_ts = now
            return result
        except Exception as e:
            logger.debug("Failed to get fleet state: %s", e)
            return ""

    def _get_recent_comms(self) -> str:
        """Pull last 3 messages from musketeers channel."""
        try:
            import redis
            r = redis.Redis(
                host=self._dragonfly_host,
                port=self._dragonfly_port,
                decode_responses=True,
                socket_connect_timeout=2,
            )
            msgs = r.xrevrange("nova.lineage.musketeers", count=3)
            if not msgs:
                return ""

            lines = ["### Recent Comms (musketeers)"]
            for msg_id, fields in msgs:
                sender = fields.get("from", "?")
                body = fields.get("body", "")[:100]
                lines.append(f"- **{sender}**: {body}")

            return "\n".join(lines)
        except Exception as e:
            logger.debug("Failed to read comms: %s", e)
            return ""

    def _get_fleet_state_json(self) -> str:
        fleet = self._get_fleet_summary()
        issues = self._get_issues_summary()
        comms = self._get_recent_comms()
        return json.dumps({
            "fleet_health": fleet,
            "my_issues": issues,
            "recent_comms": comms,
        })

    def _get_issues_json(self) -> str:
        try:
            url = (
                f"{self._pc_api}/companies/{self._company_id}/issues"
                f"?assigneeAgentId={self._agent_id}"
                f"&status=todo,in_progress,in_review,blocked"
            )
            req = urllib.request.Request(url, headers={"Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=5) as resp:
                issues = json.loads(resp.read())
            return json.dumps(issues, default=str)
        except Exception as e:
            return json.dumps({"error": str(e)})

    # -- Internal: config loading ------------------------------------------

    def _load_config(self, hermes_home: str) -> None:
        """Load novamesh-specific config from config.yaml context.novamesh."""
        try:
            import yaml
            config_path = os.path.expanduser(f"{hermes_home}/config.yaml")
            if os.path.exists(config_path):
                with open(config_path) as f:
                    cfg = yaml.safe_load(f) or {}
                nm_cfg = cfg.get("context", {}).get("novamesh", {})
                if nm_cfg:
                    self._config = nm_cfg
                    self._pc_api = nm_cfg.get("pc_api", self._pc_api)
                    self._dragonfly_host = nm_cfg.get("dragonfly_host", self._dragonfly_host)
                    self._dragonfly_port = nm_cfg.get("dragonfly_port", self._dragonfly_port)
                    self._company_id = nm_cfg.get("company_id", self._company_id)
                    self._agent_id = nm_cfg.get("agent_id", self._agent_id)
                    self._inject_issues = nm_cfg.get("inject_issues", True)
                    self._inject_fleet_health = nm_cfg.get("inject_fleet_health", True)
                    self._inject_comms = nm_cfg.get("inject_comms", True)
                    max_tokens = nm_cfg.get("max_context_injection_tokens", 2000)
                    self._max_injection_chars = max_tokens * _CHARS_PER_TOKEN
        except Exception as e:
            logger.debug("Failed to load novamesh config: %s", e)

        # Env var fallbacks
        if not self._agent_id:
            self._agent_id = os.environ.get("PAPERCLIP_AGENT_ID", "")
        if not self._company_id:
            self._company_id = os.environ.get("PAPERCLIP_COMPANY_ID", "")

    def _init_compressor(self, kwargs: Dict[str, Any]) -> None:
        """Initialize the wrapped ContextCompressor."""
        try:
            from agent.context_compressor import ContextCompressor
            model = kwargs.get("model", "claude-opus-4-6")
            base_url = kwargs.get("base_url", "")
            api_key = kwargs.get("api_key", "")
            provider = kwargs.get("provider", "")
            self._compressor = ContextCompressor(
                model=model,
                base_url=base_url,
                api_key=api_key,
                provider=provider,
            )
            # Mirror initial state
            self.context_length = self._compressor.context_length
            self.threshold_tokens = self._compressor.threshold_tokens
        except Exception as e:
            logger.warning("Failed to init wrapped compressor: %s", e)
