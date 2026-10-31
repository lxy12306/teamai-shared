# 计划 review HTML

把本次 Markdown 计划 `{PLAN}` 渲染为便于审阅的单文件 `{PLAN_HTML}`。它是同版计划的视图，不是第二份事实来源；任务、代码片段、验证命令与期望结果须忠实于 Markdown。默认保存在目标项目本地，用户要求上传时按其指定目的地另行处理。

## 文件与内容

- 完整 `<!DOCTYPE html>`，`lang="zh-CN"`、UTF-8 和 viewport 元信息；CSS 内联，使用系统字体栈。不要依赖 Tailwind CDN、Google Fonts、外部脚本、图片或网络请求，断网仍可审阅。
- 以 UTF-8 读取源 Markdown；若开头有 BOM，先移除再解析标题。兼容 CRLF/LF，首行 `# 标题` 必须渲染为可见的 `<h1>`，不能把 BOM 或字面量 `#` 留在页面标题中。
- 标题和版本、Goal / Architecture / Tech Stack / Source、Codebase Baseline、Global Constraints、任务目录、全部 Task 与 Step、Files / Interfaces、Run / Expected、源 Markdown 在项目内的相对路径和生成日期均须呈现。Markdown 缺少的可选字段不虚构；不展示机器上的绝对用户目录。
- 文字、路径、命令、代码和属性均按 HTML 上下文转义；来自计划的内容不能变成脚本、标签或事件处理器。目录锚点用受控 ID 生成，避免把原始标题直接拼入属性。
- HTML 忠实呈现已做敏感信息最小化的 Markdown；若源计划仍含真实凭据或非必要个人信息，先按计划版本规则修正文，再生成对应版本视图，不只在 HTML 中遮盖。
- 保留代码块换行与语言标识，长路径和代码可横向滚动；目录锚点可跳到对应任务。不要因排版删减较长的任务或检查项。

## 生成路径

- 已完成 Markdown 的敏感信息检查后，宿主可执行 Python 3.8+ 时优先用本 skill 的 [渲染脚本](../scripts/render_plan_html.py)，用法见 [S5 落盘步骤](../workflows/to-plan.md)。它只支持本技能计划格式，不把任意 Markdown 当成可无损转换；脚本失败或目标 HTML 已存在时先查原因，不覆盖同版产物。
- 无脚本执行能力时，按源计划 Task 顺序分批生成 HTML：先页头、约束与目录，再逐 Task 写入并核对该段的 Step、代码块、Run 和 Expected；不要一次让模型输出整份长 HTML。只有最终数量与内容对照通过才落最终路径或称 HTML 完成。无法可靠暂存、合并或验收时，保留已完成的 Markdown/diff，报告 HTML 未完成；不假定三个宿主都可运行 Python。

## 阅读布局

- 单栏正文，桌面宽度约 820–900px，窄屏留合理边距；页头与约束区先让审阅者看到目标和硬约束，再列任务目录。
- 用清晰标题、间距、细边框与少量语义色区分 Task、Files、Run 和 Expected。颜色不能是唯一信息载体，文字标签必须保留。
- 代码使用等宽系统字体与浅底，行内路径可换行；键盘焦点可见，目录链接文字能独立说明目标。

## 最小骨架

```html
<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>实现计划</title>
  <style>
    :root { color-scheme: light; font-family: system-ui, sans-serif; color: #1e293b; }
    body { margin: 0; background: #f8fafc; }
    main { max-width: 56rem; margin: auto; padding: 2rem 1rem; }
    header, nav, article, section { background: white; border: 1px solid #cbd5e1; border-radius: .75rem; padding: 1.25rem; margin-block: 1rem; }
    pre { overflow-x: auto; padding: 1rem; background: #f1f5f9; }
    code, pre { font-family: ui-monospace, Consolas, monospace; }
    :focus-visible { outline: 3px solid #2563eb; outline-offset: 2px; }
  </style>
</head>
<body><main><!-- 经转义的计划内容 --></main></body>
</html>
```

## 验收

生成后将 HTML 与源计划逐项对照：`<h1>` 标题、版本、代码库基线、约束、Task / Step / 代码块 / Run / Expected 的数量和内容均一致，不露出 Markdown 标记或 BOM。用可用浏览器打开**最终本地文件**，检查目录跳转、长代码滚动、窄屏阅读和运行时错误；浏览器不可用时报告仅完成结构检查，不声称视觉验收。上传前再检查最终 HTML 中的敏感信息与源路径。不要为了“单文件”把外部资源复制进来而未核对许可。
