from pathlib import Path

import pytest

from par.ingress import get_event
from par.supervisor import adapt_supervisor_intent, submit_supervisor_intent


def test_provider_replacement_produces_equivalent_bounded_intent():
    proposal = {
        "kind": "request_action",
        "payload": {"task_id": "task-1"},
        "requested_action": {"action": "continue", "subject": "task-1"},
    }

    hermes = adapt_supervisor_intent(supervisor_id="hermes", intent=proposal)
    other = adapt_supervisor_intent(supervisor_id="other-supervisor", intent=proposal)

    assert hermes == other


def test_normalized_intent_is_deeply_immutable_and_detached():
    proposal = {
        "kind": "request_action",
        "payload": {"context": {"tags": ["safe"]}},
        "requested_action": {"action": "continue", "metadata": {"mode": "bounded"}},
    }
    normalized = adapt_supervisor_intent(supervisor_id="fixture", intent=proposal)

    with pytest.raises(TypeError):
        normalized.payload["new"] = "value"
    with pytest.raises(TypeError):
        normalized.payload["context"]["new"] = "value"

    proposal["payload"]["context"]["tags"].append("mutated")
    proposal["requested_action"]["metadata"]["mode"] = "unbounded"

    assert normalized.payload["context"]["tags"] == ("safe",)
    assert normalized.requested_action["metadata"]["mode"] == "bounded"


def test_write_like_intent_uses_durable_ingress_without_authority(tmp_path: Path):
    db = tmp_path / "runtime.db"
    event = submit_supervisor_intent(
        supervisor_id="fixture-a",
        idempotency_key="supervisor:1",
        intent={
            "kind": "request_action",
            "payload": {"task_id": "task-1"},
            "requested_action": {"action": "continue", "subject": "task-1"},
        },
        path=db,
    )

    persisted = get_event(event["id"], path=db)
    assert persisted is not None
    assert persisted["source"] == "api"
    assert persisted["kind"] == "supervisor.request_action"
    assert persisted["authority"] == {}
    assert persisted["authority_is_grant"] is False
    assert persisted["requested_action"] == {
        "action": "continue",
        "subject": "task-1",
    }


@pytest.mark.parametrize(
    "intent",
    [
        {"kind": "execute", "requested_action": {"action": "run"}},
        {"kind": "request_action"},
        {
            "kind": "request_action",
            "requested_action": {"action": "run", "credentials": "ambient"},
        },
        {
            "kind": "request_action",
            "requested_action": {"action": "run", "max_cost_usd": 1},
        },
    ],
)
def test_unsupported_or_authority_escalating_intent_fails_closed(intent):
    with pytest.raises(ValueError):
        adapt_supervisor_intent(supervisor_id="fixture", intent=intent)
