"""HTTP helpers of the anonymization API tests: sessions and their SSE streams."""

import json
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

BASE = "/anonymizations/sessions"

Frame = tuple[str, dict[str, Any]]


def create(
    client: TestClient,
    path: Path,
    name: str | None = None,
    mime: str = "application/pdf",
    mode: str = "accurate",
) -> dict[str, Any]:
    """Upload ``path`` as a new session (201) and return the session."""
    with path.open("rb") as f:
        response = client.post(
            BASE, files={"file": (name or path.name, f, mime)}, data={"mode": mode}
        )
    assert response.status_code == 201, response.text
    return response.json()


def frames(text: str) -> list[Frame]:
    """The ``(event, data)`` frames of an SSE body; keep-alive comments are skipped."""
    out: list[Frame] = []
    for block in text.split("\n\n"):
        lines = [line for line in block.splitlines() if not line.startswith(":")]
        if not lines:
            continue
        event = next(line[7:] for line in lines if line.startswith("event: "))
        data = next(line[6:] for line in lines if line.startswith("data: "))
        out.append((event, json.loads(data)))
    return out


def anonymize(client: TestClient, session_id: str) -> list[Frame]:
    """Run the anonymization of a session and return every frame of its stream."""
    response = client.post(f"{BASE}/{session_id}/anonymize/stream")
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("text/event-stream")
    return frames(response.text)


def edit(client: TestClient, session_id: str, payload: dict[str, Any]) -> list[Frame]:
    """Apply manual corrections and return every frame of the stream."""
    response = client.post(f"{BASE}/{session_id}/edits/stream", json=payload)
    assert response.status_code == 200, response.text
    return frames(response.text)


def run(client: TestClient, path: Path, **kwargs: Any) -> dict[str, Any]:
    """Create a session, anonymize it and return its final state (``GET``)."""
    session_id = create(client, path, **kwargs)["session_id"]
    anonymize(client, session_id)
    return client.get(f"{BASE}/{session_id}").json()
