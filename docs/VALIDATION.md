# Validation record — Forma CAD 1.0.0

## DeepSeek Harness recoverable tool results (2026-11-07)

- Final regression: 939 backend tests and 242 frontend tests passed; TypeScript and the production frontend build passed. Vite reported a non-fatal mixed static/dynamic API import notice; the API remains in the main bundle.

- Reviewed local DeepSeek Harness revision fcc0ad462172522aaf5c93a7b2e01f6b9f0b0159; source mapping and implementation boundaries are in DSH_INTEGRATION.md.
- Added durable full-text retention before head/tail previews, model-facing read-only paging and user-facing authenticated paging.
- Tests cover session ownership, cascading deletion, restart persistence, UTF-8 hashes, Unicode offsets, serialized page budgets, bounded eviction, storage failure and no original-tool replay.
- Frontend tests cover lazy loading, page replacement, errors, escaped markup, late responses after session changes and restored result IDs.
- This is a source-only 1.0.0 change. Existing desktop binaries and installers are unchanged; live external-provider and CAD acceptance remain separate.

# Historical validation record — Forma CAD 0.9.0

## Bundled Blender one-click startup (2026-11-07)

- 918 backend tests and 237 frontend tests passed; TypeScript and the production frontend build passed.
- The actual frozen mcp-for-blender 2.2.7 server passed stdio initialization and discovery of 9 tools without uvx or a user-installed Python runtime.
- Synthetic tests cover executable discovery, one-click startup, deduplication, scene identity, credential isolation, failure reporting, authentication and addon bootstrap scheduling.
- Bundled startup cancels the upstream default-port timer and disables automatic addon update checks; Forma selects a loopback port and verifies its own scene before reporting ready.
- Blender itself is not bundled. This machine has no Blender installation, so actual GUI/plugin startup, geometry, viewport and save/export acceptance remain unverified.
- Historical entries below describe their explicitly named releases and do not certify the new Blender workflow.

# Historical validation record — Forma CAD 0.8.3

## 0.8.3 source validation (2026-11-04)

- 255 backend tests and 66 frontend tests passed for 0.8.3; TypeScript and production frontend build passed.
- Added bounded DSML format recovery, per-request execution instructions and factual memory rules.
- Synthetic model tests verify request assembly, budgets, protocol recovery and no duplicate execution.
- Real model/CAD geometry acceptance and the reported SOLIDWORKS .NET loading issue remain unverified.
- Historical validation below applies to its explicitly named release, not to 0.8.3.

# Historical validation record — Forma CAD 0.8.2

## 0.8.2 source validation (2026-11-04)

- 239 backend tests and 66 frontend tests passed; TypeScript and production frontend build passed.
- Full executor catalog verified: 161 tools, no exclusions. Microsoft WebView2 bootstrapper signature verified.
- Includes model-service advanced settings, per-round conversation rendering, context usage display,
  collapsed inspector default, conversation navigation, and image/document drag-and-drop attachments.
- Context category counts remain local estimates; provider-reported totals may differ.
- SOLIDWORKS live geometry acceptance remains unverified. Historical checks below apply to 0.8.1.

# Historical validation record — Forma CAD 0.8.1

The complete 0.8.1 source/build and six frozen distribution checks passed on 2026-11-04.

## Source validation

- 180 backend tests and 15 frontend tests pass; Vue/TypeScript and Vite production build pass.
- New fail-stop regressions cover timeout/disconnect/malformed results, shared mutable snapshots,
  monotonic approval expiry, post-call status failure/cancellation, and API failed/unknown reporting.
- Actual LangGraph tests stop on initial, operation and final transport failures without another
  model request or a second tool in the same batch. Synthetic bridges simulate these failures.
- Frontend request-generation tests cover out-of-order responses, navigation back to the same task,
  invalidation and late callbacks. Evidence truncation is explicitly marked.
- Pinned Vue 3.5.44, DOMPurify 3.4.17, Vite 6.4.5 and Vitest 4.1.12 replace affected versions.
  npm audit reports zero known vulnerabilities for the refreshed lockfile on 2026-11-04.
- Complete actual upstream MCP catalog remains 161 tools, no excluded tools, all schemas/checksums verified.
- New regression coverage: one-time task-bound approvals, duplicate/stale decisions, denial, expiry,
  document-state changes, cancellation, durable unknown outcomes, report evidence, API authentication,
  and a fixed read-only recovery allowlist even when tool annotations claim a mutator is read-only.
- A real LangGraph test confirms a denied operation stops the tool batch and further model calls.
- Prior dependency repair, long-task, cancellation, model-stream, media, history and retrieval tests remain.
- Official WebView2 bootstrapper is verified with Windows Authenticode and SHA256. Runtime installation
  is not performed here; installer execution in dependency-failure unit tests uses test doubles.

## Distribution validation

Version 0.8.1 full release build and all six frozen distribution checks passed on 2026-11-04:

- scripts/smoke_full.py: relocated ZIP, automatic bundled connection, all 161 tool names/schemas,
  assembly hashes, macro opt-in/reconnect, bundled bootstrapper and one-click executor recovery.
- scripts/smoke_prerequisites.py: actual frozen EXE read-only runtime report, payload integrity,
  secret exclusion, and no application DB/executor creation.
- scripts/smoke_portable.py: extracted ZIP opens the real WebView2 desktop runtime.
- scripts/smoke_setup.py: frozen config backup recovery, repair and preservation of the original.
- scripts/smoke_media.py: authenticated image upload/decode and session cascade cleanup.

- scripts/smoke_execution.py: real frozen application, real 161-tool C# executor, local model-protocol
  fixture, denied doc_save, persisted status checkpoints/report, and read-only recovery planning.
  The requested save never executes. No external model is contacted and no CAD edit is performed.
- 0.8.0 baseline browser interaction with an isolated synthetic bridge: displayed exact confirmation arguments,
  cancelled the operation, checked recovery state and ran a read-only planning fixture. Zero console
  errors or warnings. Screenshots: output/playwright/v080-confirmation-fixture.png and
  output/playwright/v080-recovery-fixture.png. The connected-CAD display in these images is synthetic,
  not evidence of an installed SOLIDWORKS or real geometry/measurement acceptance.

- 0.8.1 browser fixture verified report loading failure, the visible retry control and successful recovery
  after removing injected HTTP 503 responses. The two console errors were the expected injected 503s;
  no other errors or warnings were recorded. Screenshot: output/playwright/v081-evidence-retry-fixture.png.

The 0.8.0 baseline frontend also passed a synthetic positive-confirmation browser flow: the card cleared,
the task completed, and one tool-return evidence item appeared. All browser save/measurement returns
are fixtures. Distribution checks do not execute a WebView2 installer or certify clean installation.

## Confirmation and recovery boundaries

- Confirmation compares connection and document metadata before/after waiting, not a geometry hash.
  User edits with identical metadata and changes after the final check remain possible.
- Checkpoints are execution records, not CAD backups, atomic transactions or rollback points.
- A returned tool result is evidence of that return, not proof of dimensions, tolerances or design intent.
  Reports explicitly keep engineering_verified false; errors and uncertain outcomes remain visible.
- Recovery compares saved path/type/configuration, never assumes geometry identity, never reuses old
  handles and never automatically replays modifications. A new read-only task plans further work.

## Declared target and remaining acceptance

- Windows 10 22H2 (19045+) / Windows 11 x64, excluding ARM64 and 32-bit Windows.
- A functional SOLIDWORKS installation and model API credentials are required for actual CAD work.
- WebView2 may be installed online through the signed Microsoft bootstrapper only after interactive
  confirmation, or explicit /INSTALLWEBVIEW2=1 in silent setup. The full runtime is not embedded.
- .NET Framework 4.8 is a system prerequisite. The full MCP's .NET 10 runtime is self-contained.
- Real SW2023 CAD, Simulation licensing and three newer COM adapters, FeatureWorks, external model/
  embedding/vision integration, manual-edit races and geometric results require target-machine tests.
- Installer compilation is separate from clean install/upgrade/uninstall acceptance. Missing-runtime
  installation, offline/proxy/enterprise conditions, restarts, signing and multi-machine tests remain.

## Post-0.8.1 source validation: approval explanations

- Confirmation-required model calls must supply `_forma_purpose`, explaining the intended action,
  target and reason in the user's language. Missing explanations are returned to the model before
  approval or CAD execution. This application-only field is stripped before executor validation/calls.
- Approval cards prominently display the explanation separately from the policy reason. Tool names,
  arguments and document snapshots remain in collapsed technical details. Legacy cards without an
  explanation allow cancellation but disable confirmation.
- Backend: 190 tests passed, including explanation correction, conditional/generic confirmations,
  exact executor arguments and discovery schemas. Frontend: 20 tests passed; production build passed.
- Synthetic browser checks passed for light/dark themes, 390px width, expandable details and missing
  explanations. Card, explanation and confirmation-button contrast ratios all exceeded 4.5:1.
- Screenshots: `output/playwright/approval-purpose-light.png`, `approval-purpose-dark.png`,
  `approval-purpose-mobile.png` and `approval-purpose-mobile-actions.png` in the same directory.
  The preview used fabricated document data; no real CAD call or external model was exercised.
- Frontend development port is now 52881. These source changes have not been repackaged into the
  existing 0.8.1 ZIP/installer; the backend must be restarted to load the new model-call logic.

## 多模型服务配置（源码更新，现有 0.8.1 安装包尚未重打包）

- 在“工作台设置 → 模型服务”中添加多个服务，每个服务独立保存名称、Base URL、加密密钥和已选模型列表；点击列表项即可编辑。
- 点击“获取模型”读取该服务的 `/models` 列表，再勾选多个模型；不支持列表接口的服务可手动添加模型 ID。
- 勾选“使用完整请求路径”后，输入框填写完整的聊天接口地址（例如 `/v1/chat/completions`），请求不会重复拼接路径。未勾选时填写 API 根路径。
- “测试连接与速度”使用当前草稿，不自动保存；显示连接、工具调用观察结果、首个输出时间、总耗时及 token/秒。速率为输出 token 数 / 整次请求耗时，包含网络等待；服务不返回用量时明确显示估算值。
- 点击“保存此服务”保存地址、密钥和多模型列表；Temperature 和视觉设置收纳在“通用模型参数”折叠区，单独点击“保存通用参数”。
- AI 设置界面改为顶部设置标签、左侧服务列表、右侧分组编辑区。连接详情和手动添加默认折叠；模型可搜索；测速结果按指标展示；保存操作固定在编辑区底部。窄屏分为服务列表和编辑页面。
- UI 整理后 27 项前端测试与生产构建通过；本地模拟浏览器验证了 1280px/390px 布局无横向溢出、明暗主题、搜索、测速、保存与重新编辑。截图：`output/playwright/model-settings-redesign-desktop.png`、`model-settings-redesign-light.png`、`model-settings-redesign-mobile-list.png`、`model-settings-redesign-mobile-editor.png`。
- 对话输入框上方的“当前 AI”按服务分组选择模型。切换会保存为下次默认值；任务运行期间锁定选择，本轮使用固定的地址、模型与密钥快照。
- 原有单服务配置自动作为“原有模型服务”显示；新服务保存在用户数据目录的 `model-profiles.json`，密钥仍通过 Windows DPAPI 加密。修改服务地址后须重新填写密钥，避免把旧服务密钥发送到新地址。
- 模型列表地址通常取聊天路径对应的 API 根路径加 `/models`。非标准完整路径会尝试同级 `/models`，失败时可手动添加模型。
- 前端开发端口为 **52881**。模型诊断会调用所填服务，可能产生少量 API 费用。
- 验证：201 项后端测试、27 项前端测试及生产构建通过；真实 OpenAI SDK + 模拟 HTTP 验证了完整路径、独立密钥、用量/估算统计、流中断和旧服务兼容。
- 浏览器使用 `build/model_ui_fixture.py` 本地模拟服务，走通获取模型、多选、测速、保存/编辑、刷新保留选择和正式对话路由；额外验证地址变更密钥保护、列表失败后手动添加。没有调用真实模型供应商或 SOLIDWORKS。
- 截图保存在 `output/playwright/model-service-list-fixture.png`、`model-service-speed-fixture.png` 和 `model-chat-selection-fixture.png`。模拟服务与浏览器已关闭；未重启用户停止的前端服务。

## Artifacts

- dist/FormaCAD/FormaCAD.exe (retain the full directory and executable config).
- dist/FormaCAD-Full-Preview-0.8.1-x64.zip and SHA256 manifest including bootstrapper hash.
- dist/installer/FormaCAD-Full-Preview-Setup-0.8.1-x64.exe.

Complete 161-tool coverage describes the pinned upstream catalog, not every SOLIDWORKS API or
certification of CAD results. VBA defaults off; optional modules/licenses remain prerequisites.
