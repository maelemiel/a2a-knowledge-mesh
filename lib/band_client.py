"""Shared Band REST API client.
Wraps Band's HTTP endpoints for rooms, participants, messages."""

import httpx

BAND_REST = "https://app.band.ai"

class BandClient:
    def __init__(self, api_key: str):
        self._api_key = api_key
        self._headers = {"X-API-Key": api_key, "Content-Type": "application/json"}
        self._client = httpx.Client(base_url=BAND_REST, headers=self._headers)

    # ── Rooms ──

    def create_room(self, title: str) -> dict:
        r = self._client.post("/api/v1/agent/chats", json={"chat": {"title": title}})
        r.raise_for_status()
        return r.json()["data"]

    def get_room_messages(self, room_id: str) -> list[dict]:
        r = self._client.get(f"/api/v1/agent/chats/{room_id}/messages")
        r.raise_for_status()
        return r.json().get("data", [])

    # ── Participants ──

    def add_participant(self, room_id: str, participant_id: str, role: str = "member") -> dict:
        r = self._client.post(
            f"/api/v1/agent/chats/{room_id}/participants",
            json={"participant": {"participant_id": participant_id, "role": role}},
        )
        r.raise_for_status()
        return r.json()["data"]

    # ── Messages ──

    def send_message(self, room_id: str, content: str, mention_ids: list[str]) -> dict:
        r = self._client.post(
            f"/api/v1/agent/chats/{room_id}/messages",
            json={
                "message": {
                    "content": content,
                    "mentions": [{"id": uid} for uid in mention_ids],
                }
            },
        )
        r.raise_for_status()
        return r.json().get("data", {})

    # ── Info ──

    def get_peers(self) -> list[dict]:
        r = self._client.get("/api/v1/agent/peers")
        r.raise_for_status()
        return r.json().get("data", [])

    def get_me(self) -> dict:
        r = self._client.get("/api/v1/agent/me")
        r.raise_for_status()
        return r.json()["data"]

    def close(self):
        self._client.close()
