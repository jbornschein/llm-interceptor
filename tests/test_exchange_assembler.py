"""Tests for ExchangeAssembler file writing logic."""

from pathlib import Path
from typing import Any

from lli.session_router import ActiveSession
from lli.exchange_assembler import ExchangeAssembler


def test_exchange_assembler_writes_request_file(tmp_path: Path) -> None:
    """Test that write_request creates a properly formatted request file."""
    assembler = ExchangeAssembler()
    
    # Create a mock session
    session_dir = tmp_path / "session_20260101_120000"
    session_dir.mkdir()
    session = ActiveSession(
        id="session_20260101_120000",
        dir_path=session_dir,
        system_prompt_hash="test-hash",
        client_ip=None,
        client_id=None,
        messages_fingerprint=[],
        next_sequence_id=1,
    )
    
    # Write a request
    record = {
        "id": "req-123",
        "timestamp": "2026-01-01T12:00:00Z",
        "type": "request",
        "method": "POST",
        "url": "https://api.example.com/v1/chat/completions",
        "body": {
            "model": "gpt-4",
            "messages": [{"role": "user", "content": "Hello"}],
        },
    }
    
    assembler.write_request(record, session)
    
    # Verify file was created
    files = list(session_dir.glob("*.json"))
    assert len(files) == 1
    assert files[0].name.startswith("00001_request_")
    
    # Verify content
    content = files[0].read_text(encoding="utf-8")
    assert '"id": "req-123"' in content
    assert '"method": "POST"' in content
    assert '"model": "gpt-4"' in content


def test_exchange_assembler_writes_response_file(tmp_path: Path) -> None:
    """Test that write_response creates a properly formatted response file."""
    assembler = ExchangeAssembler()
    
    # Create a mock session and pre-write the request
    session_dir = tmp_path / "session_20260101_120000"
    session_dir.mkdir()
    session = ActiveSession(
        id="session_20260101_120000",
        dir_path=session_dir,
        system_prompt_hash="test-hash",
        client_ip=None,
        client_id=None,
        messages_fingerprint=[],
        next_sequence_id=1,
    )
    
    # Write request first (sets up mapping)
    request_record = {
        "id": "req-456",
        "timestamp": "2026-01-01T12:00:00Z",
        "type": "request",
        "method": "POST",
        "url": "https://api.example.com/v1/chat/completions",
        "body": {"model": "gpt-4", "messages": []},
    }
    assembler.write_request(request_record, session)
    
    # Write response
    response_record = {
        "request_id": "req-456",
        "timestamp": "2026-01-01T12:00:02Z",
        "type": "response",
        "status_code": 200,
        "latency_ms": 1500,
        "body": {
            "choices": [{"message": {"role": "assistant", "content": "Hi!"}}],
            "usage": {"total_tokens": 10},
        },
    }
    
    assembler.write_response(response_record)
    
    # Verify response file was created
    files = sorted(session_dir.glob("*.json"))
    assert len(files) == 2
    assert files[1].name.startswith("00001_response_")
    
    # Verify content
    content = files[1].read_text(encoding="utf-8")
    assert '"request_id": "req-456"' in content
    assert '"status_code": 200' in content
    assert '"total_tokens": 10' in content


def test_exchange_assembler_writes_streaming_response(tmp_path: Path) -> None:
    """Test that write_streaming_response correctly assembles and writes streaming chunks."""
    assembler = ExchangeAssembler()
    
    # Create a mock session and pre-write the request
    session_dir = tmp_path / "session_20260101_120000"
    session_dir.mkdir()
    session = ActiveSession(
        id="session_20260101_120000",
        dir_path=session_dir,
        system_prompt_hash="test-hash",
        client_ip=None,
        client_id=None,
        messages_fingerprint=[],
        next_sequence_id=1,
    )
    
    # Write request first
    request_record = {
        "id": "req-789",
        "timestamp": "2026-01-01T12:00:00Z",
        "type": "request",
        "method": "POST",
        "url": "https://api.example.com/v1/chat/completions",
        "body": {"model": "gpt-4", "messages": [], "stream": True},
    }
    assembler.write_request(request_record, session)
    
    # Write streaming chunks (OpenAI format)
    chunks = [
        {"content": {"choices": [{"delta": {"content": "Hello"}}]}},
        {"content": {"choices": [{"delta": {"content": " world"}}]}},
        {"content": {"choices": [{"delta": {"content": "!"}}]}},
    ]
    meta = {
        "request_id": "req-789",
        "timestamp": "2026-01-01T12:00:03Z",
        "usage": {"total_tokens": 5},
    }
    
    assembler.write_streaming_response("req-789", chunks, meta)
    
    # Verify response file was created
    files = sorted(session_dir.glob("*.json"))
    assert len(files) == 2
    assert files[1].name.startswith("00001_response_")
    
    # Verify content
    content = files[1].read_text(encoding="utf-8")
    assert '"request_id": "req-789"' in content
    # The response body should contain the assembled content
    assert 'Hello world!' in content


def test_exchange_assembler_sequence_id_incrementing(tmp_path: Path) -> None:
    """Test that sequence IDs increment correctly for multiple exchanges."""
    assembler = ExchangeAssembler()
    
    session_dir = tmp_path / "session_20260101_120000"
    session_dir.mkdir()
    session = ActiveSession(
        id="session_20260101_120000",
        dir_path=session_dir,
        system_prompt_hash="test-hash",
        client_ip=None,
        client_id=None,
        messages_fingerprint=[],
        next_sequence_id=1,
    )
    
    # Write multiple requests
    for i in range(1, 4):
        request_record = {
            "id": f"req-{i}",
            "timestamp": f"2026-01-01T12:00:{i:02d}Z",
            "type": "request",
            "method": "POST",
            "url": "https://api.example.com/v1/chat/completions",
            "body": {"model": "gpt-4", "messages": []},
        }
        assembler.write_request(request_record, session)
        
        response_record = {
            "request_id": f"req-{i}",
            "timestamp": f"2026-01-01T12:00:{i+2:02d}Z",
            "type": "response",
            "status_code": 200,
            "body": {},
        }
        assembler.write_response(response_record)
    
    # Verify all files were created with correct sequence IDs
    files = sorted(session_dir.glob("*.json"))
    assert len(files) == 6  # 3 requests + 3 responses
    
    # Check sequence IDs in filenames
    expected_files = [
        "00001_request_",
        "00001_response_",
        "00002_request_",
        "00002_response_",
        "00003_request_",
        "00003_response_",
    ]
    for i, expected_prefix in enumerate(expected_files):
        assert files[i].name.startswith(expected_prefix), f"File {i}: {files[i].name}"
