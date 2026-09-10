import pytest

from scripts.ingest_github_action_event import _event_identity


def test_pull_request_identity_uses_head_sha_and_action() -> None:
    repo, number, stable = _event_identity(
        "pull_request",
        {
            "action": "synchronize",
            "repository": {"full_name": "koshuang/personal-agent-runtime"},
            "pull_request": {"number": 74, "head": {"sha": "abc123"}},
        },
    )
    assert repo == "koshuang/personal-agent-runtime"
    assert number == 74
    assert stable == "head:abc123:action:synchronize"


def test_review_identity_uses_review_id() -> None:
    _, number, stable = _event_identity(
        "pull_request_review",
        {
            "repository": {"full_name": "koshuang/personal-agent-runtime"},
            "pull_request": {"number": 74},
            "review": {"id": 1234},
        },
    )
    assert number == 74
    assert stable == "review:1234"


def test_check_run_identity_requires_linked_pull_request() -> None:
    with pytest.raises(ValueError, match="at least one pull request"):
        _event_identity(
            "check_run",
            {
                "repository": {"full_name": "koshuang/personal-agent-runtime"},
                "check_run": {"id": 99, "pull_requests": []},
            },
        )


def test_unsupported_event_fails_closed() -> None:
    with pytest.raises(ValueError, match="unsupported GitHub event"):
        _event_identity(
            "issues",
            {"repository": {"full_name": "koshuang/personal-agent-runtime"}},
        )
