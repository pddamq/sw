# Skills 与外部 MCP

当前源码在左侧提供「Skills 与 MCP」页面，与内置 SolidWorks 执行器独立管理。
Blender 已有连接预设和独立建模后端选择，详见 [Blender MCP 接入说明](BLENDER_MCP.md)。
版本继续使用 0.9.0；此功能为后续源码更新，旧安装包不会自动获得这些修改。

## Skills：导入并调用工作方法

1. 打开「Skills 与 MCP」，选择「导入 Skill 文件夹」或「导入 SKILL.md」。
2. 每次选择一个 Skill 根目录。目录内须有且仅有一个 `SKILL.md`，参考文件必须在其目录内，导入后保留相对层级。
3. `SKILL.md` 顶部需要 YAML frontmatter，例如：

```markdown
---
name: design-review
description: Review mechanical design requirements and collect evidence before modeling.
---
Read references/checklist.md before reviewing.
Use available tools to verify actual geometry and document all unknown checks.
```

4. 导入后默认启用；同名 Skill 更新内容，保留原来的 ID 和启用状态。页面可查看正文、文件列表、启用、停用及删除。
5. 开始任务时只向模型提供启用 Skill 的名称和描述。匹配时通过 `skill_read` 读取正文，参考文件按需读取，每次最多 8,000 字符，长文通过 `next_offset` 分页。
6. 可以在消息中写 `/design-review 检查当前零件`。设置 `disable-model-invocation: true` 的 Skill 只有在任务起始消息中出现对应 `/name` 时进入目录；普通 Skill 允许模型自行选择。

限制：最多保存 64 个 Skill；每个 Skill 最多 128 个文件，单文件 256 KB，总计 8 MB。文件须为 UTF-8 文本，暂不支持 ZIP、二进制附件及目录中包含多个 Skill 的批量导入。

导入文件是指导文本，不是执行插件。`scripts/` 中的程序只可作为文本读取，不会自动运行。Skill 不会扩展已确认文档范围、跳过操作确认或替代实际模型校验。任务使用启动时的 Skill 快照，管理修改在任务结束后进行，后续任务读取新状态。

## MCP：接入外部工具

MCP（Model Context Protocol，模型上下文协议）用于让 Agent 发现和调用外部工具。

支持三种传输：

- `stdio`：运行本地 MCP 程序，配置启动程序、JSON 参数数组、工作目录及环境变量。
- `streamable_http`：连接远程 Streamable HTTP MCP URL，可配置请求头。
- `sse`：兼容 SSE MCP 服务，可配置请求头。

### 添加与连接

1. 点击「添加 MCP 服务」，填写名称与传输。
2. stdio 的启动程序只填程序名或可执行文件路径，参数单独填写为字符串数组，例如 `["server.py"]`；不要把整条 shell 命令塞入程序字段。远程服务填写 http(s) URL。
3. 凭据使用 env 或 headers，避免放入命令、参数和 URL。Windows 使用当前用户的 DPAPI 加密存储，界面不回显明文；留空保留旧值。勾选「清除旧凭据」可清除并重新填写；更换目标或启动参数时必须先清除旧凭据。
4. 保存仅持久化配置，不会启动或连接程序。点击「连接并发现工具」才建立连接；连接后可展开工具声明。
5. 向 Agent 描述服务名、工具名与任务。Agent 使用统一目录和 `discover_tools` 按需发现工具，工具名隔离为 `ext_<server-id>_<hash>`，不会覆盖 SolidWorks 工具。
6. 每次外部调用显示具体用途与参数，确认后执行。连接代次或工具声明变化会让旧确认失效。

最多保存 16 个外部服务，每个服务最多 256 个工具。连接失败保留配置，可修正后重连。保存已连接服务会断开旧连接，需要手动重新连接。应用重启后服务配置仍在，外部服务需手动连接。

### 标准 JSON 导入

点击「导入 MCP JSON」，粘贴 `mcpServers` 配置：

```json
{
  "mcpServers": {
    "local-tool": {
      "command": "python",
      "args": ["C:/tools/example_server.py"],
      "env": {"EXAMPLE_TOKEN": "replace-me"}
    },
    "remote-tool": {
      "type": "http",
      "url": "https://example.com/mcp",
      "headers": {"Authorization": "Bearer replace-me"},
      "disabled": true
    }
  }
}
```

示例路径和 URL 需替换为实际 MCP 服务。导入是原子操作：任一声明无效时不导入这批配置。重复导入会新增服务，不会按名称覆盖现有服务。成功后清空输入框，仍须逐项手动连接。当前不包含 OAuth 授权界面、MCP resources/prompts、自动运行 Skill 脚本或自动重连。

## 执行边界与失败处理

- 外部服务与内置 CAD 执行器独立连接、独立状态；无需 SolidWorks 运行，也能使用外部工具。
- 外部工具不继承 CAD 工具的只读判定，每次都单独确认；工程闭环与只读恢复任务中暂不执行外部工具。
- 导入的工具说明、Skill 文本与外部返回均不构成工程放行证据。
- 调用前保存操作检查点；外部进程退出或结果未知时停止任务，不自动重放；取消等待已派发调用收尾，不声称撤销外部效果。
- HTTP/SSE 使用同源请求检查，拒绝向另一个来源转发请求及凭据。远程 JSON Schema 只接受本地引用。
- 日志与返回文本会遮盖已配置的凭据，stdio stderr 不直接持久化。配置文件、对话和工具结果仍可能包含业务数据。

## 验证范围

离线自动测试覆盖 Skill 校验、层级与分页、快照、启停、配置导入、凭据存储、三种真实 MCP 传输、本地逐调用确认、取消、断连、发现与 Agent 调用，以及 Vue 管理界面交互。协议测试使用本地测试服务，不连接实际第三方账户，也不以测试服务代替 SolidWorks 真机验收。
