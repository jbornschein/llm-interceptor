# TODO: Implement Hash Chain for Session Continuity

## Goal
Replace the current `messages_fingerprint` array (stored in memory for each active session) with an incremental hash chain approach. This reduces memory usage while maintaining deterministic session continuity detection.

## Tasks

### 1. SessionRouter Changes
- [x] Modify `ActiveSession` dataclass to include `chain_hash: str` field
- [x] Remove `messages_fingerprint: list[str]` from `ActiveSession`
- [x] Add new method `_compute_chain_hash(system_hash: str, messages_fingerprint: list[str]) -> str`
- [x] Update `route_request` logic to:
  - Build chain hash incrementally for each message position
  - Check if any partial chain hash matches an active session
  - Store only the final chain hash in the session
- [x] Update `_get_or_create_session` to store `chain_hash` in metadata file

### 2. Metadata Format Changes
- [x] Update `session_meta.json` format to include `chain_hash: str` field
- [x] Ensure backward compatibility or migration path

### 3. Tests
- [x] Run existing tests to verify hash chain produces same results as fingerprint array
- [x] Add test for incremental chain hash computation
- [x] Add test for partial chain match (multiple messages appended at once)

## Notes
- Hash chain format: `H(H(H(system, msg1), msg2), msg3)...`
- Continuation detection: Check each partial chain hash against active sessions
- Session IDs remain unchanged (`session-YYYYMMDD-Thhmmss-uuid`)
- Only hash is stored in memory; full messages remain in exchange files on disk
