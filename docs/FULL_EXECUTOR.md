# 完整工具执行器（0.8.1）

## 交付内容

默认执行器是自包含 Windows x64 的 `resources/mcp/solidworks-mcp.exe`。
实际 MCP `tools/list` 返回 **161 个工具**，与固定上游提交逐项比对，未裁剪任何工具。
原有 18 工具基础版保留在 `resources/builtin-mcp`，仅作手动选择的备选，不替代完整目录。
首次使用优先选完整执行器并默认后台自动连接；已有自定义配置保持原样，外部执行器需手动连接。
设置中的“启动时自动连接随包执行器”可关闭；仅运行当前应用准确匹配且不带自定义参数的内置程序。
自动连接不启动新的 SW，稍后启动 SW 可点击刷新重试附加。接入检查可“一键恢复内置连接”，保留模型设置。
带随包标记的设置会跟随当前分发目录，即使旧解压目录仍存在；没有标记的旧配置仅在原路径失效时尝试修复。

“全部”指固定上游版本实现的全部工具，不等同于 SOLIDWORKS 所有 API 或所有设计任务都已支持。
当前环境没有 SW 真机，已验证编译、协议和工具目录，尚未验证 161 项真实 CAD 操作或 SW2023 兼容性。

## 工具范围

- 会话、连接、状态、对话框恢复。
- 文档新建、打开、保存、打包、撤销、单位和配置管理。
- 草图几何、关系、尺寸、编辑、偏移、镜像、阵列、修剪和导入。
- 拉伸、切除、旋转、放样、扫描、孔、圆角、倒角、拔模、抽壳、阵列和线程等特征。
- 面、边、顶点、实体、参考几何、质量属性、测量和模型检查。
- 装配组件、配合、结构、移动、阵列、干涉和 BOM。
- 工程图图纸、视图、投影视图、剖面、局部视图、尺寸、注释和表格。
- 钣金、曲面、焊件、材料、属性、导出和视口截图。
- `sim_static`、`sim_run`、`sim_plot`、`sim_list`、`sim_delete` 五个仿真工具。
- `feature_recognize` FeatureWorks 特征识别。
- `vba_run` 宏执行工具，默认关闭，可明确开启。

每项准确参数以工作台“工具中心”的实际 JSON Schema 为准。
随包 `tool-catalog.json` 包含全部名称、描述、Schema、上游提交和核心程序集校验值。
LangGraph 按任务选择一组工具给模型，并通过 `discover_tools` 检索剩余目录，避免一次填入 161 份 Schema。

## 宏工具

目录始终包含 `vba_run`，默认设置 `allow_vba=false`：C# 服务禁止宏，Agent 也不选择该工具。
在设置 → C# 执行器中勾选“允许 VBA 宏工具”，保存并重连后，才同时启用 C# 宏能力和 Agent 工具选择。
因此默认 Agent 可选择 160 个工具，显式开启宏后可选择全部 161 个。
宏具备修改模型和本机文件的能力，仅按用户明确宏执行请求使用；开关不会自动生成或运行宏。
关闭开关后 Agent 立即停止选择宏，服务端状态在重连后同步。页面提示配置与连接是否一致。

## 目标电脑依赖

- Windows 10 22H2 / Windows 11 x64、已安装的 SOLIDWORKS、桌面窗口所需的 WebView2 与 .NET Framework 4.8。
- 缺少 WebView2 时安装包或便携启动可经确认联网补装，使用随包且经 Microsoft 签名验证的小引导程序；完整运行时仍需下载。
- .NET Framework 4.8 属于 Windows 桌面前置组件，缺失时提示通过系统更新修复，不自动安装整个 SDK。
- 完整执行器随包带 .NET 10 运行时，用户不需额外安装 .NET 10 SDK。
- 仿真工具需要 Simulation 模块及其实际许可；FeatureWorks 识别需要相应模块和许可。
- 工具数为 161 只证明目录完整，不代表模块存在或许可证可用。
- SW2023、具体 SP、模板、材料库和实际模型都要分别验收。上游报告的测试基线是 SW2025 SP1.2。

## 编译方式及可审计差异

完整构建直接编译 `vendor/solidworks-mcp` 中原始 Core 和 Server 工程，保留全部工具主体。
`build-mcp.ps1 -SolidWorksDir ...` 可使用本机完整 SDK 引用；`-InteropDir ...` 可指定五个引用 DLL 的目录。
默认使用 `prepare_full_executor.py` 下载 SHA256 固定的公开 NuGet 编译引用：

- Visiativ.SOLIDWORKS.Interop 34.0.0：sldworks、swconst。
- BlueByte.SOLIDWORKS.Costing.Interop 2025.5.0：sldcostingapi。
- BlueByte.SOLIDWORKS.FeatureWorks.Interop 2023.1.0：fworks。
- hawkware.solidworks 28.1.0.74：cosworks。

原始引用 DLL 仅放在 `build/native-full/references`，运行包嵌入所需 COM 类型元数据，不复制这些 DLL。
NuGet 元数据、引用来源和 SHA256 随包保留，.NET 运行时通知同样归档。
公开 Simulation 引用较旧，缺少上游使用的三个较新 API 成员，因此额外编译
`executor/SimulationVersionDispatch.cs`，使用反射经真实 COM IDispatch 调用同名成员：

- `SetForceComponentValues2`
- `GetForceComponentValues2`
- `GetPropertyByName2`

适配层不伪造结果、不删掉仿真工具，也不替换为旧 API 的不同语义；目标模块不支持这些成员时返回真实错误。
这三个调用的 COM 行为、版本支持和仿真数值必须在目标机器单独验收。
如果改用本机包含相应成员的新 SDK 引用，编译直接绑定其成员，不需该适配层。

## 构建与目录验证

```powershell
powershell -ExecutionPolicy Bypass -File scripts\build.ps1 -Installer
.\.venv\Scripts\python.exe scripts\verify_full_executor.py
.\.venv\Scripts\python.exe scripts\smoke_full.py
```

构建优先使用 PATH 中的 dotnet，也可使用 `build/dotnet/dotnet.exe` 或单独传入 `build-mcp.ps1 -Dotnet ...`。
工具发现必须与上游 161 个名称完全一致，Schema 全部有效，才会生成 `tool-catalog.json`。
PyInstaller 和便携打包再次检查目录与程序集 SHA256；缺工具或发布后程序集变化都会停止完整包构建。
`smoke_full.py` 解压新 ZIP，验证冻结应用自动选中随包完整 EXE、161 工具目录、宏开关和重连。
这些测试不运行宏、不修改 CAD，也不申请启动新的 SW 实例。

## 数据与操作恢复

执行器收到明确的 `SWMCP_DATA_DIR=<应用数据目录>/executor`，包含日志、暂存、备份等。
`sw_status.recentOps` 保留最近 1000 条并返回 `recentOpsLimit/recentOpsTotal/recentOpsTruncated`；
应用向模型展示时只保留首尾窗口，并用 `recentOpsWindow.omitted` 明确省略数量，不会把截断窗口当作完整历史。
所有完成的操作另追加写入 `executor/operations.jsonl`，进程重启后仍可追溯整段长任务；
模型可用只读工具 `executor_history(offset, limit, name)` 按最新在前分页读取该日志。
`vbaEnabled=false` 时同时返回 `vbaNote`，说明是执行器进程未设置 `SWMCP_VBA=on`，而不是工具不存在；
旧执行器缺少该字段时应用会补上同一解释。
默认关闭自动启动 SW、默认关闭 VBA、显式请求全部工具集，避免外部环境变量意外改变目录或行为。
模型 API Key 不传给执行器进程。
COM 操作串行执行；取消后已完成修改仍保留，超时后先核实 recentOps、特征树和实际模型。
客户端为普通长任务保留 900 秒、仿真 1900 秒、三轮收敛 5700 秒；退出时等待在途调用收尾，不把窗口关闭当作撤销。
模型回复缺少完整结束标记、被截断、中止或调用标识无效时，不执行本轮工具；历史已经完成的步骤仍需检查。
旧版 18 工具设置不会被强行覆盖；选择“mcp / solidworks-mcp.exe”候选并重新连接即可切换。
