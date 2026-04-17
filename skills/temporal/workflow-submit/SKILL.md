---
name: novamesh-temporal-submit
description: Submit, monitor, and query NovaMesh Temporal workflows from chat. Dispatch multi-agent projects, single-agent work loops, health checks, and DAG-orchestrated builds — all through natural language.
version: 1.0.0
author: KK (Kenon)
license: MIT
dependencies: [temporalio>=1.9.0]
metadata:
  hermes:
    tags: [Temporal, Workflow, Orchestration, NovaMesh, Fleet, Multi-Agent, DAG, Dispatch]

---

# NovaMesh Temporal Workflow Submission

Submit and manage durable Temporal workflows from chat. Every workflow runs on the NovaMesh Temporal stack (server 1.29.1, Postgres persistence, OTEL traced) and survives process death.

## When to Use This Skill

Use when you need to:
- **Dispatch a multi-agent project** — decompose work across domains and let specialist agents execute in parallel
- **Assign a single issue to an agent** — durable claim→wake→poll→QA→done lifecycle
- **Run a DAG-orchestrated project** — sub-tasks with dependency edges, topological execution order
- **Trigger an ad-hoc health check** — probe all fleet services immediately
- **Query workflow state** — check what's running, what completed, what failed
- **Manage the fleet semaphore** — see who holds vLLM inference slots

## Available Workflows

### 1. MultiAgentDispatchWorkflow
Decomposes a project via the Router, creates PC issues, dispatches child workflows to specialist agents with windowed parallelism.

**Submit from chat:**
```
Submit a multi-agent dispatch workflow:
  Title: "Build health check dashboard"
  Description: "Simple status page showing vLLM, Temporal, NATS, Paperclip health. Backend endpoint + frontend display. No auth, no persistence."
  Company: teamadapt (or company ID)
  Max sub-tasks: 5
  Parallelism: 3
```

**Temporal details:**
- Workflow: `MultiAgentDispatchWorkflow`
- Task queue: `novamesh`
- Input: `ProjectSpec(company_id, project_title, project_description, max_sub_tasks, sub_task_parallelism)`
- Output: `ProjectResult(outcome, parent_issue_id, sub_task_results, aggregate_qa_notes)`

### 2. DAGDispatchWorkflow
Same as MultiAgentDispatch but with dependency-aware scheduling. Sub-tasks that depend on others wait until dependencies complete. Failed dependencies cause dependents to be skipped.

**Submit from chat:**
```
Submit a DAG dispatch workflow:
  Title: "Deploy monitoring stack"
  Description: "Set up OTEL collector, wire Temporal traces, add Grafana dashboards, configure alerting. Infra before deployment, deployment before observability."
  Company: teamadapt
  Max sub-tasks: 6
  Parallelism: 3
```

**Temporal details:**
- Workflow: `DAGDispatchWorkflow`
- Task queue: `novamesh`
- Input: `DAGProjectSpec(company_id, project_title, project_description, max_sub_tasks, sub_task_parallelism)`
- Output: `ProjectResult(outcome, parent_issue_id, sub_task_results, aggregate_qa_notes, execution_order)`
- Edges are auto-detected from domain ordering heuristics (infra→devops→QA, etc.)

### 3. AgentWorkLoopWorkflow
Assigns a single PC issue to a specific agent with durable lifecycle: claim → wake → poll → read result → QA verify → done/reopen.

**Submit from chat:**
```
Submit an agent work loop:
  Company: teamadapt
  Agent: kenon (or agent ID)
  Issue: TEA-166 (or issue ID)
  Max run time: 600s
  Max retries: 2
```

**Temporal details:**
- Workflow: `AgentWorkLoopWorkflow`
- Task queue: `novamesh`
- Input: `WorkItem(company_id, agent_id, issue_id, max_run_seconds, max_retries)`
- Output: `WorkResult(outcome, issue_id, qa_verdict, qa_notes)`

### 4. FleetHealthCheckWorkflow
One-shot health probe of all fleet services. Normally runs on a 60s schedule, but can be triggered ad-hoc.

**Submit from chat:**
```
Run a fleet health check now
```

**Temporal details:**
- Workflow: `FleetHealthCheckWorkflow`
- Task queue: `novamesh`
- Input: `HealthCheckConfig(check_vllm, check_temporal, check_dragonfly, check_nats, check_paperclip)`
- Output: `{health: {services, all_healthy}, transitions: [...]}`
- State persisted to Dragonfly at key `novamesh:fleet:health`

### 5. FleetSemaphoreWorkflow (query only)
Long-running semaphore for shared resources. Already running as `fleet-semaphore-vllm`.

**Query from chat:**
```
Show vLLM semaphore state
```

Returns: active grants, queue depth, who's holding, who's waiting.

## Querying Workflows

### Check workflow status
```
What's the status of workflow dispatch-smoke-abc123?
```

### List recent workflows
```
Show me recent Temporal workflows
```

### Get workflow result
```
Get the result of workflow dag-deploy-monitoring-xyz
```

## Connection Details

- **Temporal server**: localhost:7233
- **Namespace**: novamesh
- **Task queue**: novamesh
- **HTTP API**: localhost:7243 (for UI links)

## Implementation Notes

When submitting workflows from chat, use the Temporal Python SDK:

```python
from temporalio.client import Client

client = await Client.connect("localhost:7233", namespace="novamesh")

# Submit
handle = await client.start_workflow(
    "MultiAgentDispatchWorkflow",
    spec,
    id=f"dispatch-{uuid4().hex[:8]}",
    task_queue="novamesh",
)

# Query
result = await handle.result()

# Describe
desc = await handle.describe()
print(desc.status)
```

For the semaphore, use queries:
```python
handle = client.get_workflow_handle("fleet-semaphore-vllm")
state = await handle.query("semaphore_state")
```

## Company ID Reference

| Company | ID |
|---------|-----|
| TeamADAPT | 92d5b79c-ca3c-4d30-b730-15ae0b00227c |
| Agency | 79fc9cb0-1362-4ae8-84b5-77685ad897fe |
| NovaMesh | 59a2f84f-8522-4a79-ad25-d5cc3b864c60 |
| BlitzKernels | f5a1c989-c74b-4d61-9707-e37aa795b902 |
