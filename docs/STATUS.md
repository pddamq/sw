# Product implementation status — 0.8.1

## Implemented

- Vue3 desktop shell, authenticated local API, persistent conversations and execution events.
- Real LangGraph tool loop and OpenAI-compatible streaming client, bounded context and reference images.
- BM25 retrieval, optional embeddings, rank fusion and context summarization retaining original history.
- Windows DPAPI credential storage, setup diagnostics, config recovery and portable/installer builds.
- Default bundled self-contained C# upstream executor: all 161 pinned tool names and JSON schemas verified
  against the original tool implementations, including 5 Simulation tools and FeatureWorks recognition.
- Separate COM dispatch adapter for 3 newer Simulation members absent in public older reference metadata;
  it calls the real API by exact name and propagates unsupported-member errors. It is not a simulated tool.
- VBA is present in the catalog but defaults off in both executor and Agent. Explicit user opt-in plus
  reconnect enables all 161 selections; default Agent selection covers 160 tools.
- Explicit executor environment uses application user data, never model API secrets, and no automatic SW launch.
- 18-tool Framework basic executor remains an optional fallback, not the primary full executor.
- Background auto-connection limited to exact bundled executors, with opt-out; external commands remain manual.
- Refresh reattaches an already running SW; moved missing bundle paths are repaired without overwriting custom settings.
- Simulation-aware timeouts, active Future resolution during cancellation and graceful long-operation shutdown.
- Incomplete/truncated model streams and malformed tool identities cannot execute partial CAD tool plans.
- Install target explicitly Windows 10 22H2 / Windows 11 x64; valid WebView2 detection across registry views.
- Microsoft-signed small WebView2 bootstrapper bundled with SHA256 integrity checking; missing-runtime
  repair is explicitly confirmed in setup or native portable startup, then checks real installed state.
- Read-only startup report without WebView2, model credentials, installer/executor/CAD execution.
- One-click bundled connection recovery and persisted bundle markers for upgrades with older extractions retained.

- Task-bound, one-time confirmations for sensitive operations; expiry, cancellation and observed-state changes stop execution.
- Durable per-call checkpoints and categorized tool-return reports; interrupted calls remain uncertain, not rolled back.
- Recovery first compares saved document identity and restricts the new planning task to a fixed read-only tool allowlist.
- Confirmation and evidence cards, authenticated report export, and visible waiting-for-confirmation task state.
- Updated and pinned frontend sanitizer, Vue, build and test dependencies; npm audit reports zero known vulnerabilities.

## Validation

- Transport exceptions and malformed results terminate the task with unknown checkpoints instead of model replanning.
- Failed post-operation status checks stop subsequent calls while preserving the actual tool return.
- Deep-copied confirmation snapshots and monotonic decision deadlines prevent stale approvals.
- Latest-request guards protect evidence/recovery panels and confirmation submissions from out-of-order responses.


180 backend regression tests and 15 frontend tests pass. Actual compiled C# processes are exercised:
161-name set equality, schema validation, source/assembly provenance, offline state, macro opt-in and reconnect.
TypeScript/Vue and Vite production build pass. Frozen distribution checks are recorded in docs/VALIDATION.md.

## Requires target-machine validation

- Every real SOLIDWORKS CAD operation; this environment has no installed SW COM registration.
- SW2023 year/SP compatibility. Upstream reports SW2025 SP1.2 baseline.
- Simulation module/licensing and the 3 newer Simulation COM member adapters, with numeric readback.
- FeatureWorks installation/licensing and actual imported-body recognition.
- Real model/embedding/vision-provider integration with the user's credentials.
- Clean install/upgrade/uninstall, code signing and second-machine validation, including actual missing
  WebView2 installation, offline/proxy/corporate restrictions and restart behavior. Build machine does not
  install/uninstall the runtime; confirmation/exit paths are exercised with isolated test doubles.

## Functional boundaries

- All 161 upstream tools means pinned upstream coverage, not every SOLIDWORKS API or arbitrary design success.
- Authenticated screenshots/reference images are available; no embedded live 3D renderer.
- One active application task. Interrupted tasks are recorded but not automatically replayed after restart.
- Cancellation keeps already-completed CAD changes; inspect live model state before retrying.
- In-process knowledge retrieval is intended for modest local libraries; no OCR or automatic updater.
- Embedding credentials reuse the model API key.
