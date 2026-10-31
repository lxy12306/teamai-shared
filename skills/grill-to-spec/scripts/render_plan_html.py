#!/usr/bin/env python3
"""Render a grill-to-spec implementation plan as a single offline HTML file.

This is a renderer for the plan format used by this skill, not a general
CommonMark implementation. It uses only the Python standard library.
"""

from __future__ import annotations

import argparse
from datetime import date
import html
import os
from pathlib import Path
import re
import sys
import tempfile
from typing import Dict, List, Optional, Tuple
from urllib.parse import urlsplit


TASK_RE = re.compile(r"^###\s+(Task\s+\d+\s*:?.*)$")
STEP_RE = re.compile(r"^\s*-\s*\[[ xX]\]\s*\*\*Step\b")
EXPECTED_RE = re.compile(r"^\s*Expected\s*:")
FENCE_RE = re.compile(r"^\s*((?:`{3,})|(?:~{3,}))([^\r\n]*)$")
HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
LIST_RE = re.compile(r"^\s*([-*+]|\d+\.)\s+(.+?)\s*$")
TABLE_SEP_RE = re.compile(r"^\s*\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)+\|?\s*$")
INLINE_RE = re.compile(r"(`[^`\n]+`|\*\*[^*]+\*\*|\[[^\]]+\]\([^)]+\))")


STYLE = """
:root { color-scheme: light; font-family: system-ui, -apple-system, "Segoe UI", sans-serif; color: #1e293b; }
* { box-sizing: border-box; }
body { margin: 0; background: #f8fafc; line-height: 1.65; }
main { max-width: 56rem; margin: auto; padding: 1.4rem 1rem 3rem; }
header, nav, article, footer { background: #fff; border: 1px solid #cbd5e1; border-radius: .75rem; padding: 1.25rem; margin-block: 1rem; }
h1 { margin-top: 0; line-height: 1.25; }
h2 { margin-top: 1.8rem; border-bottom: 1px solid #e2e8f0; padding-bottom: .3rem; }
h3[data-task] { margin-top: 2rem; padding: .65rem .8rem; border-left: 4px solid #2563eb; background: #eff6ff; border-radius: .25rem; }
p { margin: .55rem 0; overflow-wrap: anywhere; }
ul, ol { padding-left: 1.5rem; }
li { margin: .35rem 0; overflow-wrap: anywhere; }
li.step { padding: .35rem .55rem; border-left: 3px solid #94a3b8; background: #f8fafc; }
.check { font-weight: 700; color: #475569; margin-right: .4rem; }
.run { border-left: 3px solid #0f766e; padding-left: .7rem; }
.expected { border-left: 3px solid #7c3aed; padding-left: .7rem; }
pre { overflow-x: auto; padding: 1rem; background: #f1f5f9; border-radius: .5rem; line-height: 1.45; }
code, pre { font-family: ui-monospace, SFMono-Regular, Consolas, "Liberation Mono", monospace; }
p code, li code, td code { background: #f1f5f9; border-radius: .2rem; padding: .1rem .2rem; overflow-wrap: anywhere; }
table { display: block; overflow-x: auto; border-collapse: collapse; width: 100%; }
th, td { border: 1px solid #cbd5e1; padding: .4rem .65rem; text-align: left; vertical-align: top; }
th { background: #f1f5f9; }
nav ul { columns: 2; }
nav a { color: #1d4ed8; }
:focus-visible { outline: 3px solid #2563eb; outline-offset: 2px; }
@media (max-width: 600px) { main { padding: .6rem .5rem 2rem; } header, nav, article, footer { padding: .85rem; } nav ul { columns: 1; } }
""".strip()


def safe_href(value: str) -> Optional[str]:
    value = value.strip()
    if not value or value.startswith("//") or "\\" in value or any(ord(char) < 32 for char in value):
        return None
    parsed = urlsplit(value)
    if parsed.scheme.lower() not in ("", "http", "https", "mailto"):
        return None
    return html.escape(value, quote=True)


def inline(value: str) -> str:
    """Escape text first, then render a small safe subset of inline Markdown."""
    chunks: List[str] = []
    previous = 0
    for match in INLINE_RE.finditer(value):
        chunks.append(html.escape(value[previous : match.start()], quote=True))
        token = match.group()
        if token.startswith("`"):
            chunks.append(f"<code>{html.escape(token[1:-1], quote=True)}</code>")
        elif token.startswith("**"):
            chunks.append(f"<strong>{inline(token[2:-2])}</strong>")
        else:
            label, target = token[1:-1].split("](", 1)
            href = safe_href(target)
            if href is None:
                chunks.append(inline(label))
            else:
                chunks.append(f'<a href="{href}">{inline(label)}</a>')
        previous = match.end()
    chunks.append(html.escape(value[previous:], quote=True))
    return "".join(chunks)


def table_cells(line: str) -> List[str]:
    value = line.strip()
    if value.startswith("|"):
        value = value[1:]
    if value.endswith("|") and not value.endswith("\\|"):
        value = value[:-1]
    cells: List[str] = []
    current: List[str] = []
    escaped = False
    code_ticks = 0
    index = 0
    while index < len(value):
        char = value[index]
        if escaped:
            if char == "|" and code_ticks == 0 and current and current[-1] == "\\":
                current.pop()
            current.append(char)
            escaped = False
            index += 1
            continue
        if char == "\\":
            current.append(char)
            escaped = True
            index += 1
            continue
        if char == "`":
            end = index
            while end < len(value) and value[end] == "`":
                end += 1
            run = end - index
            if code_ticks == 0:
                code_ticks = run
            elif code_ticks == run:
                code_ticks = 0
            current.append(value[index:end])
            index = end
            continue
        if char == "|" and code_ticks == 0:
            cells.append("".join(current).strip())
            current = []
        else:
            current.append(char)
        index += 1
    if code_ticks:
        raise ValueError("unclosed inline code in table row")
    cells.append("".join(current).strip())
    return cells


def render_table(lines: List[str]) -> str:
    columns = table_cells(lines[0])
    output = ['<table><thead><tr>']
    output.extend(f"<th>{inline(cell)}</th>" for cell in columns)
    output.append("</tr></thead><tbody>")
    for row in lines[2:]:
        cells = table_cells(row)
        if len(cells) != len(columns):
            raise ValueError("table row column count differs from header")
        output.append("<tr>")
        output.extend(
            f"<td>{inline(cells[index])}</td>"
            for index in range(len(columns))
        )
        output.append("</tr>")
    output.append("</tbody></table>")
    return "".join(output)


def render_blocks(lines: List[str], counters: Dict[str, int]) -> str:
    output: List[str] = []
    list_kind: Optional[str] = None

    def close_list() -> None:
        nonlocal list_kind
        if list_kind is not None:
            output.append(f"</{list_kind}>")
            list_kind = None

    index = 0
    while index < len(lines):
        line = lines[index].rstrip("\n")
        stripped = line.strip()
        if not stripped:
            close_list()
            index += 1
            continue

        fence = FENCE_RE.match(line)
        if fence:
            close_list()
            marker = fence.group(1)
            language = re.sub(r"[^A-Za-z0-9_.+-]", "", fence.group(2).strip().split(" ", 1)[0])
            code: List[str] = []
            index += 1
            while index < len(lines):
                if is_closing_fence(lines[index], marker):
                    break
                code.append(lines[index])
                index += 1
            if index == len(lines):
                raise ValueError("unclosed code fence in plan")
            counters["code"] += 1
            attribute = f' class="language-{html.escape(language, quote=True)}"' if language else ""
            output.append(
                f'<pre data-codeblock="{counters["code"]}"><code{attribute}>'
                + html.escape("\n".join(code), quote=True)
                + "</code></pre>"
            )
            index += 1
            continue

        heading = HEADING_RE.match(line)
        if heading:
            close_list()
            level = len(heading.group(1))
            content = heading.group(2)
            if TASK_RE.match(line):
                counters["task"] += 1
                number = counters["task"]
                output.append(
                    f'<h{level} id="task-{number}" data-task="{number}">'
                    + inline(content)
                    + f"</h{level}>"
                )
            else:
                output.append(f"<h{level}>{inline(content)}</h{level}>")
            index += 1
            continue

        if stripped in ("---", "***", "___"):
            close_list()
            output.append("<hr>")
            index += 1
            continue

        if line.lstrip().startswith("|") and index + 1 < len(lines) and TABLE_SEP_RE.match(lines[index + 1]):
            close_list()
            end = index + 2
            while end < len(lines) and lines[end].lstrip().startswith("|"):
                end += 1
            output.append(render_table(lines[index:end]))
            index = end
            continue

        item = LIST_RE.match(line)
        if item:
            kind = "ol" if item.group(1).endswith(".") else "ul"
            if list_kind != kind:
                close_list()
                output.append(f"<{kind}>")
                list_kind = kind
            content = item.group(2)
            checked = re.match(r"^\[([ xX])\]\s*(.*)$", content)
            tick = ""
            if checked:
                tick = '<span class="check" aria-hidden="true">☑</span>' if checked.group(1).lower() == "x" else '<span class="check" aria-hidden="true">☐</span>'
                content = checked.group(2)
            if STEP_RE.match(line):
                counters["step"] += 1
                output.append(f'<li class="step" data-step="{counters["step"]}">{tick}{inline(content)}</li>')
            else:
                output.append(f"<li>{tick}{inline(content)}</li>")
            index += 1
            continue

        close_list()
        if EXPECTED_RE.match(line):
            counters["expected"] += 1
            output.append(f'<p class="expected" data-expected="{counters["expected"]}">{inline(stripped)}</p>')
        elif stripped.startswith("Run:"):
            output.append(f'<p class="run">{inline(stripped)}</p>')
        elif stripped.startswith("> "):
            output.append(f"<blockquote>{inline(stripped[2:])}</blockquote>")
        else:
            output.append(f"<p>{inline(stripped)}</p>")
        index += 1

    close_list()
    return "\n".join(output)


def is_closing_fence(line: str, opening: str) -> bool:
    return re.match(
        r"^\s*" + re.escape(opening[0]) + "{" + str(len(opening)) + r",}\s*$",
        line,
    ) is not None


def source_counts(lines: List[str]) -> Dict[str, int]:
    counts = {"task": 0, "step": 0, "expected": 0, "code": 0}
    inside_code = False
    opening = ""
    for line in lines:
        if inside_code:
            if is_closing_fence(line, opening):
                inside_code = False
            continue
        fence = FENCE_RE.match(line)
        if fence:
            counts["code"] += 1
            inside_code = True
            opening = fence.group(1)
            continue
        counts["task"] += bool(TASK_RE.match(line))
        counts["step"] += bool(STEP_RE.match(line))
        counts["expected"] += bool(EXPECTED_RE.match(line))
    if inside_code:
        raise ValueError("unclosed code fence in plan")
    return counts


def task_entries(lines: List[str]) -> List[Tuple[int, str]]:
    entries: List[Tuple[int, str]] = []
    inside_code = False
    opening = ""
    for index, line in enumerate(lines):
        if inside_code:
            if is_closing_fence(line, opening):
                inside_code = False
            continue
        fence = FENCE_RE.match(line)
        if fence:
            inside_code = True
            opening = fence.group(1)
            continue
        task = TASK_RE.match(line)
        if task:
            entries.append((index, task.group(1)))
    return entries


def render_plan(source: Path, repo: Path) -> Tuple[str, Dict[str, int]]:
    try:
        relative_source = source.relative_to(repo).as_posix()
    except ValueError as exc:
        raise ValueError("plan must be inside --repo") from exc
    text = source.read_text(encoding="utf-8-sig")
    lines = text.splitlines()
    if not lines or not re.match(r"^#\s+\S", lines[0]):
        raise ValueError("first line must be a Markdown H1 title")
    title = lines[0][2:].strip()
    tasks = task_entries(lines)
    first_task = tasks[0][0] if tasks else len(lines)
    counters = {"task": 0, "step": 0, "expected": 0, "code": 0}
    intro = render_blocks(lines[:first_task], counters)
    detail = render_blocks(lines[first_task:], counters)
    expected = source_counts(lines)
    if counters != expected:
        raise ValueError(f"rendered counts {counters} do not match source counts {expected}")
    contents = "\n".join(
        f'<li><a href="#task-{number}">{inline(label)}</a></li>'
        for number, (_, label) in enumerate(tasks, start=1)
    )
    toc = f'<nav aria-label="任务目录"><h2>任务目录</h2><ul>{contents}</ul></nav>' if tasks else ""
    output = "\n".join(
        [
            "<!DOCTYPE html>",
            '<html lang="zh-CN">',
            "<head>",
            '<meta charset="utf-8">',
            '<meta name="viewport" content="width=device-width, initial-scale=1">',
            f"<title>{html.escape(title, quote=True)}</title>",
            f"<style>{STYLE}</style>",
            "</head>",
            "<body><main>",
            f"<header>{intro}</header>",
            toc,
            f"<article>{detail}</article>",
            "<footer>",
            f"<p>源 Markdown：<code>{html.escape(relative_source, quote=True)}</code></p>",
            f"<p>生成日期：{date.today().isoformat()}</p>",
            "</footer>",
            "</main></body>",
            "</html>",
            "",
        ]
    )
    if "<script" in output.lower():
        raise ValueError("unexpected script tag in rendered HTML")
    return output, counters


def write_html(target: Path, content: str) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=".plan-html-", suffix=".tmp", dir=target.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        # A hard link creates the final name atomically and refuses overwrite.
        # Keeping the temporary file in the same directory avoids cross-volume links.
        os.link(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True, type=Path, help="project root containing the plan")
    parser.add_argument("plan", type=Path, help="source Markdown plan")
    parser.add_argument("html", type=Path, help="output HTML within the project")
    args = parser.parse_args(argv)
    repo = args.repo.resolve()
    plan = args.plan.resolve()
    target = args.html.resolve()
    if not repo.is_dir():
        parser.error("--repo must be an existing directory")
    if not plan.is_file():
        parser.error("plan must be an existing file")
    if plan == target:
        parser.error("output cannot overwrite the Markdown plan")
    try:
        target.relative_to(repo)
    except ValueError:
        parser.error("HTML output must be inside --repo")
    try:
        output, counts = render_plan(plan, repo)
        write_html(target, output)
    except (ValueError, OSError) as exc:
        parser.exit(1, f"render failed: {exc}\n")
    print(
        f"rendered {target.name}: "
        f"Task={counts['task']} Step={counts['step']} "
        f"Expected={counts['expected']} Code={counts['code']}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
