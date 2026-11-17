# Third-party notices

Forma CAD integrates, but is not affiliated with, SOLIDWORKS or Dassault Systemes.
SOLIDWORKS must be installed separately on the user's computer.

## C# SolidWorks MCP

- Source: https://github.com/soheil-mp/solidworks-mcp
- Revision: 79fb529b67ca4a0587e5570024a772fad0b2c14d
- License: MIT. The complete upstream LICENSE is retained in vendor/solidworks-mcp/LICENSE
  and included in binary packages under licenses/solidworks-mcp/.
- The upstream source files remain unchanged. Forma adds a separate compile-time adapter for
  three newer Simulation COM methods when using public older reference metadata; see docs/FULL_EXECUTOR.md.
- Upstream's reported live test baseline is SOLIDWORKS 2025 SP1.2.
  SW2023 support requires separate compatibility testing.
- Full builds use either locally installed SOLIDWORKS interop types or checksum-pinned NuGet
  compile-only reference assemblies. Original interop DLLs are not copied into the runtime package.
  Compile reference package metadata and provenance are included under resources/mcp/.
  Public package metadata does not establish rights to redistribute the SOLIDWORKS application;
  SOLIDWORKS and licensed optional modules remain separately installed prerequisites.

## DeepSeek Harness design reference

- Source: https://github.com/deepseek-ai/deepseek-harness
- Reviewed revision: fcc0ad462172522aaf5c93a7b2e01f6b9f0b0159
- License: MIT; the local reference checkout retains its LICENSE.
- Forma independently adapts the recoverable head/tail tool-result retention pattern in Python/Vue.
  It does not embed the TypeScript source or add the Cordis/plugin runtime as a dependency.
  Source paths and integration boundaries are documented in docs/DSH_INTEGRATION.md.

## Other dependencies

Python dependencies and exact installed versions are listed in requirements-lock.txt after a
validated build. Frontend dependencies are locked by frontend/package-lock.json.
Core libraries include Vue (MIT), Vite (MIT), lucide (ISC), FastAPI (MIT),
LangGraph (MIT), OpenAI Python SDK (Apache-2.0), MCP Python SDK (MIT),
pywebview (BSD-3-Clause), pypdf (BSD-3-Clause), and PyInstaller (GPL with its
bootloader distribution exception). Preserve their notices with release artifacts.

The self-contained MCP distribution includes Microsoft .NET runtime notices.
WebView2 是单独安装的 Microsoft 桌面运行时，不属于 SOLIDWORKS。0.7.0 随包提供通过
Windows Authenticode 验证的官方联网引导程序，来源与摘要记录在
`resources/prerequisites/webview2-bootstrapper.json`。完整运行时在目标机经确认后由 Microsoft
安装程序下载，安装和使用受 Microsoft 条款约束；工作台不会重打包 SOLIDWORKS 本体。

Before a public release, generate a complete dependency license inventory from the
locked environment and retain runtime license files. This source-stage notice does
not replace the full license text of each dependency.
