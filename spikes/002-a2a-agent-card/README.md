# 002: A2A Agent Card

Valide qu'un serveur HTTP peut exposer une **Agent Card** A2A et répondre à des requêtes JSON-RPC.

## Question

> Given a Python HTTP server, when a client fetches `/.well-known/agent-card.json`, does it return a valid A2A Agent Card, and can it handle `message/send` via JSON-RPC 2.0?

## Standard A2A

- Agent Card : `/.well-known/agent-card.json`
- JSON-RPC 2.0 sur HTTP `POST`
- Méthodes : `message/send`, `message/stream`
- SDK Python dispo : `pip install a2a-python`

## Test

```bash
cd spikes/002-a2a-agent-card
uv init
uv add a2a-python fastapi uvicorn

# Start server
uv run python server.py

# In another terminal:
curl http://localhost:8765/.well-known/agent-card.json
curl -X POST http://localhost:8765/a2a \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"message/send","params":{"message":{"role":"user","parts":[{"type":"text","text":"hello"}]}}}'
```

## Verdict

TBD
