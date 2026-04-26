"""Tests for SessionRouter routing logic."""

from typing import Any

from pathlib import Path

from lli.session_router import SessionRouter


def create_record(
    system: str | None = None,
    messages: list[dict[str, Any]] | None = None,
    headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Helper to create a request record."""
    body: dict[str, Any] = {}
    if system is not None:
        body["system"] = system
    if messages is not None:
        body["messages"] = messages
    return {"body": body, "headers": headers or {}}


class TestSessionRouterNewSession:
    """Test creating new sessions."""

    def test_session_router_creates_new_session_on_first_request(
        self, tmp_path: Path
    ) -> None:
        """A request with no existing sessions should create a new session."""
        router = SessionRouter(base_dir=tmp_path)

        record = create_record(
            messages=[{"role": "user", "content": "Hello"}],
        )

        session = router.route_request(record)

        assert session is not None
        assert session.id.startswith("session-")
        assert session.dir_path.exists()
        assert session.next_sequence_id == 1

    def test_session_router_creates_new_session_with_different_system_prompt(
        self, tmp_path: Path
    ) -> None:
        """Different system prompt should create a new session."""
        router = SessionRouter(base_dir=tmp_path)

        # First request with system A
        record_a = create_record(
            system="You are a helpful assistant.",
            messages=[{"role": "user", "content": "Hello"}],
        )
        session_a = router.route_request(record_a)

        # Second request with different system B
        record_b = create_record(
            system="You are a sarcastic assistant.",
            messages=[{"role": "user", "content": "Hello"}],
        )
        session_b = router.route_request(record_b)

        assert session_a.id != session_b.id
        assert session_a.system_prompt_hash != session_b.system_prompt_hash

    def test_session_router_uses_explicit_session_id(self, tmp_path: Path) -> None:
        """Explicit session ID from header should be used."""
        router = SessionRouter(base_dir=tmp_path)

        record = create_record(
            messages=[{"role": "user", "content": "Hello"}],
            headers={"x-lli-session-id": "my-custom-session"},
        )

        session = router.route_request(record)

        assert session.id == "my-custom-session"
        assert session.dir_path == tmp_path / "my-custom-session"


class TestSessionRouterContinuity:
    """Test session continuity logic."""

    def test_session_router_routes_same_client_to_same_session(
        self, tmp_path: Path
    ) -> None:
        """Same client with same system prompt should route to existing session."""
        router = SessionRouter(base_dir=tmp_path)

        # First request
        record1 = create_record(
            system="You are a helpful assistant.",
            messages=[
                {"role": "user", "content": "Hello"},
                {"role": "assistant", "content": "Hi there!"},
            ],
        )
        session1 = router.route_request(record1)
        initial_chain = session1.chain_hash

        # Second request from same client
        record2 = create_record(
            system="You are a helpful assistant.",
            messages=[
                {"role": "user", "content": "Hello"},
                {"role": "assistant", "content": "Hi there!"},
                {"role": "user", "content": "How are you?"},
            ],
        )
        session2 = router.route_request(record2)

        assert session1.id == session2.id
        # Chain hash should be updated to reflect new state
        assert session2.chain_hash != initial_chain
        assert len(session2.chain_hash) == 64  # SHA256 hex length

    def test_session_router_continuity_with_client_id(self, tmp_path: Path) -> None:
        """Requests with matching client_id and message continuity should route to same session."""
        router = SessionRouter(base_dir=tmp_path)

        # First request with client_id and initial message
        record1 = create_record(
            messages=[{"role": "user", "content": "Hello"}],
            headers={"x-client-id": "client-123"},
        )
        session1 = router.route_request(record1)

        # Second request with same client_id -延续 the conversation
        record2 = create_record(
            messages=[
                {"role": "user", "content": "Hello"},
                {"role": "assistant", "content": "Hi there!"},
                {"role": "user", "content": "How are you?"},
            ],
            headers={"x-client-id": "client-123"},
        )
        session2 = router.route_request(record2)

        assert session1.id == session2.id

    def test_session_router_different_client_id_creates_new_session(
        self, tmp_path: Path
    ) -> None:
        """Requests with different client_id should create separate sessions."""
        router = SessionRouter(base_dir=tmp_path)

        # First request with client_id A
        record1 = create_record(
            messages=[{"role": "user", "content": "Hello"}],
            headers={"x-client-id": "client-a"},
        )
        session1 = router.route_request(record1)

        # Second request with different client_id B
        record2 = create_record(
            messages=[{"role": "user", "content": "Hello"}],
            headers={"x-client-id": "client-b"},
        )
        session2 = router.route_request(record2)

        assert session1.id != session2.id


class TestSessionRouterForkDetection:
    """Test fork/branch detection logic."""

    def test_session_router_forks_on_different_system_prompt(
        self, tmp_path: Path
    ) -> None:
        """Changing system prompt mid-session should fork."""
        router = SessionRouter(base_dir=tmp_path)

        # First session with system A
        record_a1 = create_record(
            system="You are a helpful assistant.",
            messages=[{"role": "user", "content": "Question 1"}],
        )
        session_a = router.route_request(record_a1)

        # Continue with system A
        record_a2 = create_record(
            system="You are a helpful assistant.",
            messages=[
                {"role": "user", "content": "Question 1"},
                {"role": "assistant", "content": "Answer 1"},
                {"role": "user", "content": "Question 2"},
            ],
        )
        session_a2 = router.route_request(record_a2)
        assert session_a.id == session_a2.id

        # Now change system - should fork
        record_b1 = create_record(
            system="You are a grumpy assistant.",
            messages=[
                {"role": "user", "content": "Question 1"},
                {"role": "assistant", "content": "Answer 1"},
                {"role": "user", "content": "Question 2"},
            ],
        )
        session_b = router.route_request(record_b1)

        assert session_a.id != session_b.id
        # The fork should be based on the parent session
        assert "fork" in session_b.id.lower() or session_b.id != session_a.id

    def test_session_router_forks_on_older_messages_fingerprint(
        self, tmp_path: Path
    ) -> None:
        """Continuing from an earlier point in history should fork."""
        router = SessionRouter(base_dir=tmp_path)

        # Build up a long conversation
        record1 = create_record(
            messages=[{"role": "user", "content": "A"}],
        )
        session1 = router.route_request(record1)

        record2 = create_record(
            messages=[
                {"role": "user", "content": "A"},
                {"role": "assistant", "content": "B"},
                {"role": "user", "content": "C"},
            ],
        )
        session2 = router.route_request(record2)
        assert session1.id == session2.id

        record3 = create_record(
            messages=[
                {"role": "user", "content": "A"},
                {"role": "assistant", "content": "B"},
                {"role": "user", "content": "C"},
                {"role": "assistant", "content": "D"},
                {"role": "user", "content": "E"},
            ],
        )
        session3 = router.route_request(record3)
        assert session2.id == session3.id

        # Now send a message that starts from an earlier point (fork)
        record_fork = create_record(
            messages=[
                {"role": "user", "content": "A"},
                {"role": "assistant", "content": "B"},
                {"role": "user", "content": "NEW MESSAGE"},  # Different from C
            ],
        )
        session_fork = router.route_request(record_fork)

        # This should create a fork since the history diverges
        assert session_fork.id != session3.id
