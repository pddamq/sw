# 内置 C# 基础执行器

版本：0.5.0，Windows x64，18 个 MCP 工具。

## 使用

1. 完整解压 `FormaCAD-Basic-Preview-0.5.0-x64.zip`，运行 `FormaCAD/FormaCAD.exe`。
2. 安装并启动 SOLIDWORKS，用相同 Windows 用户和权限级别运行工作台。
3. 打开“接入检查”，核对当前启动路径；默认指向随包的 `forma-mcp.exe`。
4. 点击“连接并验证”。工具数为 18 表示 MCP 协议已连接；SOLIDWORKS 是否连接另行显示。
5. 填写模型 API 配置。先在新的空白零件上验证简单任务，再操作已有设计。

内置的是执行器，不是 SOLIDWORKS 本体。运行依赖 .NET Framework 4.8；桌面界面另需 WebView2。
基础版通过 COM 晚绑定调用安装在目标机器上的 SW，编译机不需要 interop DLL，也不需要 .NET 10 SDK。
此方式减少编译依赖，不等同于已验证全部 SW 版本。SW2023 真机操作仍待验收。

已有外接 MCP 配置会被保留。可在接入检查页明确选择“内置基础执行器”，保存后重新连接。
扫描候选不会运行程序；连接按钮启动 C# 子进程并仅尝试附加已运行的 SW。
只有明确请求 `sw_connect(launch=true)` 时才尝试启动新的 SW；退出执行器不会关闭 SW 或文档。

## 工具范围

- `sw_help`：范围、单位和操作顺序说明。
- `sw_status`、`sw_connect`：状态、附加；启动 SW 为显式可选操作。
- `doc_list`、`doc_new`、`doc_open`、`doc_save`：列出文档、创建零件、打开和保存原生文件。
- `model_info`、`doc_rebuild`、`feature_list`、`model_measure`：文档信息、重建、顶层特征和质量属性。
- `sketch_start`、`sketch_rectangle`、`sketch_circle`、`sketch_finish`：在准确命名的基准面创建基础二维草图。
- `feature_extrude`：由闭合二维草图生成单向定深的实体凸台拉伸。
- `view_fit`、`view_screenshot`：缩放显示和实际视口 PNG 截图。

草图坐标为局部草图坐标，输入长度为毫米；内部换算为米。质量属性输出为 kg、m²、m³。
基础版不施加尺寸或几何约束，因此草图通常欠约束；矩形与圆的创建不代表尺寸已被锁定。
不包含切除、圆角、倒角、阵列、装配编辑、工程图创建、钣金、曲面、仿真或通用 C#/VBA 执行。
`model_measure` 读取当前 SW 密度/材料下的质量属性，不验证预期材料、公差或全部设计尺寸。

保存仅支持与文档类型匹配的 `.sldprt`、`.sldasm`、`.slddrw`，使用已有的绝对本地目录。
已有目标文件需显式 `overwrite=true`，包括保存回同一路径。基础版不导出 STEP。
截图来自当前真实视口，不更改方向；SW 窗口最小化或桌面锁定时可能失败。

## 基础矩形板验收

以下是目标机器上的待执行步骤，并非本构建环境已完成的 CAD 结果。

1. `sw_connect`，参数 `launch=false`，确认 `attached=true`。
2. `doc_new`；若 SW 没有默认零件模板，提供现有 `.prtdot` 的绝对 `template_path`。
3. `feature_list`，选择 `type=RefPlane` 的准确名称；不要猜测中文或英文名称。
4. `sketch_start`，传 `plane_name`。
5. `sketch_rectangle`，参数为 `x1_mm=0, y1_mm=0, x2_mm=100, y2_mm=60`。
6. `sketch_finish`，记录返回的 `sketch_name`。
7. `feature_extrude`，传该草图名称和 `depth_mm=10`。
8. `doc_rebuild` 后 `model_measure`，理论体积为 `0.00006 m³`，即 `60000 mm³`；比较实际数值并人工检查。
9. `view_fit`、`view_screenshot`，确认截图与 SW 窗口一致。
10. `doc_save` 到用户指定的新 `.sldprt` 路径，重新打开文件核对结果。

圆柱算例可在另一个新零件上改用半径 10 mm 的圆与 20 mm 拉伸，理论体积为 `2000π mm³`。
尺寸核对、约束检查、文件重新打开与人工复核都属于真实验收的一部分。

## 编译与验证

```powershell
powershell -ExecutionPolicy Bypass -File scripts\build-builtin-mcp.ps1
.\.venv\Scripts\python.exe -m pytest -q tests\test_builtin_executor.py
powershell -ExecutionPolicy Bypass -File scripts\build.ps1 -Installer
.\.venv\Scripts\python.exe scripts\smoke_builtin.py
```

源文件为 `executor/FormaMcp.cs`；构建到 `resources/builtin-mcp`。
PyInstaller 将完整目录放到桌面程序的 `_internal/resources/builtin-mcp`，请保留 EXE 和 `.exe.config`。
协议测试运行真正编译的 C# EXE；便携包测试解压后通过实际冻结的 Python API 连接该 EXE、发现 18 个工具并重连。
这些检查不启动 SW、不修改 CAD 模型，也不代替真实 SW2023 验收。

## 错误与恢复

- 参数有类型、必填、未知字段、长度及距离范围校验；错误会作为真实 MCP 工具错误返回。
- 标准输出仅用于逐行 JSON-RPC；诊断写标准错误。单条请求超过 1 MiB 时关闭传输。
- 全部 COM 操作在同一个 STA 线程串行执行。多个应用实例之间没有共享锁，请只保留一个控制工作台。
- 仅关闭本执行器启动且仍然匹配的草图；用户手动切换到其他草图时返回错误。
- COM 失败或超时可能已产生部分特征；先检查特征树和实际模型，再决定重试。没有自动回滚或自动重放。
- 当前基础版不提供上游 recentOps；使用 `feature_list`、`model_info` 和 SW 窗口核实。
- 更广泛的工具可另行构建或配置上游 `solidworks-mcp`，但需分别验证其依赖和 SW 兼容性。
