# Durable conversation and generation attempts

Forma CAD stores validated model turns separately from UI messages. These records are historical evidence, not an execution queue.

## Recovery behavior

- The local SQLite `conversation_turns` journal stores each accepted assistant turn, its model/protocol/endpoint identity, call identifiers, dispatch markers, and corresponding results.
- A model response is validated and durably accepted before its text is displayed or its calls are dispatched. Failure to save acceptance stops that response.
- A complete exchange can be reconstructed for the next user task. Loading history never invokes tools.
- An interrupted batch becomes explicitly labeled historical evidence. Returned results remain known; dispatched calls without results are `unknown`; undispatched calls are `not_executed`. The dispatch marker is conservative: it precedes approval and executor entry, so `unknown` does not assert an operation actually started.
- Unknown outcomes require live-state inspection. Recovery grants no approval and never replays mutations automatically.
- Native provider replay is reused only for the same protocol, model and endpoint. Switching any of them removes provider-specific replay from the outgoing projection, without changing stored evidence.
- Colliding historical call identifiers are projected as evidence rather than rewriting signed native blocks. Malformed or unsupported-version records produce an inspection notice instead of entering the native tool history.
- Context compression preserves complete persisted user-anchor groups. Deleting a session cascades to its journal. Existing experimental journals without endpoint identity receive a compatible column migration.
- Original UI messages, execution events and checkpoints remain intact. Image bytes are not stored in the journal; the existing media store retains images separately.

## Generation status

Each transport attempt emits `model_attempt` with an opaque `attempt_id`, increasing task-local `revision`, round, attempt number, timestamp and elapsed time. Phases include waiting, receiving, retry wait, rejected, interrupted and committed. Committed means the model turn passed validation and was saved; it does not mean CAD operations succeeded or engineering verification passed.

The UI displays attempt status and elapsed seconds. Attempt identifiers also tag answer/reasoning events. Older revisions and late text from superseded attempts are ignored. Retries retain already executed tool evidence; restored interrupted runs stop showing active generation indicators. Legacy traces without attempt identifiers still render.

## Offline verification

Run `.\.venv\Scripts\python.exe -m pytest tests/test_conversation_journal.py tests/test_journal_agent.py tests/test_reasoning.py` from the project root, and `npm test` from `frontend`.

The tests use synthetic providers, temporary databases and fake executors. They cover restart reconstruction, partial batches, corrupt records, native identity changes, duplicate identifiers, migration, cascade deletion, durable acceptance failure, compaction boundaries and stale frontend events. They do not certify a real provider or SOLIDWORKS installation.
