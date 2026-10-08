import uuid

import run_mechanism_gate_bc2_v1 as r1  # installs the frozen A2A compatibility aliases
from a2a.types import SendMessageRequest, StreamResponse
from google.protobuf.json_format import MessageToDict

base = r1.base
PRESENTATION_KEYS = ("artifact_texts", "status_message_texts")


async def selective_delegate(app, card, task_description, audit):
    """Return only nonempty Artifact/status-message text arrays to the model."""
    http = base.httpx.AsyncClient(
        transport=base.httpx.ASGITransport(app=app),
        base_url="http://a2a.test",
    )
    client = base.ClientFactory(base.ClientConfig(streaming=False, httpx_client=http)).create(card)
    request_message = base.Message(
        role=base.Role.ROLE_USER,
        message_id=str(uuid.uuid4()),
        parts=[base.Part(text=task_description)],
    )
    try:
        events = [event async for event in client.send_message(SendMessageRequest(message=request_message))]
        task_event = next(
            (event.task for event in events if isinstance(event, StreamResponse) and event.HasField("task")),
            None,
        )
        if task_event is None:
            audit.append({"task_received": False, "task_completed": False, "artifact_count": 0})
            return {"artifact_texts": [], "status_message_texts": []}, None

        artifact_texts = [
            part.text
            for artifact in task_event.artifacts
            for part in artifact.parts
            if part.text
        ]
        status_message = task_event.status.message if task_event.status.HasField("message") else None
        status_message_texts = [
            part.text for part in status_message.parts if part.text
        ] if status_message else []
        task_completed = task_event.status.state == base.TaskState.TASK_STATE_COMPLETED
        audit.append({
            "task_received": True,
            "task_completed": task_completed,
            "task_state": MessageToDict(task_event.status, preserving_proto_field_name=False).get("state"),
            "artifact_count": len(artifact_texts),
            "status_message_text_count": len(status_message_texts),
        })
        visible = {"artifact_texts": artifact_texts, "status_message_texts": status_message_texts}
        if tuple(visible) != PRESENTATION_KEYS:
            raise RuntimeError("selective A2A presentation emitted an unfrozen field")
        return visible, "\n".join(artifact_texts) if artifact_texts and task_completed else None
    finally:
        await client.close()
        await http.aclose()
