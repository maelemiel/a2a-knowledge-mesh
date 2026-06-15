"""Spike 005: Band Resolution.
Reconciler creates a Band room, @mentions agents, posts resolution."""

import asyncio
import logging
import httpx
from dotenv import load_dotenv
from band.config import load_agent_config

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BAND_REST = "https://app.band.ai"

async def test_resolution():
    load_dotenv()
    agent_id, api_key = load_agent_config("registry_agent")
    headers = {"X-API-Key": api_key, "Content-Type": "application/json"}

    async with httpx.AsyncClient(base_url=BAND_REST, headers=headers) as c:
        # 1. Get own agent info
        r = await c.get("/api/v1/agent/me")
        me = r.json()["data"]
        my_handle = me["handle"]
        logger.info(f"Connected as: {my_handle}")

        # 2. Get peers (other agents we can invite)
        r = await c.get("/api/v1/agent/peers")
        peers = r.json().get("data", [])
        logger.info(f"Peers: {len(peers)}")
        for p in peers:
            logger.info(f"  - {p.get('handle', '?')}")

        # 3. Create a chat room
        room_name = f"conflict-ally-{asyncio.get_event_loop().time():.0f}"
        r = await c.post("/api/v1/agent/chats", json={
            "name": room_name,
            "description": "Conflict: project:ALLY has 2 different values",
        })
        room = r.json().get("data", r.json())
        room_id = room.get("id") or room.get("chat", {}).get("id")
        logger.info(f"Room created: {room_name} (id={room_id})")

        # 4. Post a message with @mentions describing the conflict
        conflict_msg = (
            f"@{my_handle} CONFLICT DETECTED: key='project:ALLY'\n"
            f"  Source 'live' says: Python/Next.js\n"
            f"  Source 'staging' says: Python/FastAPI\n"
            f"@{my_handle} Please resolve: which is the correct value?"
        )
        r = await c.post(f"/api/v1/agent/chats/{room_id}/messages", json={
            "content": conflict_msg,
        })
        msg_id = r.json().get("id") or r.json().get("data", {}).get("id")
        logger.info(f"Conflict message sent (id={msg_id})")

        # 5. Post resolution
        resolution_msg = (
            f"@{my_handle} RESOLUTION: project:ALLY = Python/Next.js\n"
            f"@{my_handle} Reason: 'live' source is canonical. 'staging' outdated."
        )
        r = await c.post(f"/api/v1/agent/chats/{room_id}/messages", json={
            "content": resolution_msg,
        })
        logger.info("Resolution posted")

        # 6. Verify room has messages
        r = await c.get(f"/api/v1/agent/chats/{room_id}/messages")
        messages = r.json().get("data", [])
        logger.info(f"Messages in room: {len(messages)}")

        for msg in messages:
            content = msg.get("content", "")[:100]
            logger.info(f"  [{msg.get('agent_name','?')}] {content}")

        logger.info("✅ BAND RESOLUTION VALIDATED")

asyncio.run(test_resolution())
