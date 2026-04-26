import hashlib
import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from lli.logger import get_logger

logger = logging.getLogger(__name__)

@dataclass
class ActiveSession:
    id: str
    dir_path: Path
    system_prompt_hash: str
    client_ip: str | None
    client_id: str | None
    chain_hash: str = ""
    next_sequence_id: int = 1
    last_active: datetime = field(default_factory=datetime.now)

class SessionRouter:
    """
    Routes incoming requests to appropriate eternal sessions based on identity and continuity.
    """

    def __init__(self, base_dir: Path):
        self.base_dir = base_dir
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.active_sessions: dict[str, ActiveSession] = {}
        self._logger = get_logger()

    def _hash_message(self, msg: Any) -> str:
        """Create a stable hash of a single message."""
        if not isinstance(msg, dict):
            return hashlib.sha256(str(msg).encode("utf-8")).hexdigest()
        
        # Simplify the message for stable comparison
        content = msg.get("content", "")
        role = msg.get("role", "")
        
        # Handle list-based content (e.g., Anthropic or OpenAI vision)
        if isinstance(content, list):
            parts = []
            for item in content:
                if isinstance(item, dict) and item.get("type") == "text":
                    parts.append(item.get("text", ""))
            content_str = " ".join(parts)
        else:
            content_str = str(content)
            
        fingerprint = f"{role}:{content_str}"
        return hashlib.sha256(fingerprint.encode("utf-8")).hexdigest()

    def _extract_messages_fingerprint(self, body: Any) -> list[str]:
        """Extract a fingerprint array of all messages except the newest user prompt."""
        if not isinstance(body, dict):
            return []
            
        messages = body.get("messages", [])
        if not isinstance(messages, list):
            return []
            
        # Try to identify where the "new" interaction starts. Usually the last user message.
        # But we just hash all messages to keep it simple.
        fingerprints = [self._hash_message(m) for m in messages]
        return fingerprints
    
    def _compute_chain_hash(self, system_hash: str, messages_fingerprint: list[str]) -> str:
        """Compute an incremental hash chain from system prompt and messages."""
        # Start with system hash
        current_hash = system_hash
        
        # Chain each message fingerprint
        for msg_fp in messages_fingerprint:
            combined = current_hash + msg_fp
            current_hash = hashlib.sha256(combined.encode("utf-8")).hexdigest()
        
        return current_hash

    def _extract_system_hash(self, body: Any) -> str:
        if not isinstance(body, dict):
            return ""
        
        system = body.get("system", "")
        if not system:
            messages = body.get("messages", [])
            if isinstance(messages, list) and len(messages) > 0:
                if isinstance(messages[0], dict) and messages[0].get("role") == "system":
                    system = messages[0].get("content", "")
                    
        return hashlib.sha256(str(system).encode("utf-8")).hexdigest()

    def route_request(self, record: dict[str, Any]) -> ActiveSession:
        """Find the matching session or create a new one."""
        headers = record.get("headers", {})
        body = record.get("body", {})
        
        explicit_session_id = headers.get("x-lli-session-id") or headers.get("X-LLI-Session-ID")
        client_id = headers.get("x-client-id") or headers.get("X-Client-ID")
        
        # 1. Explicit ID override
        if explicit_session_id:
            return self._get_or_create_session(explicit_session_id, body=body, client_id=client_id)
            
        # 2. Check for continuity using hash chain
        system_hash = self._extract_system_hash(body)
        messages_fingerprint = self._extract_messages_fingerprint(body)
        
        # Build chain hash incrementally and check for partial matches
        # This allows matching sessions that have fewer messages
        current_chain_hash = self._compute_chain_hash(system_hash, [])
        
        for sess_id, sess in self.active_sessions.items():
            # Check client_id match if set
            if client_id and sess.client_id and client_id != sess.client_id:
                continue
            
            # Check system prompt match
            if system_hash != sess.system_prompt_hash:
                continue
            
            # Build chain hash incrementally to find matching prefix
            for i in range(len(messages_fingerprint)):
                current_chain_hash = self._compute_chain_hash(system_hash, messages_fingerprint[:i+1])
                
                # If this partial chain matches an active session, it's a continuation
                if current_chain_hash == sess.chain_hash:
                    self._logger.info(f"Routed request to existing session {sess.id}")
                    # Update to final chain hash
                    final_chain_hash = self._compute_chain_hash(system_hash, messages_fingerprint)
                    sess.chain_hash = final_chain_hash
                    sess.last_active = datetime.now()
                    return sess
        
        # No match found - create new session
        
        self._logger.info("No matching session found, creating new session")

        # 3. Create new session
        # Generate new ID with UUID to avoid collisions
        # Format: session-20260426-T172830-4f512e81 (date + T + time + uuid)
        timestamp = datetime.now().strftime("%Y%m%d-T%H%M%S")
        unique_id = uuid.uuid4().hex[:8]
        new_id = f"session-{timestamp}-{unique_id}"
        
        self._logger.info(f"Created new session {new_id}")
        return self._get_or_create_session(new_id, body=body, client_id=client_id)
        
    def _get_or_create_session(self, session_id: str, body: dict[str, Any] = None, client_id: str = None) -> ActiveSession:
        if session_id not in self.active_sessions:
            dir_path = self.base_dir / session_id
            dir_path.mkdir(parents=True, exist_ok=True)
            
            # Compute chain hash for new session
            sys_hash = self._extract_system_hash(body) if body else ""
            msg_fp = self._extract_messages_fingerprint(body) if body else []
            chain_hash = self._compute_chain_hash(sys_hash, msg_fp) if msg_fp else sys_hash
            
            # Write metadata
            meta_path = dir_path / "session_meta.json"
            if not meta_path.exists():
                meta_path.write_text(json.dumps({
                    "session_id": session_id,
                    "started_at": datetime.now().isoformat(),
                    "client_id": client_id,
                    "chain_hash": chain_hash
                }, indent=2))
                
            self.active_sessions[session_id] = ActiveSession(
                id=session_id,
                dir_path=dir_path,
                system_prompt_hash=sys_hash,
                client_ip=None,
                client_id=client_id,
                chain_hash=chain_hash,
                next_sequence_id=1
            )
        return self.active_sessions[session_id]
