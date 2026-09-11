from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from par.db import init_db
from par.github_events import ingest_github_pr_event


def _object(value: Any, *, name: str) -> dict[str, Any]:
    """Return a JSON object or fail closed with a field-specific error."""
    if not isinstance(value, dict):
        raise ValueError(f"{name} must be a JSON object")
    return value


def _positive_int(value: Any, *, name: str) -> int:
    """Validate an identifier represented as a positive JSON integer."""
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{name} must be a positive integer")
    return value


def _linked_pull_requests(value: Any, *, name: str) -> list[int]:
    if not isinstance(value, list) or not value:
        raise ValueError(f"{name} must contain at least one pull request")
    numbers: list[int] = []
    seen: set[int] = set()
    for index, item in enumerate(value):
        number = _positive_int(
            _object(item, name=f"{name}[{index}]").get("number"),
            name="pull_request.number",
        )
        if number not in seen:
            seen.add(number)
            numbers.append(number)
    return numbers


def _event_identities(event_name: str, payload: dict[str, Any]) -> list[tuple[str, int, str]]:
    """Derive semantic per-PR metadata for one supported GitHub Actions event."""
    repository = _object(payload.get("repository"), name="repository").get("full_name")
    if not isinstance(repository, str) or not repository.strip():
        raise ValueError("repository.full_name must be a non-empty string")
    repository = repository.strip()

    if event_name in {"pull_request", "pull_request_review"}:
        pull_request = _object(payload.get("pull_request"), name="pull_request")
        number = _positive_int(pull_request.get("number"), name="pull_request.number")
        action = payload.get("action")
        if not isinstance(action, str) or not action.strip():
            raise ValueError("action must be a non-empty string")
        action = action.strip()
        if event_name == "pull_request_review":
            review = _object(payload.get("review"), name="review")
            stable = f"review:{_positive_int(review.get('id'), name='review.id')}:action:{action}"
        else:
            head = _object(pull_request.get("head"), name="pull_request.head")
            sha = head.get("sha")
            if not isinstance(sha, str) or not sha.strip():
                raise ValueError("pull_request.head.sha must be a non-empty string")
            stable = f"head:{sha.strip()}:action:{action}"
        return [(repository, number, stable)]

    if event_name == "check_run":
        check_run = _object(payload.get("check_run"), name="check_run")
        check_id = _positive_int(check_run.get("id"), name="check_run.id")
        numbers = _linked_pull_requests(check_run.get("pull_requests"), name="check_run.pull_requests")
        return [(repository, number, f"check:{check_id}:pr:{number}") for number in numbers]

    if event_name == "workflow_run":
        workflow_run = _object(payload.get("workflow_run"), name="workflow_run")
        run_id = _positive_int(workflow_run.get("id"), name="workflow_run.id")
        numbers = _linked_pull_requests(workflow_run.get("pull_requests"), name="workflow_run.pull_requests")
        return [(repository, number, f"workflow-run:{run_id}:pr:{number}") for number in numbers]

    raise ValueError(f"unsupported GitHub event: {event_name}")


def main() -> None:
    """Ingest one immutable Actions delivery into durable runtime ingress records."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--event-name", required=True)
    parser.add_argument("--event-path", type=Path, required=True)
    parser.add_argument("--delivery-id", required=True)
    parser.add_argument("--db", type=Path, default=Path(".par/runtime.db"))
    args = parser.parse_args()

    payload = json.loads(args.event_path.read_text())
    payload = _object(payload, name="event payload")
    identities = _event_identities(args.event_name, payload)

    init_db(args.db)
    results = []
    for repository, pull_request_number, semantic_identity in identities:
        delivery_id = f"actions:{args.delivery_id}:pr:{pull_request_number}"
        event = ingest_github_pr_event(
            event_name=args.event_name,
            delivery_id=delivery_id,
            repository=repository,
            pull_request_number=pull_request_number,
            payload=payload,
            path=args.db,
        )
        results.append(
            {
                "event_id": event["id"],
                "delivery_id": delivery_id,
                "semantic_identity": semantic_identity,
                "status": event["status"],
            }
        )
    print(json.dumps({"events": results}, sort_keys=True))


if __name__ == "__main__":
    main()
