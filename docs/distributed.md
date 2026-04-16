# Hermes Distributed Multi-Agent Architecture

**Status:** Design Reference
**Last Updated:** 2026-04-08

---

## 1. Overview

Hermes supports multi-agent operation through four complementary subsystems:

1. **Profile-Based Isolation** -- Independent agent instances with separate state
2. **Mixture-of-Agents (MoA)** -- Parallel multi-LLM collaboration for complex reasoning
3. **Subagent Delegation** -- Parent-child task orchestration via tool calls
4. **Multi-Platform Gateway** -- Concurrent messaging channel adapters across servers

These layers compose: a distributed deployment runs profile-isolated agents on multiple servers, each hosting platform adapters, with optional MoA and subagent delegation for advanced reasoning.

---

## 2. Profile-Based Multi-Agent Isolation

**Source:** `hermes_cli/main.py` (`_apply_profile_override()`)
**Source:** `hermes_cli/config.py` (path resolution via `get_hermes_home()`)

Each profile acts as an independent agent instance with fully isolated state:

```
~/.hermes/                        # Default profile
├── config.yaml
├── .env
└── profiles/
    ├── developer/                # Development agent
    │   ├── config.yaml
    │   └── skills/
    ├── data-science/             # Analytics agent
    └── operations/               # Ops/infrastructure agent
```

Key mechanisms:

- `_apply_profile_override()` sets `HERMES_HOME` before any imports, ensuring all downstream path resolution is scoped to the profile
- All 119+ internal references use `get_hermes_home()` for path resolution -- no hardcoded paths
- Gateway platforms use token locks via `acquire_scoped_lock()` to prevent credential conflicts when multiple profiles target the same platform

---

## 3. Mixture-of-Agents (MoA)

**Source:** `tools/mixture_of_agents_tool.py`

A 2-layer collaborative architecture where multiple frontier LLMs generate diverse perspectives, then an aggregator synthesizes the final output:

```
Layer 1: Reference Models (Parallel)
├── anthropic/claude-opus-4.6
├── google/gemini-3-pro-preview
├── openai/gpt-5.4-pro
└── deepseek/deepseek-v3.2
        ↓ (diverse responses)
Layer 2: Aggregator Model
└── anthropic/claude-opus-4.6 (synthesizes final output)
```

**Configuration constants:**

| Constant               | Value | Purpose                                  |
|------------------------|-------|------------------------------------------|
| `REFERENCE_MODELS`     | 4     | Frontier models for diverse perspectives |
| `AGGREGATOR_MODEL`     | 1     | Claude Opus 4.6 for synthesis            |
| `REFERENCE_TEMPERATURE`| 0.6   | Higher temp for creative diversity       |
| `AGGREGATOR_TEMPERATURE`| 0.4  | Lower temp for focused coherence         |

**Usage:**

```python
from mixture_of_agents_tool import mixture_of_agents_tool

result = await mixture_of_agents_tool(
    user_prompt="Design a scalable microservices architecture..."
)
```

> **Design note:** Temperature differentiation between layers is intentional -- higher temperature on reference models maximizes response diversity, while lower temperature on the aggregator produces coherent synthesis. This is a standard ensemble technique adapted for LLM collaboration.

---

## 4. Subagent Delegation

**Source:** `tools/delegate_tool.py`
**Source:** `cli.py` (`_resolve_turn_agent_config`)

Enables parent agents to spawn child subagents for specialized tasks with state preservation:

```python
# cli.py -- _resolve_turn_agent_config
def _resolve_turn_agent_config(self, user_message: str) -> dict:
    return {
        "max_turns": 90,
        "default_toolsets": ["terminal", "file", "web"],
        "enabled_features": ["tool_use", "memory", "skills"],
    }
```

Key behaviors:

- Saves/restores `_last_resolved_tool_names` global state across child agent runs
- Maintains conversation history and context between parent and child agents
- Child agents inherit the parent's profile but can override toolsets

---

## 5. Multi-Platform Gateway

**Source:** `gateway/run.py` (`GatewayRunner`)
**Source:** `gateway/platforms/` (per-platform adapters)

### 5.1 Supported Platforms

| Platform | Adapter         | Credentials Required          | Connection Method       |
|----------|-----------------|-------------------------------|-------------------------|
| Telegram | `telegram.py`   | Bot token + App token         | Long polling (outbound) |
| Slack    | `slack.py`      | Socket Mode + Multi-workspace | WebSocket (persistent)  |
| Discord  | `discord.py`    | Bot token + Gateway intent    | Event-driven            |
| WhatsApp | `whatsapp.py`   | Business API key              | Webhooks                |
| Signal   | `signal.py`     | Phone number + API key        | REST + Webhooks         |
| Matrix   | `matrix.py`     | Access token + Homeserver     | Matrix SDK              |

Additional adapters exist for: DingTalk, Feishu, Email, SMS, Mattermost, WeCom, Home Assistant, and generic webhooks.

### 5.2 GatewayRunner

The `GatewayRunner` class (line 461 in `gateway/run.py`) manages platform adapters and can distribute them across servers:

```python
class GatewayRunner:
    def __init__(self):
        self.platforms: List[Platform] = []
        self.session_store: SessionStore
        self.background_tasks: Dict[str, asyncio.Task] = {}

    async def start_gateway(self, platforms: List[str] = None):
        for platform_name in platforms or [Platform.TELEGRAM, Platform.SLACK, Platform.DISCORD]:
            adapter = self._create_adapter(platform_name)
            if await adapter.connect():
                self.platforms.append(adapter)
```

---

## 6. Distributed Deployment

### 6.1 Architecture

```
Server 1 (Primary Gateway)       Server 2 (Specialist)        Server 3 (Analytics)
├── Telegram adapter              ├── Discord adapter           ├── Signal adapter
├── Slack adapter                 ├── WhatsApp adapter          └── Matrix adapter
│                                 │
└─────────────────── Shared State Layer (SQLite / Redis) ────────────────────┘
                     Token Locks  ·  Session Store  ·  Tool Registry
```

Each server runs its own `HERMES_HOME` directory and a subset of platform adapters. State synchronization happens through a shared backend.

### 6.2 State Synchronization

**Source:** `gateway/session.py` (`SessionStore`, line 503)
**Source:** `gateway/status.py` (`acquire_scoped_lock`, line 237; `release_scoped_lock`, line 317)

**Token locks** prevent credential conflicts when multiple servers claim the same platform identity:

```python
def acquire_scoped_lock(
    scope: str,           # e.g., "slack-app-token", "telegram-bot"
    identity: str,        # unique credential (token, API key)
    metadata: dict        # pid, platform, timestamps
) -> tuple[bool, dict]
```

**Session store** persists conversation state across platforms:

```python
class SessionStore:
    def build_session_key(
        source: SessionSource,
        group_sessions_per_user: bool,
        thread_sessions_per_user: bool
    ) -> str
```

**Config example** (`~/.hermes/config.yaml`):

```yaml
gateway:
  session_store:
    type: "sqlite"    # or "redis" for distributed deployments
    path: "~/.hermes/state.db"

  platform_adapters:
    telegram:
      enabled: true
      server: "server-1.example.com"
      lock_scope: "telegram-bot"
    slack:
      enabled: true
      server: "server-2.example.com"
      lock_scope: "slack-app-token"
    discord:
      enabled: true
      server: "server-3.example.com"
      lock_scope: "discord-bot"
```

> **Operational note:** For single-server deployments, SQLite is sufficient. For multi-server, switch to Redis so all nodes share lock and session state. Without a shared backend, token locks are local-only and cannot prevent cross-server credential conflicts.

### 6.3 Communication Patterns

**Pattern 1: Parent-Child Subagent Delegation**

A parent agent on Server 1 delegates specialized tasks to child agents on other servers:

```python
async def delegate_task(
    parent_agent: AIAgent,
    child_agents: List[AIAgent],
    task: str
) -> dict:
    code_result = await child_agents[0].chat(f"Analyze this codebase: {task}")
    data_result = await child_agents[1].chat(f"Process and visualize: {task}")

    return {
        "code_analysis": code_result,
        "data_insights": data_result,
        "coordinated": True
    }
```

**Pattern 2: Cross-Platform Message Routing**

Messages arriving on one platform can be routed to a specialist agent on another:

```python
async def _handle_slack_message(event: dict) -> None:
    if event.get("subtype") == "bot_message":
        await parent_agent.update_context(event.get("text"))
    else:
        specialist = _select_specialist(event.get("text"))
        await specialist.process_request(event)
```

### 6.4 Collaboration Feature Matrix

| Feature              | Implementation                                     | Benefit                                           |
|----------------------|----------------------------------------------------|-------------------------------------------------|
| Shared Memory        | `SessionStore` with SQLite/Redis backend           | Consistent conversation history across platforms |
| Load Balancing       | Platform-specific adapters with health checks      | Automatic failover and scaling                   |
| Unified Identity     | Token lock mechanism                               | Single agent identity across distributed servers |
| Context Propagation  | Thread-based session keys                          | Seamless handoff between platforms               |
| Distributed Tooling  | Tool registry synchronized across servers          | Consistent capabilities regardless of server     |

---

## 7. Configuration Reference

### 7.1 Agent Configuration (`config.yaml`)

```yaml
agent:
  max_turns: 90
  gateway_timeout: 300
  mixture_of_agents:
    enabled: true
    reference_models:
      - anthropic/claude-opus-4.6
      - google/gemini-3-pro-preview
      - openai/gpt-5.4-pro
    aggregator_model: anthropic/claude-opus-4.6

profiles:
  - name: developer
    tools: [terminal, code, web]
  - name: operations
    tools: [terminal, monitoring, automation]
```

### 7.2 vLLM Inference Server

For self-hosted model serving (used as MoA reference models or standalone):

| Setting                 | Value                                      |
|-------------------------|--------------------------------------------|
| Endpoint                | `http://0.0.0.0:10001/v1`                  |
| Model                   | `Qwen/Qwen3-Next-80B-A3B-Thinking-FP8`    |
| Max context             | 1,048,576 tokens (1M)                      |
| KV cache dtype          | FP8                                        |
| GPU memory utilization  | 85%                                        |
| Tool calling            | Enabled (`qwen3_xml` parser)               |
| Eager mode              | Enabled (no CUDA graphs -- required for 1M context KV cache headroom) |

---

## 8. Deployment

### 8.1 Quick Start

```bash
# Create profile-specific agent instance
hermes -p developer setup

# Install messaging gateways
hermes gateway install --platforms telegram,slack,discord

# Enable mixture-of-agents
export HERMES_MOA_ENABLED=true

# Run with all available agents
hermes run --all-agents
```

### 8.2 Multi-Server Deployment

```bash
# Server 1: Primary Gateway (Telegram + Slack)
export HERMES_HOME=~/.hermes/server1
export TELEGRAM_BOT_TOKEN="<token>"
export SLACK_BOT_TOKEN="xoxb-<token>"
export SLACK_APP_TOKEN="xapp-<token>"
hermes gateway --platforms telegram,slack --server primary

# Server 2: Specialist Agent (Code + DevOps)
export HERMES_HOME=~/.hermes/server2
export CODE_ANALYSIS_MODEL="anthropic/claude-opus-4.6"
export DEVOPS_INTEGRATIONS="github,gitlab,jenkins"
hermes gateway --platforms discord --server specialized

# Server 3: Analytics Agent
export HERMES_HOME=~/.hermes/server3
export DATA_ANALYTICS_MODEL="google/gemini-3-pro-preview"
export ANALYTICS_SOURCE="postgresql,redis"
hermes gateway --platforms signal,matrix --server analytics
```

### 8.3 Systemd Service Management

All services run under systemd (no Docker, no venv):

```bash
systemctl start hermes-gateway
systemctl enable hermes-gateway
```

### 8.4 Operations Commands

```bash
# Check distributed agent status
hermes gateway status

# Synchronize configuration across servers
hermes gateway sync

# Centralized deployment (all platforms, single server)
hermes gateway --platforms telegram,slack,discord,whatsapp,signal,matrix
```

---

## 9. Source File Index

| Component             | File                              | Key Symbol                   |
|-----------------------|-----------------------------------|------------------------------|
| CLI / Profile System  | `hermes_cli/main.py`              | `_apply_profile_override()`  |
| Configuration         | `hermes_cli/config.py`            | `get_hermes_home()`          |
| MoA Tool              | `tools/mixture_of_agents_tool.py` | `mixture_of_agents_tool()`   |
| Delegate Tool         | `tools/delegate_tool.py`          | `delegate_tool()`            |
| Gateway Runner        | `gateway/run.py:461`              | `GatewayRunner`              |
| Session Store         | `gateway/session.py:503`          | `SessionStore`               |
| Token Locks           | `gateway/status.py:237`           | `acquire_scoped_lock()`      |
| Platform: Telegram    | `gateway/platforms/telegram.py`   | --                           |
| Platform: Slack       | `gateway/platforms/slack.py`      | --                           |
| Platform: Discord     | `gateway/platforms/discord.py`    | --                           |
| Platform: WhatsApp    | `gateway/platforms/whatsapp.py`   | --                           |
| Platform: Signal      | `gateway/platforms/signal.py`     | --                           |
| Platform: Matrix      | `gateway/platforms/matrix.py`     | --                           |
