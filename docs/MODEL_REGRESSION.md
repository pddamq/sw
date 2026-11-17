# Model regression scenarios

## Offline record and replay

Run from the project root:

```powershell
.\.venv\Scripts\python.exe scripts/model_scenarios.py --record build/model-scenarios.json
.\.venv\Scripts\python.exe scripts/model_scenarios.py --replay build/model-scenarios.json
```

The runner executes the real `CadAgent` graph with temporary SQLite storage, a synthetic model stream and a fake executor. It starts no server, browser or CAD application and sends no external model requests. The six scenarios cover:

1. Discover an unavailable tool and admit it only on the following request.
2. Remove textual tool syntax from contaminated assistant history.
3. Reject an invalid parameter batch and accept a corrected structured call.
4. Summarize long synthetic context while retaining the current request.
5. Discard partial text on a transport interruption and retry generation.
6. Cancel generation, then successfully run the next task in the same session.

Use `--scenario NAME` to select one or more scenarios. Names are `discovery`, `contaminated_history`, `parameter_repair`, `long_context`, `transport_interruption`, and `cancellation_next_task`.

Records contain versioned, fixed synthetic response fixtures, assertions and stable counters. Replay verifies that fixtures still match the runner version, reruns them, and compares outcomes/counters. It never executes arbitrary code from a record, loads credentials, or restores a real user's CAD commands. Unknown versions, scenarios, duplicate scenarios, changed fixtures and records over 1 MiB are rejected. After intentionally changing fixtures, generate a new baseline explicitly.

Metrics include model request count (including summary requests), validated/rejected turns, transport retries and synthetic mutation count. Every result explicitly reports zero real-provider requests and zero real-CAD operations. These are deterministic application reliability checks, **not real-model accuracy measurements**. They do not replace the native protocol tests, approval/cancellation tests, or real CAD acceptance.

## Optional real-provider tests

`scripts/verify_live_model.py` remains a separate explicit opt-in CLI. Supply `--base-url`, `--model`, a scenario, and a process-only `FORMA_TEST_API_KEY`. It uses isolated synthetic fixtures and never reads production profile credentials. Live calls incur provider usage. The offline runner never invokes it automatically.

## Test suite

`tests/test_model_scenarios.py` covers the six scenarios and record validation. `tests/test_native_model_protocols.py` and `tests/test_protocol_consistency.py` cover Chat/Responses/Messages serialization, native replay, atomic validation and transport protocol integrity.
