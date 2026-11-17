# Blender 内置接入

## 用户使用

1. 在电脑上安装 Blender，或准备便携版 Blender。
2. 打开 Forma，在聊天选择 `Blender MCP`，点击「一键启动」；也可在「Skills 与 MCP」点击「一键启动 Blender 并连接」。
3. 应用自动识别 Blender、打开独立窗口、加载随包插件、分配本机端口、启动内置 MCP，再读取场景确认连接。
4. 提示就绪后直接输入建模需求。无需安装 Python/uv、输入命令、编辑 JSON、安装或启用插件。

正常安装的 Blender 从 PATH、Windows 安装信息与常见目录发现。便携版未识别时，在高级选项填写一次 `blender.exe` 路径，后续记住。Blender 本体与模型服务 API Key 仍需由用户准备；本功能不自动下载 Blender 软件。

## 随应用附带的内容

- `resources/blender/server/forma-blender-mcp.exe` 及其自包含 Python 运行库。
- 固定版本 `mcp-for-blender==2.2.7` 的 Blender 插件与匹配 MCP 服务。
- `forma_start.py`，只在本次由 Forma 新开的 Blender 实例加载插件。
- 插件所需的纯 Python 依赖，避免向 Blender 的 Python 环境临时安装依赖。
- 手动安装备用插件 ZIP、依赖版本清单、许可证文件和文件 SHA256 清单。

运行时不通过 uvx 下载，不需要网络安装依赖。在线素材、付费生成服务及用户配置的模型 API 本身仍可能需要联网。

内置服务使用明确的配置标记，安装升级或移动便携版目录后自动跟随新的资源路径；外部自定义 MCP 程序路径保持原样。旧 uvx 配置保留为外部服务，点击一键启动会独立创建或复用内置服务，不静默覆盖旧配置。

## 现有工程与退出行为

一键启动打开新的独立 Blender 图形界面，使用进程内的默认启动状态；既有 Blender 窗口、文档和插件设置保持原样。不自动接管、关闭或重置用户已打开的 Blender 场景。

插件只对本次新进程生效，不保存用户插件偏好，也不改写 Blender 默认启动文件。退出 Forma 时断开 MCP，Blender 窗口保留，请自行保存模型。重新启动 Forma 后点击一键启动会建立新的独立窗口；原窗口中的未保存内容保持原样。

每次工具调用继续使用单次操作确认与检查点；取消或异常不代表已回滚。工程闭环仍为 SolidWorks 专用，Blender 使用自己的场景、尺寸、网格和保存检查。

## 状态与故障

- 自动分配空闲 loopback 端口，不要求用户填写 9876，也不暴露到公网。
- 初始化工具目录后，另外读取场景并核对本次实例标记；发现工具不等于场景已连接。
- 启动失败、超时或场景身份异常会明确提示，不把连接程序启动当成建模成功。
- Blender 启动日志在应用用户数据的 `extensions/blender-start.log`，不传递模型 API Key。
- 资源缺失或哈希不匹配时停止启动，提示重新安装完整应用，不回退到联网下载。
- 多次点击复用当前进程和配置，避免重复打开窗口；执行任务期间接入配置锁定。
- 高级选项仍可使用自定义 MCP，但正常用户无需进入这些表单。

## 开发和发布

`./scripts/build-blender.ps1` 在独立构建环境下载并校验固定根版本及兼容依赖，编译自包含 MCP、复制匹配插件与纯 Python 依赖、收集许可证并生成完整性清单，然后以真实 stdio 协议发现工具。

`./scripts/build.ps1` 默认包含这一步。`-SkipBlenderBuild` 只复用已生成的 Blender 资源，仍执行校验，缺失时阻止发布。桌面 PyInstaller 配置强制包含整个 Blender 资源目录，因此安装包与便携包使用同一份离线资源。

构建依赖独立于桌面应用的 MCP 客户端版本，避免升级 Blender 服务依赖时改变已有 SolidWorks 与外部 MCP 调用链。实际解析的构建依赖及许可证随资源保存。

## 验证边界

自动化回归使用合成进程/本地 MCP 测试服务，覆盖内置标记、移动路径、资源完整性、一键启动、去重、场景身份、失败和鉴权。冻结的真实第三方 MCP 服务单独执行 stdio 协议验证。

本机尚未安装 Blender；真实 Blender 图形界面启动、插件加载、网格建模、视口和保存导出仍需目标机器验收。编译与协议通过不等于已完成 Blender 真机场景验收。

上游资料：https://github.com/ahujasid/mcp-for-blender
固定版本：https://pypi.org/project/mcp-for-blender/2.2.7/
Blender 下载：https://www.blender.org/download/
