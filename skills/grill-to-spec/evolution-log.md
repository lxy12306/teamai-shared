# 演进记录

## 2026-09-29

- 动机：将需求压实与实现计划收敛为单一入口，避免跨 skill 交接断层。
- 变更：合入 S5 `to-plan` 与 HTML 规范，移除独立计划 skill；工单可选，已确认 spec 可直接进入计划。
- 影响：保留计划版本、diff、Task / Step 与本地 review HTML 格式；仅修改本仓库，不同步全局安装。
- 教训：文件存在不等于需求已确认；阶段恢复须校验内容，上传须另获授权。

## 2026-10-29

- 动机：减少无效追问与跨阶段停顿，修复领域模型空路由、需求来源过窄、工单依赖矛盾、计划脚本缺失和不可离线审阅的 HTML。
- 变更：S1 只追问影响本次交付的决策并记录证据/状态；已授权的 S1–S5 可连续执行，支持直接从明确需求进入目标产物；新增领域模型格式与双向追溯检查；计划仍保留版本、diff、Task/Step、Files/Interfaces，但代码片段按风险提供；本地 review HTML 改为无网络依赖。未安装或同步。
- 参考一手实现：[Patina grill-to-spec](https://github.com/patinaproject/skills/blob/main/plugins/work/skills/grill-to-spec/SKILL.md)、[vvanagas grill-me](https://github.com/vvanagas/agent-skills/blob/main/skills/grill-me/SKILL.md)、[Spec Kit clarify](https://github.com/github/spec-kit/blob/main/templates/commands/clarify.md) 与 [analyze](https://github.com/github/spec-kit/blob/main/templates/commands/analyze.md)、[OpenSpec OPSX](https://github.com/Fission-AI/OpenSpec/blob/main/docs/opsx.md)、[Superpowers writing-plans](https://github.com/obra/superpowers/blob/main/skills/writing-plans/SKILL.md)。仅借鉴流程机制，未复制源码或模板。
- 前向演练：S1 首问、领域模型、spec→工单及直接写计划均完成；浏览器检查发现 Windows UTF-8 BOM 让 HTML 页头露出字面 `#`，增加 BOM 处理规则并重生成同版 HTML 后复查通过。目标宿主发现与自动触发仍未测试。

## 2026-10-29 · 复审后回归

- 动机：六视角评审给出 7.8/10，指出来源定位、确认主体、敏感信息与跨宿主验证的缺口。
- 变更：决策和需求记录可定位来源，`agreed` 核对有权主体或适用的生效契约；S5 增加敏感信息最小化、输入授权边界、代码库基线和执行前重检，保留版本、diff 与默认离线 HTML。
- 实测修复：Qoder 首问曾在无项目证据时给倾向性推荐；S3 曾把已有能力写成不存在、混入无关语言术语，并把影响本次 R 的开放问题标非阻塞；S5 曾编造未运行测试的失败数并留下临时代码。增加对应决策与交付检查后，同输入 Qoder S1、S3、S5 回归通过。
- 验证范围：Codex 结构校验和本地隔离前向演练通过；Qoder CLI 隔离副本的发现、引用文件加载及 S1/S3/S5 任务通过；Pi 隔离副本只读 S1 与资源加载通过。Pi 的 S3/S5 写入、三宿主自动触发及本轮 HTML 浏览器视觉检查未验证。未安装或同步草稿。

## 2026-10-29 · 长计划 HTML 回归

- 动机：第三轮评审为 8.1/10；10 Task、30 Step 的 Qoder 长计划转换超过三分钟无 HTML，不能用短计划成功概括长计划。
- 变更：新增仅依赖 Python 3.8+ 标准库的 `scripts/render_plan_html.py`，可执行宿主优先确定性渲染；无执行能力时按 Task 分批生成并逐段核对，失败保留 Markdown/diff 且不宣称 HTML 完成。脚本拒绝覆盖既有 HTML，先在同目录暂存并校验，再独占创建目标。
- 验证：同一 BOM/CRLF 压力夹具生成 Task 10、Step 30、Expected 30、代码块 10、Run 30，30 个唯一预期标记齐全；无外部资源和绝对用户路径。独立审计复现并推动修复表格管道丢内容、反斜杠危险链接和代码围栏误判；恶意 HTML、重复输出与临时文件清理复测通过。浏览器 Computer Use 因无法确认当前 Chrome URL 停止，本轮未作视觉验收；Qoder 无脚本执行权限的长计划路径仍未证明能完成。
- 第四轮独立复审：8.3/10；固定 S5 产物成本仍是既定取舍。无 Python 执行能力时的长计划分批 HTML、Pi 的写入流程和三宿主自动触发尚未通过实机验收，按未验证处理。
