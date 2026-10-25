# Repository-backed architecture authoring

Use this reference when a diagram must explain a real repository. The source is
the authority for responsibilities, calls, boundaries, and persistence. The
diagram is complete when the requested meaning is covered and every asserted
fact has supporting source evidence.

First decide which bytes the user wants explained. Archify's repository
evidence requires a real Git top-level directory with an `origin` remote and
an available 40-character commit. Its source checks read blobs at that commit,
not the working tree. If the requested source is a non-Git project, a Git
project without a usable `origin` or commit, or uncommitted working-tree bytes,
do not author those bytes as Archify repository evidence. In this Show Me
bundle, use [the independent interactive alternative](../../references/interactive-alternative.md)
with records of the bytes or excerpts actually read, source snapshots only when needed,
and real-browser checks. Do not invent a remote
URL or commit, omit `meta.repository`/`sources` to imply source verification,
or treat `link_mode: "local-only"` as a working-tree snapshot. If the user asks
for the committed revision despite working-tree edits, continue
with Archify and inspect the pinned commit's bytes for every claim.

Inside Show Me's independent interactive alternative, Archify may still be used solely as a renderer for its supported diagram types when its commands are available. That path must omit both `meta.repository` and node `sources`, explicitly state that Archify did not check the non-Git or working-tree source, and verify the actual source bytes, semantic claims, artifact, and handoff separately under the alternative contract. An Archify `finalize` or `--require-provenance` receipt in this mode proves only the candidate/HTML delivery and the browser checks it actually ran; it is not repository-source evidence. Never omit repository fields while implying the source was verified by Archify.

## Explore on demand

1. **Freeze committed identity.** For the committed-revision case above, record
   `git rev-parse HEAD`, `git remote get-url origin`, and
   `git status --short --untracked-files=all`. Remove HTTP(S) userinfo
   (including usernames, passwords, and tokens) before recording the
   origin or placing it in the candidate. Preserve its transport, port, path and
   `.git` suffix; do not rewrite an internal SSH origin as HTTPS. Pin the credential-free URL and
   forty-character revision in `meta.repository`. Use `link_mode: "local-only"`
   for an SSH origin, unsupported forge, or intentionally local-only source
   links; retain the actual URL and revision. Web links require a supported
   GitHub or Gitee HTTPS origin. Before presenting a web source link as
   reachable, check that its pinned commit and cited path actually resolve at
   the remote; a local commit can pass blob checks before it is pushed. If the
   link is not reachable, use `local-only` and report the local commit and path
   without implying online access.
   If the worktree is dirty, record the changed paths and read cited files from
   the pinned commit (for example, from a separate clean checkout or `git show
   <revision>:<path>`). The Archify receipt verifies path and line existence in
   committed blobs; it does not establish the meaning of a claim or verify
   working-tree edits.

2. **Map the slice.** Use project instructions, manifests, entry points,
   registrations, and deployment configuration to locate candidate runtime
   units. Read the entry, configuration, and modules relevant to the request.
   Follow imports and call sites
   until the requested responsibility reaches its actual input, output, or
   side effect. Read a small connected slice instead of scanning the repository
   for a convenient label.

3. **Trace ownership.** Derive runtime and I/O relationships from the observed
   actor, operation, and target at their call sites; deployment and trust
   relationships use the corresponding configuration or enforcement evidence. Distinguish the controller requesting
   an operation from the runtime that executes it and the store receiving bytes.
   For a file or database edge, the source must identify its actual reader or
   writer; a responsibility statement such as “maintains tasks” does not prove
   direct I/O. Keep these facts with the source locations while reading, without
   a separate planning artifact. Choose which distinctions need separate
   nodes using [Composition and meaning](authoring-defaults.md#composition-and-meaning);
   discovering an implementation role does not automatically add it to the overview.
   A configured provider, an injected adapter, a local stub, and a durable
   service are different claims; label the one the source supports.

4. **Record evidence while reading.** Keep exact repository-relative paths and
   inclusive line ranges for each component and meaningful relationship. Follow
   actual branches, retries, fallbacks, and error handling. A function that is
   exported or configured but never called by the normal path is an optional
   capability, not a required runtime edge. For a claim about authoritative
   state change or control ownership, trace to the actual write or execution
   site and the conditions that permit it; an upstream caller alone does not
   establish those conditions.

5. **Name uncertainty.** Write unresolved questions beside the claim they
   affect: for example, “`writeFile` is called here; durability is unknown.”
   Resolve a question by reading the next relevant source range or preserve it
   as an explicit unknown. Never turn a label, package description, or config
   value into an unobserved service or behavior.

Stop exploring when every requested responsibility, relationship, and boundary
has supporting source entailment and the remaining unknowns cannot change that
coverage. There is no node, edge, citation, view, card, or boundary count to
hit. Do not add a summary step merely to signal completion.

Batch independent relevant files when known. Each additional read should answer
an unresolved question that can change the diagram. Reuse concise facts and
their source ranges already verified in this task; across revisions, recheck
the affected entry points, configuration, dependencies, and evidence.

## Choose an example by structure

Select the main example in the [Type router](../SKILL.md#type-router) before
loading its content, using the request and repository metadata already needed
for source inspection. Selection fits the existing read batch and needs no extra
message, command, or repository-wide scan. For mixed or unclear tasks, use the
requested responsibilities and entry points as they become known in normal
inspection; keep their actual roles. Read another example when a necessary
capability remains unexplained. Examples teach shape, not facts: a library need
not acquire filesystem nodes, and finished showcases still follow the
first-draft automatic-routing rule.

## Author from evidence

Use the mode's complete JSON shape, including repository identity,
components, and connections; every repository-backed component needs supporting source
references, while boundaries or cards are added only when they
answer a real reader question. Let automatic routes and automatic
viewBox sizing work first. Keep the primary path readable, put exception paths
beside their owner, and leave filesystem stores outside a control boundary when
the source shows a separate responsibility.

An existing example teaches field shape, not facts or arbitrary values. It does
not authorize a new boundary kind, a long note, a viewBox size, or a route
control. Consult the specific mode schema and `schemas/common.schema.json`
whether or not the selected example already contains the field; use the
schema's enum, length, identifier, and repository rules. Architecture
boundaries currently use `kind: "region"` or `kind: "security-group"`; source
references use `path`, `line`, and optional `end_line`.

Repository-backed components need concise, truthful `sources` references. Preserve
control ownership when summarizing filesystem I/O: the code that reads or writes
a file owns that action, while a pure in-memory transform receives and returns
values. This fact-check does not require a separate overview node for every helper. Use the existing examples for valid field shape, then replace all
identifiers, wording, source paths, and claims with inspected repository facts.
