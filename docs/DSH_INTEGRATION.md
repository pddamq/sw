# DeepSeek Harness integration notes

## Reference

- Repository: https://github.com/deepseek-ai/deepseek-harness
- Reviewed local revision: `fcc0ad462172522aaf5c93a7b2e01f6b9f0b0159`.
- License: MIT; upstream license is retained in `deepseek-harness/LICENSE`.
- This change is an independently implemented Python/Vue adaptation of a design pattern, not a runtime dependency on Cordis or the TypeScript harness.

## Newly integrated: recoverable tool-result previews

Upstream reference paths:

- `packages/spill/spill-policy/src/index.ts`: retain full tool text before replacing the model-facing copy; storage failure preserves the return.
- `packages/spill/spill-policy/src/retention.ts`: keep ordered head and tail text and explicit omission metadata.
- `packages/compaction/compaction-tool-result-pruner/src/index.ts`: distinguish reduced conversation content from canonical source evidence.

Forma implementation:

- `backend/tool_results.py` stores tool-facing text over 18,000 Unicode characters in SQLite, with a random result ID, source tool/call, SHA256, session ownership and cascade deletion.
- Model-facing and UI previews keep both ends within 18,000 characters, including the application omission/recovery notice. The middle is retained, not discarded.
- `tool_result_read(result_id, offset, limit)` becomes available on the next model request when this session has retained results. It reads historical text only, even when CAD is disconnected or Blender is selected. It never invokes an executor or grants approval.
- Character paging returns at most 8,000 characters and 12,000 serialized JSON characters per page. Control-character escaping and non-BMP Unicode are covered.
- The authenticated session-scoped API and `RetainedToolResult.vue` expose the same text to users. Readers load only on click, display one page at a time and discard responses after changing sessions or unmounting.
- Tool results, errors and existing CAD checkpoints keep their original meaning. If local retention fails, the original text remains inline and a visible notice is emitted; the operation is not replayed or relabeled.
- Existing `sw_status` projection still compacts its operation window before model-facing retention. Its append-only operation log remains accessed through `executor_history`; this feature does not undo source-side truncation already performed by an MCP server.
- Images continue through the existing media path. This store retains text only; original image pixels are not copied into text storage.

Retention is bounded: 8 MiB per result, 32 MiB per conversation and 128 MiB globally, measured as live retained UTF-8 text. Old entries are removed first; missing/expired or integrity-failed results return explicit errors. SQLite can retain reusable free pages after deletion, so these are logical text budgets, not a total database-file-size guarantee.

## Reviewed and preserved existing behavior

- Model-request retry and cancellation: compared `packages/llm/llm-retry/src/index.ts` with `backend/model_retry.py`. Retries remain separate from CAD execution; incomplete model output does not replay completed tools.
- Context pressure and atomic summary replacement: compared `packages/compaction/compaction-basic/src/index.ts` with `backend/context_policy.py`. The existing output reserve, complete tool exchanges, summary reduction checks and original-history preservation remain intact.
- Advisory repeat notices, per-request tool snapshots, native transport validation and per-call CAD confirmation were already integrated. They remain enabled.

The full plugin architecture and unbounded retry mode were not imported. The CAD application retains its local execution controls, engineering checks and explicit unknown-outcome handling.

## Release and validation scope

- Source version remains `1.0.0`.
- This source update does not rebuild the existing desktop EXE, portable archive or installer.
- Automated checks use synthetic model/tool responses and the local API. They do not certify live SOLIDWORKS/Blender geometry, manufacturability or external model-provider availability.
