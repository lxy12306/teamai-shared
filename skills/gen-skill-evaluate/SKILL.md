---
name: gen-skill-evaluate
description: 使用 NVIDIA SkillEvaluator 评测 Agent Skill。用于静态质量评分、检查 skill 内或多个 skill 间的语义重叠，或在沙箱中对照 Agent 启用 skill 前后的任务表现。
---

# Gen Skill Evaluate

套件修订 `2026-11-17.2`（与 `gen-skill` 配套，也可独立调用）。

## 选择评测

定位实际待评测的 `SKILL.md`。用户只说“评测”或“评分”时，运行静态质量检查。排查重复指令或技能重叠时用语义检查。测量触发、任务收益或版本回归时用实跑对照。

先用环境已有的 CLI，运行 `skillevaluator --version`。本机 Windows 的基础版不在 PATH 时，PowerShell 用 `& "$env:LOCALAPPDATA\gen-skill-evaluate\venv\Scripts\skillevaluator.exe"` 替换示例的命令名。缺少 CLI 时，在 Python 3.12–3.14 的隔离环境用 `python -m pip install "skillevaluator==0.9.0"`，进阶工作流用下文对应的 extra。0.9.0 是以下命令的核对版本，用其他版本时先核对选项。

`<skill-dir>` 是包含 `SKILL.md` 的目录。把 `<report-dir>` 指向该目录以外的独立输出位置。对照试跑和正式跑各用一个位置。涉及客户或内部材料时，将原始报告与轨迹放在受限目录，分享前检查并脱敏成功与失败记录，按任务约定清理临时合集。

## 静态质量检查

```sh
skillevaluator quality-check "<skill-dir>" -r cli -r json -o "<report-dir>"
```

这项检查可离线执行，无需 API Key、Docker 或额外扫描器。读取 `skillevaluator-quality.json`，直接告诉用户得分（满分 100）、等级、通过状态、主要发现和报告位置。非零退出时，区分质量未过、找不到清单和运行错误。无可读报告或无得分时，说明原因和实际状态。中文说明可能被英文触发词或标题规则误判，按语义复核。安全扫描与任务表现各有独立的证据要求。

## 语义重叠

在隔离环境用 `python -m pip install "skillevaluator[tier2]==0.9.0"`。按 [模型配置](https://docs.nvidia.com/skills/skillevaluator/configuration#embeddings) 配置 embeddings。skill 内检查另需聊天模型，可用 `skillevaluator doctor --tier 2` 检查配置。点名多个 skill 时，把其清单按各自目录放入只含目标的临时合集，核对清单数和名称，再运行合集检查。已配置模型服务也需适用本次数据。外部调用前确认可提交哪些描述或正文，必要时先脱敏。

```sh
skillevaluator context-optimization-check "<skill-dir>" -r cli -r json -o "<report-dir>"
skillevaluator similarity-check "<skills-dir>" --type skill -r cli -r json -o "<report-dir>"
```

合集默认按描述筛选候选。结果只覆盖描述层面。输入范围允许且需正文证据时加 `--full-body`。按相似片段、职责和触发条件判断是否真的重叠。仅有几个明确候选或没有合适模型时，可人工对照并标明采用的方式。用户指定 NVIDIA 语义评测而条件不齐时，标明具体缺项。

## 实跑对照

1. **确认运行条件。** 配置评分 provider、选定 Agent 的认证和隔离后端。在运行环境用 `python -m pip install "skillevaluator[tier3]==0.9.0"`。用与实跑一致的 Agent、模型和后端做预检，再启动模型生成或评测。下列命令以 Codex 和 Docker 为例。将 `<model-id>` 换成已配置的实际 Agent 模型，两处取值一致。预检确认依赖和配置，推理与评分链路由试跑确认。缺少 Harbor、模型配置或可用后端时，交付静态结果和手写案例，逐项报告触发、收益或回归有哪些未验证。如能在目标宿主观察调用，可先手动验证少量正负例，记录宿主和覆盖范围。本机基础 Windows 环境可用 Docker 后端或符合条件的 WSL2/Linux 环境补齐隔离能力。
2. **准备可判定的案例。** 从真实任务选少量显式调用、自然措辞、负例和相关边界案例，为手写、复用和生成的案例统一写明可观察的任务结果与断言，技能调用预期单列。正例的 `expected_skill` 填目标 skill 名，负例填 `null`。检查已有 `should_trigger` 与预期是否一致，因为它优先。首次执行前检查输入、评分脚本、Docker 配置与工作区挂载，限定凭据和网络范围。外部调用前核对案例生成、Agent 推理和评分各环节可接收的数据，包括正文、指引、运行输入和评分所需输出。必要时脱敏。可按 [案例格式](https://docs.nvidia.com/skills/skillevaluator/eval-datasets#dataset-format) 手写 `evals/evals.json`，或在隔离副本中用 `skillevaluator create-eval-dataset "<skill-dir>" --full --prompt "<eval-guidance.md>"` 起草，逐例审核正负标签、输入和断言。用 `skillevaluator tier3 validate "<skill-dir>" --json` 核对清单和覆盖，修复错误后运行。复用自定义评分时，预检后的试跑与正式评测使用实际评分模式。原生 Harbor 任务按需增加 `--harbor-contract` 校验。
3. **先小试，再用同条件对照。** 下例最多跑清单的前三例，先把代表性案例排进试跑集合，测触发时覆盖正例和负例。确认两组都成功执行、评分有证据且目标 skill 只在启用组可发现，再扩大规模。正式评测将 `--max-cases` 设为审定案例数，并核对实际运行的案例 ID。启用和未启用组使用相同 Agent、模型、案例、评分规则和尝试次数。一个 Agent、三例、每组各一次计划六次任务试验，评分调用与重试另计。扩大规模前按案例数 × Agent 数 × 每例尝试次数 × 两组估算任务试验，必要时用 `--max-attempts-total` 限制逻辑试验数。用于正式验收时，预先按用户目标或项目约定写明关键必过案例、可接受的触发错误或收益门槛，以及最低有效样本、汇总口径和需要复跑的条件。

```sh
skillevaluator doctor --agents codex --agent-model "codex=<model-id>" --env-mode docker
skillevaluator tier3 evaluate "<skill-dir>" --agents codex --agent-model "codex=<model-id>" --env-mode docker --grading-mode default --n-attempts 1 --max-cases 3 --results-dir "<report-dir>"
```

4. **核对结果。** 从输出指定的实际 run 目录读取报告。需要结构化证据时运行 [NVIDIA 的离线触发导出示例](https://docs.nvidia.com/skills/skillevaluator/tier3-live-evaluation#repeatable-activation-validation)。在 Windows 上可将示例中 Python 正文存成临时脚本，用同一环境的 Python 传入 skill 路径和 run 路径。逐例按运行组对照 `activation_validation.expected_activation` 与可信轨迹的 `observed_activation`。启用组中，预期触发而未调用是漏触发，预期不触发而调用是误触发。未启用组预期不调用，单列为基线核对。`unknown`、缺少记录或不完整轨迹记为证据不足。仅自定义评分时，触发需要额外的宿主证据。单独报告两组的任务得分和 Skill Lift。基线缺失、失败或可比条件改变时，交代影响与可比范围。试跑结果标明案例数、实际尝试数、失败和截断情况。旧版与新版使用同一审定案例集和配置，各保留结果。

## 交付结果

交代目标、版本、实际执行的评测、分数或指标、主要发现和原始报告位置。说明依赖、证据或覆盖不足的项目。用户要求按评测结果迭代时修改目标并复测受影响的检查。

[安装说明](https://docs.nvidia.com/skills/skillevaluator/installation) · [CLI 参考](https://docs.nvidia.com/skills/skillevaluator/cli-reference) · [实跑指南](https://docs.nvidia.com/skills/skillevaluator/tier3-live-evaluation)
