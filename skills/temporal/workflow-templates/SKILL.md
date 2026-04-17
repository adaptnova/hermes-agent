---
name: novamesh-workflow-templates
description: Define and invoke reusable Temporal workflow templates from chat. Templates are YAML files that map natural language commands to pre-configured workflow submissions.
version: 1.0.0
author: KK (Kenon)
license: MIT
dependencies: [temporalio>=1.9.0, pyyaml]
metadata:
  hermes:
    tags: [Temporal, Workflow, Templates, Automation, Pipeline, NovaMesh]

---

# NovaMesh Workflow Templates

Define reusable workflow templates in YAML and invoke them with natural language from chat.

## When to Use This Skill

Use when you want to:
- **Define repeatable workflow patterns** — "deploy staging" always runs the same pipeline
- **Create one-command automations** — "run daily briefing" submits a pre-configured DAG dispatch
- **Share workflow patterns across agents** — templates are YAML files any agent can use
- **Build domain-specific commands** — "security audit" triggers a multi-agent security review pipeline

## Template Format

Templates live in `~/.hermes/workflow-templates/` (or a project-local `.hermes/workflow-templates/`).

```yaml
# ~/.hermes/workflow-templates/deploy-staging.yaml
name: deploy-staging
description: Deploy current branch to staging environment
trigger_phrases:
  - "deploy staging"
  - "push to staging"
  - "staging deploy"
workflow_type: PipelineWorkflow
input:
  name: "Deploy to Staging"
  company_id: "92d5b79c-ca3c-4d30-b730-15ae0b00227c"
  stages:
    - name: "Build & Test"
      workflow_type: AgentWorkLoopWorkflow
      input_template:
        company_id: "92d5b79c-ca3c-4d30-b730-15ae0b00227c"
        agent_id: "${AGENT_ID}"
        issue_id: "${ISSUE_ID}"
        max_run_seconds: 300
      on_failure: stop
    - name: "Deploy"
      workflow_type: AgentWorkLoopWorkflow
      input_template:
        company_id: "92d5b79c-ca3c-4d30-b730-15ae0b00227c"
        agent_id: "${AGENT_ID}"
        issue_id: "${ISSUE_ID}"
        max_run_seconds: 300
      on_failure: stop
    - name: "Verify"
      workflow_type: AgentWorkLoopWorkflow
      input_template:
        company_id: "92d5b79c-ca3c-4d30-b730-15ae0b00227c"
        agent_id: "${AGENT_ID}"
        issue_id: "${ISSUE_ID}"
        max_run_seconds: 300
      on_failure: stop
```

## Built-in Templates

### daily-briefing
Runs a multi-agent dispatch to produce a daily briefing.
```
Submit daily briefing
```

### fleet-health-report
Runs a health check and posts results to musketeers.
```
Run fleet health report
```

### security-review
Runs a DAG dispatch with security-focused decomposition.
```
Run security review on [project description]
```

### multi-domain-project
Generic DAG dispatch with dependency ordering.
```
Start multi-domain project: [description]
```

## Variable Substitution

Templates support `${VAR}` substitution:
- `${AGENT_ID}` — current agent's PC ID
- `${COMPANY_ID}` — current company ID
- `${ISSUE_ID}` — current issue ID (if in a task context)
- `${TIMESTAMP}` — current ISO timestamp
- `${USER}` — requesting user

## Creating Custom Templates

1. Create a YAML file in `~/.hermes/workflow-templates/`
2. Define `name`, `trigger_phrases`, `workflow_type`, and `input`
3. The template is immediately available — no restart needed

## Implementation Notes

When a user says something matching a trigger phrase:
1. Load the template YAML
2. Substitute variables
3. Call `temporal_submit` tool with the appropriate action
4. Return the workflow ID to the user

This bridges natural language commands to structured Temporal workflow submissions.
