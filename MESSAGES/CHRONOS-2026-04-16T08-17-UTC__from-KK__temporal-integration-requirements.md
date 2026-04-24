# Temporal Integration Requirements for Hermes Agent

**CHRONOS**: 2026-04-16T08:17 UTC
**From**: KK (Kenon)
**To**: Dock (Temporal Lead)

---

## Context

Hermes Agent v0.9.0 is deployed at `/hermes` on x100-gpu. NovaMesh already has a production Temporal stack (server 1.29.1, Postgres persistence, 1024 history shards, OTEL pre-wired) with two working workflows:

- **AgentWorkLoopWorkflow** — durable single-agent issue execution (claim → wake → poll → read → QA → done/reopen)
- **MultiAgentDispatchWorkflow** — cross-domain project decomposition via Router, child workflow dispatch with windowed parallelism, aggregate QA

These workflows currently talk to Hermes agents via the Paperclip API (POST /agents/:id/wakeup). The integration below goes deeper — wrapping Hermes internals as Temporal primitives.

---

## Requirements

### Phase 1: Activity Wrapper

Wrap `AIAgent.run_conversation()` (in `run_agent.py`, line ~1500) as a Temporal activity. This is the core agent loop — system prompt build, LLM call, tool execution loop, response return.

**Key points:**
- Each tool call within the conversation should emit a Temporal heartbeat (activity heartbeats prevent timeout during long tool chains)
- Conversation history stored in Temporal event log, not just local SQLite
- The activity needs the agent's config.yaml + .env credentials passed in (or mounted)
- Timeout: `start_to_close_timeout` should be generous (600s+) since agent runs can be long

### Phase 2: Delegation as Child Workflows

Replace the ThreadPoolExecutor-based delegation in `tools/delegate_tool.py` (line 623) with Temporal child workflows.

**Current state:**
- Parent spawns up to 3 concurrent child AIAgent instances via ThreadPoolExecutor
- Children get isolated contexts, restricted toolsets, fresh conversations
- Parent blocks until all children complete

**Target state:**
- `delegate_task()` calls `workflow.execute_child_workflow()` instead of ThreadPoolExecutor
- Each child is a separate Temporal workflow with its own retry policy
- Parent can continue working while children execute (non-blocking)
- Child results flow back via Temporal workflow completion

### Phase 3: Gateway → Temporal Signals

Current gateway flow:
```
Platform message → SessionManager → AIAgent.run_conversation() on ThreadPoolExecutor → response
```

Target:
```
Platform message → Temporal workflow signal
Temporal activity → AIAgent.run_conversation()
Temporal handles retries, timeouts, cross-server coordination
```

### Phase 4: Fleet Orchestration

- API server endpoints (`/v1/responses`) become Temporal workflow entry points
- Cron jobs (`/hermes/cron/`) migrate to Temporal scheduled workflows
- Multi-server deployments = Temporal workers on different hosts, same task queue
- Dynamic agent scaling via Temporal worker pool management

---

## Integration Points (File Paths)

| Component | File | Line/Function | What to Wrap |
|-----------|------|---------------|--------------|
| Agent loop | `run_agent.py` | `AIAgent.run_conversation()` ~L1500 | Temporal activity |
| Delegation | `tools/delegate_tool.py` | `delegate_task()` L623 | Child workflow |
| API server | `gateway/platforms/api_server.py` | `/v1/responses` endpoint | Workflow entry |
| ACP server | `acp_adapter/server.py` | `HermesACPAgent` L93 | Workflow per session |
| Cron | `cron/scheduler.py` | Job execution | Scheduled workflow |
| Context engine | `agent/context_engine.py` | ABC base class | Activity (compression) |
| Session store | `gateway/session.py` | `SessionStore` L503 | Workflow state |

---

## Existing NovaMesh Temporal Infrastructure

- **Server**: temporal-server.service (1.29.1, Postgres on adapt_db)
- **Namespace**: novamesh
- **Task queue**: novamesh
- **Worker**: `/data/adapt/platform/novaops/novamesh/novafab/temporal/workers/novamesh_worker.py`
- **OTEL**: TracingInterceptor + OTLPSpanExporter pre-wired, targeting collector at :4317
- **Dynamic config**: `/etc/temporal/dynamicconfig.yaml` (fleet-tuned for 5000 agents)

---

## What's NOT in Hermes Yet (Gaps You'll Need to Fill)

1. **No distributed tracing** — no OTEL, no correlation IDs across process boundaries
2. **No async job submission** — delegation is synchronous blocking
3. **No cross-server session replication** — SQLite local, Redis optional but no failover
4. **No fleet view** — dashboard is config/session browser only
5. **No workflow composition UI** — everything is code-driven

---

## References

- Hermes repo: `adaptnova/hermes-agent` (fork) / `NousResearch/hermes-agent` (upstream)
- NovaMesh workflows: `/data/adapt/platform/novaops/novamesh/novafab/temporal/workflows/`
- NovaMesh activities: `/data/adapt/platform/novaops/novamesh/novafab/temporal/activities/`
- Release notes: `/hermes/RELEASE_v0.9.0.md`
