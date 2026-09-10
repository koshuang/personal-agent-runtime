import pytest

from scripts.ingest_github_action_event import _event_identities


def test_pull_request_identity_uses_head_sha_and_action() -> None:
    identities = _event_identities(
        "pull_request",
        {
            "action": "synchronize",
            "repository": {"full_name": "koshuang/personal-agent-runtime"},
            "pull_request": {"number": 74, "head": {"sha": "abc123"}},
        },
    )
    assert identities == [("koshuang/personal-agent-runtime", 74, "head:abc123:action:synchronize")]


def test_review_identity_uses_review_id_and_action() -> None:
    identities = _event_identities(
        "pull_request_review",
        {
            "action": "submitted",
            "repository": {"full_name": "koshuang/personal-agent-runtime"},
            "pull_request": {"number": 74},
            "review": {"id": 1234},
        },
    )
    assert identities == [("koshuang/personal-agent-runtime", 74, "review:1234:action:submitted")]


def test_review_identity_distinguishes_lifecycle_actions() -> None:
    base = {
        "repository": {"full_name": "koshuang/personal-agent-runtime"},
        "pull_request": {"number": 74},
        "review": {"id": 1234},
    }
    submitted = _event_identities("pull_request_review", {**base, "action": "submitted"})
    edited = _event_identities("pull_request_review", {**base, "action": "edited"})
    dismissed = _event_identities("pull_request_review", {**base, "action": "dismissed"})
    assert len({submitted[0][2], edited[0][2], dismissed[0][2]}) == 3


def test_review_identity_requires_action() -> None:
    with pytest.raises(ValueError, match="action must be a non-empty string"):
        _event_identities(
            "pull_request_review",
            {
                "repository": {"full_name": "koshuang/personal-agent-runtime"},
                "pull_request": {"number": 74},
                "review": {"id": 1234},
            },
        )


def test_check_run_identity_requires_linked_pull_request() -> None:
    with pytest.raises(ValueError, match="at least one pull request"):
        _event_identities(
            "check_run",
            {
                "action": "completed",
                "repository": {"full_name": "koshuang/personal-agent-runtime"},
                "check_run": {"id": 99, "pull_requests": []},
            },
        )


def test_check_run_identity_covers_every_linked_pull_request() -> None:
    identities = _event_identities(
        "check_run",
        {
            "action": "completed",
            "repository": {"full_name": "koshuang/personal-agent-runtime"},
            "check_run": {
                "id": 99,
                "pull_requests": [{"number": 74}, {"number": 75}, {"number": 74}],
            },
        },
    )
    assert identities == [
        ("koshuang/personal-agent-runtime", 74, "check:99:action:completed:pr:74"),
        ("koshuang/personal-agent-runtime", 75, "check:99:action:completed:pr:75"),
    ]


def test_check_run_identity_distinguishes_lifecycle_actions() -> None:
    base = {
        "repository": {"full_name": "koshuang/personal-agent-runtime"},
        "check_run": {"id": 99, "pull_requests": [{"number": 74}]},
    }
    created = _event_identities("check_run", {**base, "action": "created"})
    completed = _event_identities("check_run", {**base, "action": "completed"})
    assert created != completed
    assert completed == [
        ("koshuang/personal-agent-runtime", 74, "check:99:action:completed:pr:74")
    ]


def test_check_run_identity_requires_action() -> None:
    with pytest.raises(ValueError, match="action must be a non-empty string"):
        _event_identities(
            "check_run",
            {
                "repository": {"full_name": "koshuang/personal-agent-runtime"},
                "check_run": {"id": 99, "pull_requests": [{"number": 74}]},
            },
        )


def test_unsupported_event_fails_closed() -> None:
    with pytest.raises(ValueError, match="unsupported GitHub event"):
        _event_identities(
            "issues",
            {"repository": {"full_name": "koshuang/personal-agent-runtime"}},
        )
