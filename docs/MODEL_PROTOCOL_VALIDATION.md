# Live model protocol validation

Date: 2026-11-04. These checks apply to the current source tree, not the previously built 0.8.3 installer.

## Provider and isolation

- Endpoint: https://xuseny.online/v1/chat/completions
- Model: deepseek-v4.1-flash
- Credentials were supplied through a process environment variable, not saved to source or application profiles.
- Each run used a temporary profile/database and synthetic messages. No user history, attachments or knowledge-library data was submitted.
- CAD tool schemas came from the bundled catalog. The executable bridge was replaced by an in-memory simulator with seven supported tool schemas. No real SOLIDWORKS process or document was queried or modified.
- Live tests are opt-in and are not part of the automated test suite.

## Observed results

1. No-op tool protocol probe: passed. A complete structured connection_probe call was validated, and a random receipt in its tool result was returned correctly. Total observed elapsed time: 6963 ms.
2. Synthetic sheet-metal workflow: passed in 6 model rounds. The model inspected simulated state, called sheetmetal_miter_flange once, rebuilt and inspected the simulated result. Zero tool errors and zero DSML leakage into the answer.
3. Synthetic history containing an old malformed DSML assistant message: passed in 6 model rounds with the same single-mutation and post-operation checks. Original stored history was preserved; model-input history was sanitized.
4. Injected protocol failure: passed in 6 total rounds, consisting of one deliberately injected malformed DSML response and 5 real model requests. Exactly one protocol recovery occurred. The real provider returned structured calls after recovery; the simulator performed one flange operation and subsequent inspection. Zero real CAD operations.

## Source changes covered

- Incremental DSML recognition buffers only a possible marker prefix; ordinary comparisons and HTML no longer wait until the end of the reply.
- Marker-like names such as DSMLParser remain ordinary text. Suspect prefix buffering has a 256-character bound.
- Historical assistant protocol text is isolated in outgoing model context and summaries, without rewriting original messages or user attachments.
- Reasoning fields are retained in the protocol-recovery assistant message for compatible providers.
- Model diagnostics handle HTTP 400/422 parameter rejection and second-leg API failures explicitly, preserve required tool choice during usage-option fallback, and bound diagnostic output including reasoning.
- Protocol-recovery requests still use bounded retries and normal schema/confirmation checks. Textual markup is never executed.

## Automated checks

- Backend: 292 tests passed.
- Frontend: 66 tests passed.
- Synthetic HTTP tests exercise the actual OpenAI SDK, including malformed output, incomplete streams, result roundtrip failures and provider parameter rejection.

## Reproduction

Use scripts/verify_live_model.py with --base-url and --model. Set FORMA_TEST_API_KEY only in the launching process environment. Select --scenario probe, agent, history or recovery. The script prints a redacted result summary and deletes its temporary profile on exit.

## Limits

These results verify API/tool orchestration for bounded fixtures. They do not certify arbitrary long conversations, every tool in the 161-tool catalog, dimensional correctness of real geometry, installation/upgrade behavior, or the previously reported SOLIDWORKS .NET loading issue. Provider behavior may vary between requests. No new installer was built for these changes.
