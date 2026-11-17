# Model capability and transport configuration

## Per-model capabilities

Open **Settings > Model services**, edit a service, and expand **逐模型能力配置**. Each model can declare supported reasoning efforts, image input support, and optional context/output limits.

- Unknown reasoning support preserves manual default/low/medium/high selection. An empty supported-effort list permits only the default.
- Explicit support lists filter the composer selector and are checked by the backend before a task is created or a provider request starts. DeepSeek Messages excludes medium.
- Image support is tri-state: unknown follows the existing global opt-in; unsupported disables outgoing images for that model; supported still requires global visual input to be enabled.
- Blank per-model limits inherit the service values. Effective output limits must remain below the effective context window.
- These are user declarations, not capability claims inferred from model names or a successful model-list request. Draft connection tests use draft overrides without saving them.
- Credentials remain protected with Windows DPAPI and are never returned in the public capability catalog.

## Global transport policy

Under **Settings > Model services > 通用模型参数 > 重试与超时**:

- Maximum retries: default 5, range 0–10; zero disables automatic retries.
- Initial delay: default 1 second.
- Maximum delay: default 30 seconds, upper bound 300; it must be at least the initial delay.
- Jitter: default 0.2, range 0–1; randomized extra delay is capped by the maximum.
- Read idle timeout: default 120 seconds, upper bound 1800. It measures a period without incoming data, not total generation duration. Explicit diagnostic-test deadlines remain shorter.

The same policy applies to generation and context-summary model requests. Only connection/transport failures, incomplete transport streams, HTTP 408/429 and 5xx are retryable. Authentication/configuration errors, cancellation, output truncation and protocol validation failures do not enter transport retry. Protocol correction remains a separate bounded process.

`Retry-After` supports seconds and HTTP dates. Retries never run earlier than this server requirement. If it exceeds the configured maximum delay, the task stops instead of truncating the server wait. Native stream error translation preserves response headers.

No retry wraps CAD tool execution. Completed operations are retained; uncertain outcomes stop the task and require live inspection.
