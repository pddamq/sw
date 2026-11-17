# Diagnostic log export

The conversation header has a `导出诊断日志` button. Reproduce the problem, click
this button, review the JSON file, then attach it when reporting the issue.
The button also works while a task is running; that export is a snapshot.

New tasks automatically record:

- Application version, model, endpoint, output/context limits and available tool names.
- Each model request, including messages, tool definitions and tool choice.
- Assembled model responses before validation, including rejected protocol text,
  structured calls, finish reason, attempt number and elapsed time.
- Partial responses discarded before transport retries.
- Validation errors, retry events, tool arguments/results, approval events and task outcome.
- Exception types and task error tracebacks.

Records are local, stored separately from chat events in the existing SQLite
database. They are never added to model context or sent anywhere automatically.
The export endpoint requires the same local authentication as other APIs.
Deleting a conversation also deletes its diagnostic records.

## Redaction and limits

The active model key and local access token are removed before writing. Common
credential fields, bearer tokens, `sk-` keys and URL query strings are also
redacted. Binary image payloads are omitted. This is not anonymization: prompts,
model output, CAD dimensions, document names and local paths may remain. Review
the file before sharing it.

At most 256 records per conversation and 1024 records globally are retained;
oldest records are pruned. Each record is limited to 64 KiB. Large text, deep
objects and large collections are truncated with explicit markers. Request
snapshots are therefore diagnostic evidence, not guaranteed exact replay files.
Each model request also keeps a structured `request_snapshot`: round, attempt,
endpoint, model, tool choice, output limit, message/tool counts and advertised tool
names. This summary survives full-payload truncation; `tool_names_truncated`
explicitly identifies an incomplete name list. The summary remains subject to
credential redaction and bounded field lengths.
A storage error stops diagnostic recording for that task instead of replaying or
aborting CAD operations; the application log receives a credential-free warning.

Old tasks do not retroactively gain request/response diagnostics. Use the existing
conversation export for their previously stored task events. This feature must
be included in a newly built installer before it appears in the installed app.
