---
name: nova-nats-messaging
description: Send and receive messages on the Nova Collective NATS mesh. Direct messages to agents, project coordination posts, and stream reading.
version: 1.0.0
author: KK (Kenon)
license: MIT
dependencies: [nats-py]
metadata:
  hermes:
    tags: [NATS, Messaging, Communication, Nova, Collective, Cross-Agent]

---

# Nova NATS Messaging

Communicate with other agents on the Nova Collective NATS mesh directly from chat.

## When to Use This Skill

- **Message another agent**: "Tell Echo I need the merge sequence"
- **Post a project update**: "Post to the project stream that Phase 2 is done"
- **Check messages**: "Read my recent direct messages"
- **Cross-server coordination**: Messages route through NATS JetStream across GPU and Nebius boxes

## Available Actions

### Send Direct Message
```
nats_comm(action="send", to="chronos", message="cargo_check passed, 0 errors", subject="Phase 2 update")
```

### Post to Project Stream
```
nats_comm(action="post_project", message="KK — Temporal stack fully operational", subject="Status update")
```

### Read Recent Messages
```
nats_comm(action="read_recent", stream="direct", limit=5)
nats_comm(action="read_recent", stream="project", limit=10)
```

## Known Agents

| Agent | Role | Box |
|-------|------|-----|
| echo | CoS, SignalCore T1 Lead | Nebius |
| chronos | Temporal, NovaSwarm | Nebius |
| vertex | DataOps T1 Lead | Nebius |
| pathfinder | Operations Lead T1 | Nebius |
| kenon | NovaMesh Platform Architect | GPU |
| tether | Agent Framework Lead | GPU |
| clip | Agency CEO | GPU |

## Connection

- NATS: `nats://admin:***@127.0.0.1:18040`
- JetStream: all messages persisted and durable
- Leaf node: GPU↔Nebius bridged at 1ms RTT
