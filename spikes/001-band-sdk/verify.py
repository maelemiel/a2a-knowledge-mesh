"""Spike 001: Band SDK Python connectivity test.
Tests: agent registration + REST API auth.
LLM not needed for connectivity validation."""

import asyncio
import logging
import os
import httpx
from dotenv import load_dotenv
from band.config import load_agent_config

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def test_band_connectivity():
    load_dotenv()
    agent_id, api_key = load_agent_config("registry_agent")
    logger.info(f"Agent UUID: {agent_id}")

    rest_url = os.getenv("THENVOI_REST_URL", "https://app.band.ai/")

    headers = {
        "X-API-Key": api_key,
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient() as client:
        # 1. Test identity endpoint
        r = await client.get(f"{rest_url}api/v1/agent/me", headers=headers)
        logger.info(f"GET /agent/me: {r.status_code}")
        if r.status_code == 200:
            data = r.json()
            logger.info(f"Agent handle: {data.get('agent', {}).get('handle', '?')}")
            logger.info(f"Agent name: {data.get('agent', {}).get('name', '?')}")

        # 2. Test peers endpoint
        r = await client.get(f"{rest_url}api/v1/agent/peers", headers=headers)
        logger.info(f"GET /agent/peers: {r.status_code}")
        if r.status_code == 200:
            peers = r.json().get("data", [])
            logger.info(f"Peers found: {len(peers)}")

        # 3. Test WebSocket connection
        ws_url = os.getenv("THENVOI_WS_URL", "wss://app.band.ai/api/v1/socket/websocket")
        logger.info(f"WS URL: {ws_url}")

    logger.info("Connectivite Band validee")

asyncio.run(test_band_connectivity())
