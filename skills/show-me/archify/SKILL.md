---
name: archify
description: Create polished, validated architecture, workflow, sequence, data-flow, and lifecycle/state diagrams as explorable standalone HTML with inline SVG, dark/light themes, optional trace motion, and PNG/JPEG/WebP/SVG/WebM export. Accept plain-language requirements or pasted Mermaid flowchart, sequenceDiagram, and stateDiagram input; inspect repository evidence when the diagram must reflect real code. Use when the user asks to visualize system architecture, infrastructure, cloud/security/network topology, technical workflows, API call sequences, request lifecycles, data pipelines, ETL/ELT, data lineage, state machines, or to convert/beautify Mermaid.
license: MIT
metadata:
  version: "3.0"
  author: tt-a1i
  based_on: Cocoon-AI/architecture-diagram-generator (MIT, v1.0)
---

# Archify

Create an interactive HTML diagram from typed JSON. Static output is the default; enable motion only when requested.

Run commands from your working directory. Unless the user names another location, give each new diagram request its own folder `.archify/<type>-<slug>-<YYYYMMDD-HHMMSS>/` there (local time, chosen once when the request starts): keep `candidate.json` and `<slug>.html` in it, set `meta.output` to that relative HTML path, and reuse the folder for every repair rerun. Before creating the folder, ensure the working, HTML output, and evidence directories resolve to a real local filesystem that supports the delivery lock protocol. If the current directory is on NFS, SMB, another network filesystem, or lacks those file operations, choose a local working/delivery directory and run Archify there; use an absolute `--repo-root` for source code elsewhere. A later request gets a new folder, so earlier versions stay intact. Replace `bin/archify.mjs` and `scripts/basic-interaction-smoke.mjs` in the commands below with the installed package's absolute paths, or paths relative to your working directory; input and output paths resolve from that working directory.

For a real codebase, read [Repository authoring](references/repository-authoring.md) before authoring: its provenance check decides whether Archify can substantiate the requested source bytes. A system description uses the steps below; an existing JSON uses the handoff path.

## Existing candidate handoff

When the user supplies an existing candidate that claims real-code evidence, first apply [Repository authoring](references/repository-authoring.md)'s provenance check to the bytes the user wants represented. Use its `meta.quality_profile` for `--quality` (`showcase` if absent). If the user explicitly requests dense `standard`, set that field to `standard` before treating the candidate as frozen. Run `finalize` first as one CLI invocation. Its passing receipt completes the automated gates; for repository evidence, that receipt covers the pinned commit only. Follow any visual review recommendation under Delivery before claiming visual quality. For repair, follow step 5.

`finalize` includes a bounded update check in its delivery receipt; see Update awareness.

## Fast authoring path

Use this path for ordinary generation. Read branch references only when their stated trigger applies.

1. Choose `architecture`, `workflow`, `sequence`, `dataflow`, or `lifecycle` from the question.
2. Use the exact schema and example paths in the Type router without listing their directories. Read [Authoring defaults](references/authoring-defaults.md) and the mode's example in a bounded batch separate from project documents and complete schemas so neither is truncated; recover any missing section before writing. For Architecture, use the matching Type router example and set its quality profile according to step 3. For Sequence, Dataflow, and Lifecycle, also read the mode and common schemas. Read the relevant schema definition before choosing any new field, enum, or constrained text, especially boundary kinds. Examples teach shape, not facts. Use fresh IDs, wording, and layout. Go directly to the candidate without preliminary help, doctor, starter validation, temporary diagrams, or output-path listing. Query brands only for an explicitly requested mark; read [Brand marks](references/brand-marks.md) for an unknown mark with a user-provided URL.
3. Once the requested scope and, for a real codebase, [source evidence](references/repository-authoring.md) are covered, write the complete candidate directly without planning coordinates in prose. Choose Architecture abstraction and connected placement using Authoring defaults before coordinates: show the main user journey and necessary branches, preserve control roles and behavior-changing conditions, and leave enough room for actual relationship labels. No node, relationship, source, view, card, or boundary count is a target or ceiling. Use automatic routes first; add explicit routing only for necessary branch, return, supplied geometry, or measured repair. Select one quality profile: `showcase` by default, or `standard` when the user explicitly requests dense `standard`. Set `meta.quality_profile` to that value. Diagram density alone does not authorize `standard`; it can pass with warnings about clipped labels, hidden relationships, ambiguous arrows, or unreadable text.
4. Once the complete first candidate is written, run `finalize` directly. Its first gate validates the selected quality profile; successful first drafts need no separate pre-validation. Keep the candidate unchanged while the command runs. Replace `<quality-profile>` below with the literal `showcase` or `standard` selected in step 3:

   ```bash
   node bin/archify.mjs finalize <type> <candidate.json> <output.html> --quality <quality-profile> --json
   ```

   For a repository-backed candidate, include evidence on the first draft and use the complete first command: `node bin/archify.mjs finalize <type> <candidate.json> <output.html> --repo-root <repo-root> --quality <quality-profile> --json`.

   A passing receipt proves the included `validate`, `deliver`, strict `check`, and real-browser `browser-check` gates passed. Use its compact summary; run standalone commands only for a separate request or focused failure diagnosis.

5. A non-zero exit is never success. Read compact stdout or `evidence.summaryReceipt`, then [repair the failed gate](references/delivery-contract.md#failed-finalize-and-candidate-repair), including its repair limit. Preserve requested meaning and source evidence. For several tangled Architecture routes, read [Architecture layout repair](references/architecture-layout-repair.md); for measured field or geometry failures, read [Authoring contract](references/authoring-contract.md). Edit the connected neighborhood and rerun the complete `finalize` command from step 4 with the same quality profile as `meta.quality_profile`. If required density or fixed geometry still prevents a showcase pass at the repair limit, keep the candidate and report the exact diagnostics and viable scope or profile choices under Delivery; never silently switch profiles or present a failed candidate as delivered.

## Update awareness

`finalize` and standalone `deliver` include `update` in their receipts. Do not run a separate check for the same delivery. If `update.noticeRequired` is true, read `references/update-awareness.md` and keep one update line in your final response to the user, even after a quality gate fails. For a task with several diagrams, mention the update once in the final response. Snooze or ignore a reminder only when the user explicitly asks; never install or update on your own initiative.

Before the first candidate, use the authoring references and relevant repository source, not Archify implementation or tests. Inspect Archify implementation if diagnostics remain unactionable after focused repairs.

## Type router

| Type | Use for | Schema | Example |
|---|---|---|---|
| `architecture` | Components, services, cloud/security boundaries, infrastructure | `schemas/architecture.schema.json` | System descriptions, services, libraries, and CLI repos: `examples/web-app.architecture.json`; deployment repos: `examples/production-deployment.architecture.json` |
| `workflow` | Processes, approval gates, tool calls, runbooks, CI/CD | `schemas/workflow.schema.json` | `examples/agent-tool-call.workflow.json` |
| `sequence` | API call chains, request lifecycles, async traces, returns | `schemas/sequence.schema.json` | `examples/cache-miss-request.sequence.json` |
| `dataflow` | Pipelines, ETL/ELT, lineage, governance, consumers | `schemas/dataflow.schema.json` | `examples/product-analytics.dataflow.json` |
| `lifecycle` | State/status transitions, retries, waiting and terminal states | `schemas/lifecycle.schema.json` | `examples/deployment-release.lifecycle.json` |

When ambiguous, run `node bin/archify.mjs guide "<scenario>" --json`. Scenario proof examples are structural references, not facts to copy.

## Mermaid input

Read Mermaid for topology and meaning, then author fresh Archify JSON; do not mechanically render Mermaid styling.

- `flowchart` / `graph` → `workflow`, or `architecture` for a component map.
- `sequenceDiagram` → `sequence`; participants become semantic participants and arrows become messages.
- `stateDiagram` → `lifecycle`; states and transitions retain meaning, not Mermaid style.

## Delivery

Use the `finalize` command above for the first candidate and after a repair.

`finalize` stops at the first non-passing gate. Its compact stdout and `<output-stem>.finalize-summary.json` are ordinary evidence. A passing run creates no screenshots and reports `visualReview: "not-requested"`.

When a passing Architecture receipt reports `visualReviewRecommendation.signals.resolvedCrossovers`, copy the candidate aside and apply the hints in one edit that changes only node positions and sizes: every node, relationship (including its `from` and `to`), label, and source stays as it was. Rerun the complete `finalize` once with `--out-dir <folder>/review-2`, because the previous HTML already owns its browser evidence. If that run fails or reports more crossings, restore the copy and finalize it with `--out-dir <folder>/review-3`. Do not start a second placement round. Hints about extra bends alone are optional.

When `layoutReviewRecommendation.action` is `inspect-sequence-width`, follow [Sequence width review](references/delivery-contract.md#sequence-width-review) before handing off a newly authored Sequence.

Perceptual review is optional for ordinary generation, including a newly positioned Architecture. Use [Optional capture evidence](references/delivery-contract.md#optional-capture-evidence), with `--out-dir <folder>/visual-check`, when the user requests visual review, during development audits, or for a concrete route/browser concern. `visualReviewRecommendation` is advisory. Inspect captures before claiming visual quality; otherwise report automated checks only.

For a standalone diagram routed from Show Me, perceptual review is required after the final successful `finalize` and interaction smoke: run `node bin/archify.mjs visual-check <output.html> --summary --require-provenance`, inspect the actual screenshots/contact sheet, and inspect any additional distinct layout or user-specified viewport that the captured views do not cover. If the page scrolls vertically, open the final HTML and scroll through the offscreen cards and diagram content; first-screen captures do not establish their readability. Check whether the main path, relationship directions, labels, and controls are understandable. A capture receipt alone is not perceptual approval. Repair visible defects and rerun the affected final gates; if inspection cannot be performed or a defect remains, hand off only a draft. This Show Me requirement does not change the optional review rule for ordinary Archify use.

Read [Delivery contract](references/delivery-contract.md) for failed gates, standalone commands, provenance/recovery, repeated delivery, exports, or opening. Recovery follows `deliver` → strict provenance `check` → `browser-check`; captures require strict provenance.

For workflow viewport overflow, read [Workflow viewport repair](references/authoring-contract.md#workflow-viewport-repair) before the next layout edit.

For a standalone explorable diagram routed here from Show Me, run `node scripts/basic-interaction-smoke.mjs <output.html> --json --all-viewports` on the final HTML after its last passing `finalize`. The flag checks 1440×900, 1600×1000, 1920×1080, and 2048×1320; require a passing result for each viewport, since any failed viewport fails the whole smoke. These four viewports establish desktop scope, not mobile usability. Test any additional user-specified viewport separately with real browser interaction. When mobile use is requested, scroll to representative nodes, expand any collapsed relationship list, activate the node, check its detail and relationship feedback, return to the full graph, and check readability, operability, and runtime errors at the requested mobile viewport; the desktop smoke receipt cannot stand in for this test. Omitting `--all-viewports` retains the single 1440×900 viewport behavior for existing callers. By default, require at least one relationship plus passing node activation, matching detail, visible relationship feedback, and return to the full graph; a zero-relationship diagram fails. Only when the user goal or checked source/scope evidence establishes that no relationship should be drawn, add `--expected-no-relationships` alongside `--all-viewports`. That mode requires zero rendered relationships and still checks browser node activation, matching detail, the visible empty-relationships state, and return to the full graph at each viewport. In its receipt, `feedback: "not-applicable"` is not a passed relationship-feedback check; require `detail`, `emptyRelationships`, and `returnToGraph` to pass for every viewport. This is a cross-viewport smoke of one representative node, not acceptance of every node; when node or relationship interaction differs by type, actually operate at least one example of each type and report the sampled and untested scope. Test any user-specified interaction separately. It is separate from `finalize` and its four gates; read [Basic interaction smoke](references/delivery-contract.md#basic-interaction-smoke) for failure handling. If the real browser cannot run, the topology lacks the required evidence, or the interaction fails at any viewport, report the artifact as a draft with the concrete failure, even if `finalize` passed.

Report artifact checks, browser evidence, captures, and actual perceptual review as distinct results. For an explicitly requested immediate preview or active desktop loop, see [Optional opening](references/delivery-contract.md#optional-opening).

## Optional viewer capabilities

`meta.animation: "trace"` is opt-in.

Read `references/viewer-runtime.md` only when the user explicitly asks for Share Cards, Route/Reach cards, motion, deep links, presentation, search/focus, or another Viewer Runtime feature.

## Setup and fallback

No install is required inside the skill package. For setup diagnosis, verify with:

```bash
node bin/archify.mjs doctor
node bin/archify.mjs demo <output-directory>
```

`doctor` checks package files and starts a headless browser through the same DevTools pipe used by `browser-check`. Browser discovery or startup failure means full `finalize` is not ready; set `ARCHIFY_CHROME` to a compatible executable and rerun. A passing doctor confirms this prerequisite, not the quality of a particular diagram.

When shell access is unavailable but file editing is possible, copy `assets/template.html` to a separate draft file and hand-place Architecture SVG there using CSS semantic classes rather than inline colors. This is an **unvalidated draft**: no `finalize` gates or artifact-bound receipts have run, so do not present it as completed Archify output. For Workflow, Sequence, Dataflow, or Lifecycle, provide a typed JSON or Mermaid draft instead of hand-editing the Architecture template. When commands become available, run `finalize` before claiming completion.

## Output

For completed output, confirm the user can actually obtain the final HTML through the shared workspace, an attachment, or a download location. Compare that accessible copy's byte count and SHA-256 with the artifact verified by `finalize` and the final interaction smoke; confirm the checked artifact has not changed after those gates. A browser opened through an agent in a remote or temporary environment does not prove that the user can obtain its local path. If access or byte identity cannot be confirmed, label the file a draft and state the missing check. Return the accessible HTML location, diagram type, validation summary, specification/artifact receipt, browser-evidence status, and truthful visual-review status. Report mobile checks separately; touch support requires a touch-emulated browser or device interaction. If the user requests a network target, finish the Archify gates locally first; handle the network copy or publication separately, verify the destination bytes independently, and report its path and evidence separately. Archify receipts do not prove network publication. A manual fallback has no validated receipt; identify its draft location or content and the checks still needed. Do not claim success for a non-zero command or claim visual inspection you did not perform.
