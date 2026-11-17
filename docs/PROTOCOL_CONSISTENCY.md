# Protocol and tool-context consistency

Implemented 2026-11-05. These are source-level changes; older installers and
already-running processes do not automatically acquire them.

## Request contracts

- The application prompt uses protocol-neutral tool terminology. Transport
  examples match the selected `chat`, `responses`, or `claude` protocol.
- Each model round takes a detached tool-definition snapshot. Request tools,
  response validation, and CAD dispatch share that admission snapshot.
- Current names and additions, removals, and definition changes are included in
  the round instructions and recorded as a `tool_registry` diagnostic event.
  These events use the existing bounded/redacted diagnostic store, not a new
  durable session format or an unlimited replay log.
- Live CAD declarations and permissions are rechecked before accepting a batch
  and before dispatch. Discovery does not authorize calls in the same batch.
- Before sending any protocol request, tool calls must have unique IDs and
  immediate matching results. Orphan, repeated, and unresolved results/calls
  fail locally, without a provider request or transport retry.
- Native replay for the selected model/protocol must match validated tool IDs,
  names, arguments, and answer text. Replay from another model/protocol is not
  used. Historical calls are not required to remain currently advertised.

## Failure handling

- A complete textual tool envelope (DeepSeek DSML, XML-looking `tool_calls`,
  singular `<tool_call>`/`<tool_use>`/`<function_call>`, a bare `{name,
  arguments}` object, a JSON array of calls, or a conservative
  `tool(kw=value)` line) is recovered into native calls before validation.
  Recovery is atomic at the envelope level: every recovered call must have a
  complete identity; known names then pass the same schema/live-registry checks
  as native calls. The markup is removed from the visible answer and the
  recovery is recorded as `recovered_textual_calls`. A complete envelope in the
  reasoning channel is also recovered when the answer text has none. Prompt text
  no longer embeds literal call envelopes, which previously invited models to
  copy them. Envelope recognition tolerates full-width bars and whitespace,
  including the observed `<｜｜DSML｜｜ calls>` / `<｜｜DSML｜｜ invoke ...>`
  spelling; an outer wrapper may be `tool_calls`, `function_calls`, or `calls`.
- A guessed/unregistered tool name is a per-call result, not a whole-response
  rejection: that call receives `not_executed` with `category=unregistered_tool`,
  and independent read-only siblings still run. If the same batch contains an
  unregistered name, known calls that could modify CAD are marked
  `unavailable_batch` and skipped, so an incomplete plan cannot change the model.
- Partial, ambiguous, or mixed native/textual envelopes are still rejected as a
  whole. Bounded repair feedback identifies the category and marks the batch
  `not_executed`. Schema feedback includes the field path and expected
  constraint, not the rejected value.
- Discovery is recommended only when the discovery tool is advertised.
- A dispatch-time precondition failure produces a paired error result marked
  `not_executed`. A failed call is a per-call result: it never pre-empts other
  calls in the same batch. Independent reads, `sw_help`, knowledge calls, and
  later CAD mutations all receive their own result unless the batch itself named
  an unavailable tool, matching the Harness batch rule that a normal tool
  failure does not abort the turn. Only task-level stops (cancellation,
  denied/stale approval, unknown outcome) end the batch.
- A batch with more than one call emits a status summary with executed, failed,
  and not-executed counts; each `tool_result` also carries `batch_id`,
  `batch_index`, and `outcome`/`executed` metadata.
- Tool results expose `outcome`, `executed`, and `category`; the UI renders a
  skipped call as `未执行` instead of the generic `失败` label.
- An identical failed call is not blocked. When the live state is unchanged the
  executor runs it again and attaches an advisory `repeat_notice` with the
  previous error and repeat count, so a real user retry after starting SW can
  still reach the executor. The guard never manufactures a fake failure.
- `sw_status.recentOps` is projected as a bounded head/tail window with
  `recentOpsTotal`, `recentOpsTruncated`, `recentOpsWindow.omitted`, the
  append-only `operationLog` path, and a model-facing instruction to page the
  full log with `executor_history(offset, limit)`. A missing VBA explanation is
  synthesized from `vbaEnabled=false` as a settings/reconnect note.
- Messages requests preserve the native `tool_result.is_error` flag. Internal
  error metadata is not forwarded as an unsupported Chat message field.
- Existing CAD confirmation, cancellation, unknown-outcome stopping, and
  completed-operation non-replay rules remain in effect.

## Offline regression coverage

`tests/test_protocol_consistency.py` covers protocol-specific examples and
repair requests, malformed call/result history, replay mismatch, detached
snapshots, registry changes during generation and before dispatch, atomic
compaction, and corrected calls after a pre-execution failure. Existing
protocol, retry, confirmation, and execution-control suites remain applicable.

No provider, CAD process, browser, or development server is needed for these
fixtures. Offline results do not establish a lower real-model error rate;
that requires same-model/same-provider comparison and real CAD acceptance.

## Reference

The design was compared with DeepSeek Harness's Messages serializer, tool
history projection, execution pipeline, and its transport-first handling of
model output. The Python implementation is project-specific; no Harness package
was edited or added as a runtime dependency.
