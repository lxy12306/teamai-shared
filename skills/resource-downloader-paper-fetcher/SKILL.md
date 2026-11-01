---
name: resource-downloader-paper-fetcher
description: 检索 OpenAlex、arXiv 与 Semantic Scholar 的论文，按主题归档并列出已保存论文主题；只读核对论文 DOI 后可从开放来源获取论文 PDF，或按明确请求尝试 Sci-Hub。用于论文检索、论文 DOI 核对、归档与论文 PDF 下载。
---

# 论文检索与获取

套件修订：`2026-11-01.2`。这是“资源下载者”的独立子技能，也可直接调用；不需要先读取父技能。

## 先确定结果与范围

- 区分**只回答检索结果**、**只读核对论文 DOI**、**保存主题清单**、**按 DOI 获取一篇**、**列出已保存论文主题**、**下载已保存的一篇**和**下载整个主题**。凡明确只看结果或不保存，使用 `search --no-save`、`verify` 或 `list` 的相应只读流程，不运行保存或下载命令。只指定一篇时不要扩成批量。下载默认用开放来源；只有用户明确要求 Sci-Hub 时才启用它。
- 需要保存或下载时，使用用户指定的输出目录；若未指定而当前项目明确是目标，可在项目内用 `papers/`。目标项目或写入位置不明时，先给对话中的候选结果并询问保存位置，不把论文写入技能安装目录。输出目录应由当前用户独占写入；若其他进程并发替换路径重解析点，脚本无法完全规避该风险。
- 保存或下载前查看输出目录的 `resource-downloader-index.json`；列出已保存论文主题时使用该输出目录。若同处还有旧版 `.index.json`，只读参考，不自动迁移或覆盖。只有技术路线与应用领域都匹配，或用户明确指定同一主题时，才沿用已有 topic；仅有宽泛关键词重合时新建 topic。`--topic` 必须是小写 ASCII slug（字母、数字、连字符）；已有 topic 上的 `fetch` 保留原检索词 `query`。向用户说明本次沿用或新建的理由。只读检索无需本地索引。
- 记录题名、作者、年份、DOI / arXiv ID、来源页、开放 PDF 候选及检索时间。用 DOI、arXiv ID 与规范化标题去重；明确的 arXiv v1/v2、明确版本与无版本记录，以及仅有 DOI 的元数据与明确 arXiv 版本，都不自动合并。候选保留提供者、版本和可用的许可信息。缺字段时保留可追溯来源，不补造元数据。按 DOI 获取时，若用户同时给出题名，用 `--expect-title` 核对；不一致时停止，不把 DOI 恰好存在当成找到目标论文。

## 执行

有 Python 3.9+ 且用户要检索、核对 DOI、保存、列出本地主题或下载时，定位本技能目录，用该环境的 Python 运行 `scripts/papers.py`。脚本只使用标准库，CLI 默认输出 UTF-8，请求标识为 `resource-downloader-paper-fetcher/2026-11-01.2`；`verify` 与 `search --no-save` 无需 `--out`，其余命令的数据目录由 `--out` 指定，使用独立的 `resource-downloader-index.json` 和 `resource-downloader-results.*` 文件。不在本技能目录创建虚拟环境或配置。运行前先看 `--help`，并将下列占位符替换为真实路径与参数：

```text
<python> <本技能目录>/scripts/papers.py search --query <检索词> --out <输出目录> [--topic <主题名>] [--limit <每来源数量>] [--sources openalex,arxiv,semantic]
<python> <本技能目录>/scripts/papers.py search --query <检索词> --no-save [--limit <每来源数量>] [--sources openalex,arxiv,semantic]
<python> <本技能目录>/scripts/papers.py list --out <输出目录>
<python> <本技能目录>/scripts/papers.py verify --doi <完整论文 DOI> [--expect-title <预期题名>]
<python> <本技能目录>/scripts/papers.py fetch --doi <完整论文 DOI> --out <输出目录> [--topic <主题名>] [--expect-title <预期题名>] [--source open|scihub] [--scihub-base <可信的 HTTPS 镜像>]
<python> <本技能目录>/scripts/papers.py download --out <输出目录> --topic <主题名> --id <论文标识>
<python> <本技能目录>/scripts/papers.py download --out <输出目录> --topic <主题名> --all
<python> <本技能目录>/scripts/papers.py download --out <输出目录> --topic <主题名> --id <论文标识> --source scihub [--scihub-base <可信的 HTTPS 镜像>]
```

`search` 默认查询 OpenAlex、arXiv、Semantic Scholar；`--sources` 可缩小来源。`search --no-save` 不传 `--out` 或 `--topic`，打印结果且不保存 topic，但 arXiv 限速状态可能在本机更新。`verify --doi` 先在 OpenAlex 核对 DOI 与元数据，失败再查 Crossref，可用 `--expect-title` 核对题名，全程只读。`list` 纯读取索引，遇到 pending 更新时只在内存中反映。`fetch --doi` 核对后保存单篇记录并尝试 PDF；同 DOI 已有明确 arXiv vN 记录时，创建或选中 DOI 中性记录。`--source open` 为默认值，仅用户明确请求 Sci-Hub 时选 `--source scihub`，其 `--scihub-base` 仅能与 Sci-Hub 一起使用。`--all` 只在用户明确要求下载整个主题时使用。单篇用检索结果里的标识核对题名与来源；有多个候选或不能确定对应论文时再询问。

默认 `--source open` 先试已保存的开放 PDF 候选；失败时，有无 DOI 都可用已保存的 Semantic Scholar ID 重查单篇并核对身份，尝试重取开放 PDF 候选（包括短期签名 URL）。明确 arXiv vN 只尝试 URL 明确指向同一修订版的 arXiv 官方 PDF；即使 Semantic Scholar 元数据含 vN，其外部 PDF URL 也不自动算该版。此类记录不使用 DOI 级开放副本候选；其他候选失败后，可由规范化 arXiv ID 构造官方同版 PDF URL 再试。其他记录可用 DOI 查 OpenAlex 开放副本，并在设置本机 `UNPAYWALL_EMAIL` 时补查 Unpaywall；缺 DOI 时，只有 Crossref 的题名、完整规范化首作者和年份都一致才自动采用其 DOI，并将 Crossref 记为元数据来源。

`resource-downloader-results.json` 记录候选的提供者、版本、许可信息（来源提供时）、实际下载来源以及逐来源尝试和失败原因；每篇下载的终局失败也写入 `download_attempts`。`resource-downloader-results.md` 的元数据来源按实际保存记录生成；PDF 的 `available` 只在存在下载流程会尝试的候选时标示，不代表已下载或链接可访问。链接持久化采用保守筛选：可保留 PLOS 的公开 DOI 参数及 `download=1|true`、`format=pdf` 等固定参数；其他查询参数、用户信息与明显凭据路径会过滤，但无法可靠识别所有不透明路径令牌。若没有 DOI 可重新发现来源，稍后下载可能需要重新检索。某来源失败继续尝试其余候选；全部失败时保留论文元数据，报告未下载，不把候选链接当成已获取文件。

用户明确选择 Sci-Hub 时才用 `--source scihub`。Sci-Hub 无法确认明确 arXiv vN，故脚本拒绝这类记录；若要尝试 Sci-Hub，需选 DOI 中性记录。已保存论文有 DOI 时先核对 DOI 与题名，不一致则停止该篇；无 DOI 时才用符合要求的来源页查询。此模式无需账户登录，默认依次尝试 `.se`、`.st`、`.ru` 三个镜像；`--scihub-base` 可指定只用一个镜像。同一镜像页面有多个 PDF 候选时逐个尝试，全部失败再转下一个镜像；镜像返回 403、机器人验证页面或无 PDF 时也继续尝试下一个，最后报告各镜像状态。机器人验证是镜像的访问限制，不是本技能的账号认证；不自动破解。PDF 与 Sci-Hub HTML 连接均核对 HTTPS 与公网 IP，并固定连接到已验证的地址；PDF 传输设 50 MiB、180 秒限制，Sci-Hub HTML 设 2 MiB、90 秒限制。PDF 使用标准库筛查文件大小、标头和 `startxref` 指向的 xref 结构，这是结构初筛而非完整解析。只有文件通过校验才称下载成功。若网络仅允许代理而不能公网直连，PDF 下载可能失败。PDF 只存到用户的本地目录，不放入技能包或团队配置。

保存和下载命令在输出根目录加锁，并在继续操作前将未完成的 pending 主题更新恢复落盘；`list` 只在内存中覆盖索引，不触发落盘恢复。

若当前宿主不能运行 Python，仍可用可用的浏览或论文检索工具完成只读检索并给出来源链接；只有工具支持保存且写入位置明确时才称已归档或已下载。arXiv API 请求至少间隔 3 秒且不并发；若跨机器运行，也要协调总请求频率。Semantic Scholar 限流或某来源失败时报告已成功的来源及未覆盖范围，勿把部分结果称为完整检索；如用户已有 API Key，可由本机环境变量 `SEMANTIC_SCHOLAR_API_KEY` 提供，不写入技能包或结果文件。接口字段与限制以 [OpenAlex API](https://help.openalex.org/api/)、[OpenAlex 开放副本](https://help.openalex.org/data/locations/)、[Crossref REST API](https://www.crossref.org/documentation/retrieve-metadata/rest-api/)、[Unpaywall API](https://unpaywall.org/api)、[arXiv API 手册](https://info.arxiv.org/help/api/user-manual.html)、[arXiv API 使用规则](https://info.arxiv.org/help/api/tou.html) 和 [Semantic Scholar Graph API](https://api.semanticscholar.org/api-docs/graph) 为准。

本技能在 Codex 可用 `$resource-downloader-paper-fetcher`、Qoder 可用 `/resource-downloader-paper-fetcher`、Pi 可用 `/skill:resource-downloader-paper-fetcher` 直接调用；这些指令只在相应宿主实际发现本技能后有效。
