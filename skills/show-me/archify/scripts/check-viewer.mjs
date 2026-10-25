#!/usr/bin/env node

// The packaged skill contains the assembled viewer, not its repository-only
// source fragments. Check the delivered template and bundled render examples.
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import vm from 'node:vm';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const examples = [
  'workflow-agent-tool-call-rendered.html',
  'sequence-cache-miss-request.html',
  'dataflow-product-analytics.html',
  'lifecycle-agent-run.html',
  'web-app-rendered.html',
];

function read(relative) {
  return fs.readFileSync(path.join(root, relative), 'utf8').replace(/\r\n?/g, '\n');
}

function blocks(html, tag) {
  const expression = new RegExp(`<${tag}(?:\\s[^>]*)?>[\\s\\S]*?<\\/${tag}>`, 'gi');
  return [...html.matchAll(expression)]
    .map(([block]) => block)
    .filter((block) => tag !== 'script' || !/^<script\b[^>]*\btype=["']application\/json["']/i.test(block));
}

function requireCondition(condition, message) {
  if (!condition) throw new Error(message);
}

try {
  const template = read('assets/template.html');
  const { version } = JSON.parse(read('package.json'));
  requireCondition(
    template.includes(`<meta name="generator" content="archify ${version}">`),
    `template generator metadata must match package version ${version}`,
  );

  const markers = [
    'SOURCE_EVIDENCE_DATA', 'I18N_DATA',
    'SVG_SLOT_START', 'SVG_SLOT_END',
    'CARDS_SLOT_START', 'CARDS_SLOT_END',
  ];
  for (const marker of markers) {
    const token = `ARCHIFY:${marker}`;
    requireCondition(template.split(token).length === 2, `expected one ${token} marker`);
  }
  for (const slot of ['SVG', 'CARDS']) {
    requireCondition(
      template.indexOf(`ARCHIFY:${slot}_SLOT_START`) < template.indexOf(`ARCHIFY:${slot}_SLOT_END`),
      `${slot} slot markers are out of order`,
    );
  }

  const scripts = blocks(template, 'script');
  const styles = blocks(template, 'style');
  requireCondition(scripts.length > 0 && styles.length > 0, 'template must include viewer scripts and styles');
  for (const [index, script] of scripts.entries()) {
    const source = script.replace(/^<script(?:\s[^>]*)?>/i, '').replace(/<\/script>$/i, '');
    new vm.Script(source, { filename: `assets/template.html script ${index + 1}` });
  }

  for (const example of examples) {
    const html = read(`examples/${example}`);
    requireCondition(
      JSON.stringify(blocks(html, 'script')) === JSON.stringify(scripts),
      `${example} contains stale viewer JavaScript; run npm run render:examples`,
    );
    requireCondition(
      JSON.stringify(blocks(html, 'style')) === JSON.stringify(styles),
      `${example} contains stale viewer CSS; run npm run render:examples`,
    );
  }
  console.log(`viewer template and ${examples.length} bundled examples are consistent`);
} catch (error) {
  console.error(`viewer check failed: ${error.message}`);
  process.exitCode = 1;
}
