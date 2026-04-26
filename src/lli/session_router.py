import hashlib
import json
import logging
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
    messages_fingerprint: list[str] = field(default_factory=list)
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
            
        # 2. Check for continuity
        system_hash = self._extract_system_hash(body)
        messages_fingerprint = self._extract_messages_fingerprint(body)
        
        # The current request usually appends 1 user message, so the prefix of the current
        # fingerprints should match the active session's fingerprints perfectly.
        # Let's search active sessions for the best match.
        
        best_match = None
        best_score = 0
        is_fork = False
        fork_parent_id = None
        
        for sess_id, sess in self.active_sessions.items():
            # If client_id is set and mismatch, skip
            if client_id and sess.client_id and client_id != sess.client_id:
                continue
                
            score = 0
            # Require system prompt to match if there is one
            if system_hash and sess.system_prompt_hash and system_hash != sess.system_prompt_hash:
                continue
            elif system_hash == sess.system_prompt_hash and system_hash:
                score += 10
                
            # Compare message history. If current history starts with session history,
            # it's a direct continuation.
            sess_is_fork = False
            if messages_fingerprint and sess.messages_fingerprint:
                # Find how many messages match from the beginning
                match_len = 0
                min_len = min(len(messages_fingerprint), len(sess.messages_fingerprint))
                for i in range(min_len):
                    if messages_fingerprint[i] == sess.messages_fingerprint[i]:
                        match_len += 1
                    else:
                        break
                        
                if match_len > 0:
                    # Very strong match if they share a prefix
                    score += match_len * 5
                    
                    # Exact continuation: current request has the exact same history plus some new messages
                    if match_len == len(sess.messages_fingerprint):
                        score += 50
                    else:
                        # Fork detection: they share a prefix, but then diverge
                        # We consider it a fork if it matches at least 2 messages or >50% of history
                        if match_len >= 2 or match_len >= len(sess.messages_fingerprint) / 2:
                            score += 20
                            sess_is_fork = True
            
            if score > best_score:
                best_score = score
                best_match = sess
                is_fork = sess_is_fork
                fork_parent_id = sess.id

        # Threshold for continuity
        if best_match and best_score > 10:
            if is_fork:
                # Create a new fork session
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                # Append _fork to parent id (strip existing forks if needed, or just append)
                base_id = fork_parent_id.split("_fork")[0]
                new_id = f"{base_id}_fork_{timestamp}"
                
                counter = 1
                while new_id in self.active_sessions or (self.base_dir / new_id).exists():
                    new_id = f"{base_id}_fork_{timestamp}_{counter}"
                    counter += 1
                    
                self._logger.info(f"Detected fork from {fork_parent_id}, created {new_id} (score {best_score})")
                sess = self._get_or_create_session(new_id, body=body, client_id=client_id)
                sess.messages_fingerprint = messages_fingerprint
                return sess
            else:
                self._logger.info(f"Routed request to existing session {best_match.id} (score {best_score})")
                # Update fingerprint to new state
                best_match.messages_fingerprint = messages_fingerprint
                best_match.last_active = datetime.now()
                return best_match

        # 3. Create new session
        # Generate new ID
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        new_id = f"session_{timestamp}"
        
        # Handle collision
        counter = 1
        while new_id in self.active_sessions or (self.base_dir / new_id).exists():
            new_id = f"session_{timestamp}_{counter}"
            counter += 1
            
        self._logger.info(f"Created new session {new_id}")
        return self._get_or_create_session(new_id, body=body, client_id=client_id)
        
    def _get_or_create_session(self, session_id: str, body: dict[str, Any] = None, client_id: str = None) -> ActiveSession:
        if session_id not in self.active_sessions:
            dir_path = self.base_dir / session_id
            dir_path.mkdir(parents=True, exist_ok=True)
            
            # Write metadata
            meta_path = dir_path / "session_meta.json"
            if not meta_path.exists():
                meta_path.write_text(json.dumps({
                    "session_id": session_id,
                    "started_at": datetime.now().isoformat(),
                    "client_id": client_id
                }, indent=2))
                
            sys_hash = self._extract_system_hash(body) if body else ""
            msg_fp = self._extract_messages_fingerprint(body) if body else []
            
            self.active_sessions[session_id] = ActiveSession(
                id=session_id,
                dir_path=dir_path,
                system_prompt_hash=sys_hash,
                client_ip=None,
                client_id=client_id,
                messages_fingerprint=msg_fp,
                next_sequence_id=1
            )
        return self.active_sessions[session_id]
