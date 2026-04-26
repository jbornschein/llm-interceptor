import json
from pathlib import Path
from typing import Any
import logging

from lli.logger import get_logger
from lli.merger import StreamMerger
from lli.session_router import ActiveSession

class ExchangeAssembler:
    """
    Writes assembled request/response pairs directly to session directories.
    Handles turning SSE chunks into final unified JSON bodies on-the-fly.
    """

    def __init__(self):
        self._logger = get_logger()
        # Keep track of request ID to sequence ID mapping
        self._request_seq_map: dict[str, tuple[str, ActiveSession]] = {}
        # Temporary in-memory merger instance
        self._merger = StreamMerger(input_path="", output_path="")

    def write_request(self, record: dict[str, Any], session: ActiveSession) -> None:
        """Write a request record to the session directory."""
        request_id = record["id"]
        
        # Pad sequence ID to 5 digits
        seq_id = f"{session.next_sequence_id:05d}"
        session.next_sequence_id += 1
        
        self._request_seq_map[request_id] = (seq_id, session)
        
        # Format the file name: {seq}_request_{timestamp}.json
        # Clean timestamp for filename
        ts_clean = record["timestamp"].replace(":", "-").replace("T", "_").split(".")[0]
        if ts_clean.endswith("Z"):
            ts_clean = ts_clean[:-1]
            
        filename = f"{seq_id}_request_{ts_clean}.json"
        filepath = session.dir_path / filename
        
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(record, f, ensure_ascii=False, indent=2)
            
        self._logger.debug(f"Wrote {filename} to {session.dir_path.name}")

    def write_response(self, record: dict[str, Any]) -> None:
        """Write a complete non-streaming response."""
        request_id = record.get("request_id")
        if not request_id or request_id not in self._request_seq_map:
            self._logger.warning(f"Response for unknown request {request_id}")
            return
            
        seq_id, session = self._request_seq_map[request_id]
        
        ts_clean = record["timestamp"].replace(":", "-").replace("T", "_").split(".")[0]
        if ts_clean.endswith("Z"):
            ts_clean = ts_clean[:-1]
            
        filename = f"{seq_id}_response_{ts_clean}.json"
        filepath = session.dir_path / filename
        
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(record, f, ensure_ascii=False, indent=2)
            
        self._logger.debug(f"Wrote {filename} to {session.dir_path.name}")
        # Clean up mapping
        self._request_seq_map.pop(request_id, None)

    def write_streaming_response(self, request_id: str, chunks: list[dict[str, Any]], meta: dict[str, Any]) -> None:
        """Rebuilds a streaming response from chunks and writes it."""
        if request_id not in self._request_seq_map:
            self._logger.warning(f"Streaming response for unknown request {request_id}")
            return
            
        seq_id, session = self._request_seq_map[request_id]
        
        # Determine format
        api_format = self._merger._detect_api_format(chunks)
        
        if api_format == "anthropic":
            response_record = self._merger._rebuild_anthropic_response(request_id, chunks, meta)
        else:
            response_record = self._merger._rebuild_openai_response(request_id, chunks, meta)
            
        # Ensure timestamp exists
        if "timestamp" not in response_record:
            response_record["timestamp"] = chunks[0].get("timestamp", meta.get("timestamp", ""))
            
        ts_clean = response_record["timestamp"].replace(":", "-").replace("T", "_").split(".")[0]
        if ts_clean.endswith("Z"):
            ts_clean = ts_clean[:-1]
            
        filename = f"{seq_id}_response_{ts_clean}.json"
        filepath = session.dir_path / filename
        
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(response_record, f, ensure_ascii=False, indent=2)
            
        self._logger.debug(f"Wrote assembled {filename} to {session.dir_path.name}")
        # Clean up mapping
        self._request_seq_map.pop(request_id, None)
