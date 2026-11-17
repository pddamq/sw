# Harness-inspired reliability improvements

Implemented locally without importing deepseek-harness at runtime.

## Tool contracts

`backend/tool_contracts.py` is the shared projection and argument preparation layer for CAD, discovery, clarification and knowledge tools. Each request holds a detached contract snapshot. All these tools accept the strictly validated optional `_forma_purpose` display field; required CAD explanations retain their previous requirements. Preparation validates the advertised schema, strips display metadata, then validates the original executor schema. Metadata never reaches CAD, knowledge search or question validation. Responses and Messages transports retain the same parameter constraints. Full-response validation, live registry checks, serial mutations and operation approvals remain in place.

## Request inspection

Open the right execution panel and expand **模型请求检查**. Records are created before ordinary model generation attempts, including transport retries and overflow recovery. Inspect model/protocol/configuration, complete retained normalized messages and tool definitions, changes from the previous retained session request, request fingerprints, input anchor, registry changes, repair instructions and context replacements. Export a selected sanitized record from its detail section.

- Authenticated endpoints: `GET /api/sessions/{session_id}/requests?before={id}` and `GET /api/sessions/{session_id}/requests/{request_id}`.
- SQLite table: `model_requests`; request input is serialized before dispatch and the transport receives an independent deep copy.
- Retention: 200 requests per session, 1000 globally; sanitized snapshot limit 2 MiB. The existing diagnostic sanitizer also bounds strings, lists and nesting. Redactions/omissions are explicitly flagged. Oversize snapshots retain metadata rather than pretending to contain the entire request.
- Diffs and hashes describe sanitized data, not secret-bearing wire bytes. Config field changes, changed/added/removed tool schema fingerprints and unchanged message-prefix count are included.
- This captures the internal normalized generation envelope. Provider-specific HTTP serialization occurs afterward. Auxiliary summary calls are not separate request-inspection rows; their resulting context replacement is recorded on the subsequent generation. Old requests cannot be reconstructed retroactively, and pruned/redacted material is not recoverable here.
- Known credentials are removed before persistence; image bytes are omitted. Conversation text and local paths may remain. Review exports before sharing. These records never execute tools or act as a replay queue.
- A storage failure at the capture boundary stops before dispatching that model request; it does not retry CAD operations.

## Explicit context overflow recovery

`backend/context_overflow.py` recognizes explicit context-overflow codes, and a narrow Messages-style `invalid_request_error` with context-length wording, for HTTP 400/413/422. Authentication failures, rate limiting, unknown errors and generic parameter errors are not classified as context overflow.

The real agent can force compaction below the estimated threshold after a provider reports overflow. It pins the current user requirement and final complete tool exchange, preserves original messages until a valid smaller replacement is available, and retries generation at the same round. Recovery is capped at two successful compaction retries per generation. No-progress compaction stops rather than resending unchanged input. Cancellation propagates; executed CAD operations are never replayed. In-task overflow summaries remain transient conversation state and are evidenced in request inspection rather than rewriting historical CAD results.

## Question drafts

Question-card drafts use browser local storage, keyed by session, task and question identity. Schema, question text, expiry, option IDs/cardinality and text length are checked before restore. Only the server's pending question is rendered. Authoritative health reconciliation removes closed drafts for the active session, and submission, skip, expiry or task completion clears the corresponding draft. Failed submission keeps the draft. Storage denial/quota errors do not block interaction.

Local storage is not encrypted; drafts can contain user-entered requirements. Different cards remain isolated. A late response for an older card cannot settle the current card. Existing global-stop, approval-denial and question-timeout semantics are unchanged.

## Offline validation

- `tests/test_tool_contracts.py`: shared definitions, nested constraints, display-field removal and native protocol schema parity.
- `tests/test_request_journal.py`: redaction, detached snapshots, differences, bounded retention, session deletion, API authentication and cross-session isolation.
- `tests/test_context_overflow.py`: real agent recovery via Chat/Responses/Messages, HTTP/in-stream errors, no duplicate fixture mutation, cancellation and retry bounds.
- `tests/test_harness_regressions.py`: multi-round explanatory inline tool reference, discovery metadata, question skip, native history continuation and storage fault boundary.
- `frontend/src/questionDrafts.test.ts`: persistence, stale/invalid drafts, isolation, cleanup and blocked storage.
- `frontend/src/questionInteractions.test.ts`: mounted Vue interactions using happy-dom, duplicate clicks, failed requests, stale responses, remount recovery and request inspection escaping.
- Timeline regressions ensure late settled-question events do not alter the next running tool.

These are synthetic, offline regressions modeled after observed defects, not recordings of real provider traffic or a claim of CAD acceptance. No real provider calls, CAD launch or port 5173 access is required.
