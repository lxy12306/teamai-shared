#!/usr/bin/env node

// Exercise the delivered Viewer's simplest semantic path in a real browser.
// This is a separate smoke check; it does not change browser-check's receipt.
import { createHash } from 'node:crypto';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

import { ChromeVisualBrowser, findChrome } from '../bin/visual-check.mjs';

const VIEWPORT = { width: 1440, height: 900 };
const ALL_VIEWPORTS = [
  VIEWPORT,
  { width: 1600, height: 1000 },
  { width: 1920, height: 1080 },
  { width: 2048, height: 1320 },
];

function sha256(bytes) {
  return createHash('sha256').update(bytes).digest('hex');
}

function requireCheck(condition, message) {
  if (!condition) throw new Error(message);
}

async function evaluate(browser, session, expression, awaitPromise = false) {
  const response = await browser.cdp.send('Runtime.evaluate', {
    expression, awaitPromise, returnByValue: true,
  }, session);
  if (response.exceptionDetails) {
    throw new Error(response.exceptionDetails.exception?.description
      || response.exceptionDetails.text || 'Browser evaluation failed.');
  }
  return response.result?.value;
}

async function settle(browser, session) {
  await evaluate(browser, session, `(async function () {
    if (document.fonts && document.fonts.ready) await document.fonts.ready;
    if (window.Archify && Archify.viewerChromeLayout && Archify.viewerChromeLayout.whenStable) {
      await Archify.viewerChromeLayout.whenStable();
    }
    await new Promise(function (resolve, reject) {
      var started = performance.now();
      function sample() {
        if (!document.querySelector('.diagram-container[data-camera-transaction]')) {
          requestAnimationFrame(function () { requestAnimationFrame(resolve); });
        } else if (performance.now() - started > 10000) {
          reject(new Error('Viewer camera did not settle.'));
        } else requestAnimationFrame(sample);
      }
      sample();
    });
  })()`, true);
}

async function nativeClick(browser, session, selector) {
  const point = await evaluate(browser, session, `(function () {
    var target = document.querySelector(${JSON.stringify(selector)});
    if (!target) return null;
    var rect = target.getBoundingClientRect();
    var x = rect.left + rect.width / 2;
    var y = rect.top + rect.height / 2;
    var hit = document.elementFromPoint(x, y);
    return { x: x, y: y, hittable: !!hit && (hit === target || target.contains(hit)),
      visible: rect.width > 0 && rect.height > 0 && x >= 0 && x < innerWidth && y >= 0 && y < innerHeight };
  })()`);
  requireCheck(point?.visible && point.hittable, `Viewer control ${selector} is not clickable.`);
  await browser.cdp.send('Input.dispatchMouseEvent', {
    type: 'mousePressed', x: point.x, y: point.y, button: 'left', clickCount: 1,
  }, session);
  await browser.cdp.send('Input.dispatchMouseEvent', {
    type: 'mouseReleased', x: point.x, y: point.y, button: 'left', clickCount: 1,
  }, session);
}

async function runtimeErrors(browser, session) {
  return await evaluate(browser, session, 'window.__archifySmokeErrors.slice()');
}

async function runViewportSmoke(artifactPath, viewport, expectedNoRelationships) {
  const artifact = path.resolve(artifactPath);
  const receipt = {
    schemaVersion: 2,
    command: 'basic-interaction-smoke',
    status: 'fail',
    ok: false,
    artifact: { path: artifact },
    relationshipExpectation: expectedNoRelationships ? 'none' : 'present',
    viewport,
    checks: {
      nodeActivation: 'not-run', detail: 'not-run', feedback: 'not-run',
      emptyRelationships: 'not-run', returnToGraph: 'not-run',
    },
    runtimeErrors: [],
  };
  let browser;
  let snapshotDirectory;
  try {
    const stat = fs.statSync(artifact);
    requireCheck(stat.isFile(), 'The artifact is not a regular file.');
    requireCheck(/\.html?$/i.test(artifact), 'The artifact path must end in .html or .htm.');
    const bytes = fs.readFileSync(artifact);
    receipt.artifact.sha256 = sha256(bytes);
    receipt.artifact.bytes = bytes.byteLength;
    const chrome = findChrome();
    requireCheck(Boolean(chrome), 'Chrome, Chromium, or Edge is unavailable; set ARCHIFY_CHROME.');
    receipt.browser = { executable: chrome };

    // Chrome's file loader can reject long Windows paths. Inspect a private
    // byte-identical copy, then verify that the delivered file stayed current.
    snapshotDirectory = fs.mkdtempSync(path.join(os.tmpdir(), 'archify-interaction-'));
    const snapshot = path.join(snapshotDirectory, 'artifact.html');
    fs.writeFileSync(snapshot, bytes);
    requireCheck(sha256(fs.readFileSync(snapshot)) === receipt.artifact.sha256,
      'The private artifact snapshot differs from the delivered HTML.');

    browser = new ChromeVisualBrowser(chrome);
    const session = await browser.sessionPromise;
    await browser.cdp.send('Emulation.setDeviceMetricsOverride', {
      ...viewport, deviceScaleFactor: 1, mobile: false,
    }, session);
    await browser.cdp.send('Emulation.setEmulatedMedia', {
      features: [{ name: 'prefers-reduced-motion', value: 'reduce' }],
    }, session);
    await browser.cdp.send('Page.addScriptToEvaluateOnNewDocument', { source: `
      window.__archifySmokeEvents = [];
      window.__archifySmokeErrors = [];
      window.addEventListener('error', function (event) {
        if (event instanceof ErrorEvent) __archifySmokeErrors.push({
          type: 'error', message: event.message, source: event.filename,
          line: event.lineno, column: event.colno
        });
      }, true);
      window.addEventListener('unhandledrejection', function (event) {
        __archifySmokeErrors.push({
          type: 'unhandledrejection',
          message: event.reason && event.reason.message || String(event.reason)
        });
      });
      document.addEventListener('keydown', function (event) {
        var node = event.target.closest && event.target.closest('[data-node-id]');
        if (node && event.key === 'Enter') __archifySmokeEvents.push({
          action: 'node-enter', trusted: event.isTrusted, nodeId: node.getAttribute('data-node-id')
        });
      }, true);
      document.addEventListener('click', function (event) {
        if (event.target.closest && event.target.closest('#btn-focus-clear')) {
          __archifySmokeEvents.push({ action: 'clear-click', trusted: event.isTrusted });
        }
      }, true);
    ` }, session);
    const loaded = browser.cdp.waitFor('Page.loadEventFired', session);
    loaded.catch(() => {});
    const url = pathToFileURL(snapshot);
    url.searchParams.set('theme', 'light');
    const navigation = await browser.cdp.send('Page.navigate', { url: url.href }, session);
    requireCheck(!navigation.errorText, `Browser navigation failed: ${navigation.errorText}`);
    await loaded;
    await settle(browser, session);
    receipt.runtimeErrors = await runtimeErrors(browser, session);
    requireCheck(receipt.runtimeErrors.length === 0,
      `Viewer runtime error: ${receipt.runtimeErrors[0]?.message}`);

    const initial = await evaluate(browser, session, `(function () {
      var svg = document.querySelector('.diagram-container > svg');
      var chip = document.getElementById('focus-chip');
      if (!svg || !chip || !window.Archify || !Archify.focus) return null;
      var edges = Array.from(svg.querySelectorAll('[data-edge-from][data-edge-to]'));
      var renderedEdges = svg.querySelectorAll('[data-edge-from], [data-edge-to]').length;
      var nodes = Array.from(svg.querySelectorAll('[data-node-id]')).map(function (node, index) {
        var id = node.getAttribute('data-node-id');
        return { index: index, id: id, label: node.getAttribute('data-node-label') || id,
          connectedEdges: new Set(edges.filter(function (edge) {
            return edge.getAttribute('data-edge-from') === id || edge.getAttribute('data-edge-to') === id;
          }).map(function (edge) {
            return edge.getAttribute('data-edge-key') || edge.getAttribute('data-edge-id') ||
              [edge.getAttribute('data-edge-from'), edge.getAttribute('data-edge-to'), edge.getAttribute('data-edge-label')].join(':');
          })).size };
      });
      nodes.sort(function (a, b) { return b.connectedEdges - a.connectedEdges || a.index - b.index; });
      return { active: Archify.focus.active(), chipHidden: chip.hidden,
        focused: svg.hasAttribute('data-focus-active'), renderedEdges: renderedEdges,
        node: nodes[0] || null };
    })()`);
    requireCheck(initial?.node?.id, 'The artifact has no interactive diagram node.');
    requireCheck(initial.active == null && initial.chipHidden && !initial.focused,
      'The artifact did not open at the full graph.');
    receipt.node = { id: initial.node.id, label: initial.node.label,
      connectedEdges: initial.node.connectedEdges };
    receipt.renderedEdges = initial.renderedEdges;
    if (expectedNoRelationships) {
      requireCheck(initial.renderedEdges === 0,
        'The artifact has diagram relationships but --expected-no-relationships was requested.');
    } else {
      requireCheck(initial.node.connectedEdges > 0,
        'The artifact has no diagram relationships; relationship feedback cannot be verified.');
      receipt.checks.emptyRelationships = 'not-applicable';
    }

    // Focusing the SVG node is setup; the activation itself is a trusted
    // browser key event, not a call to Archify.focus.set().
    const focused = await evaluate(browser, session, `(function () {
      var node = document.querySelectorAll('.diagram-container > svg [data-node-id]')[${initial.node.index}];
      if (!node || !node.hasAttribute('tabindex')) return { focused: false, visible: false };
      var rect = node.getBoundingClientRect();
      var visible = node.checkVisibility({ checkOpacity: true, checkVisibilityCSS: true })
        && rect.width > 0 && rect.height > 0 && rect.right > 0 && rect.bottom > 0
        && rect.left < innerWidth && rect.top < innerHeight;
      node.focus({ preventScroll: true });
      return { focused: document.activeElement === node, visible: visible };
    })()`);
    receipt.node.visible = focused.visible;
    requireCheck(focused.visible && focused.focused,
      `Node ${initial.node.id} is not visible and keyboard focusable.`);
    await browser.cdp.send('Input.dispatchKeyEvent', {
      type: 'keyDown', key: 'Enter', code: 'Enter', windowsVirtualKeyCode: 13, text: '\r',
    }, session);
    await browser.cdp.send('Input.dispatchKeyEvent', {
      type: 'keyUp', key: 'Enter', code: 'Enter', windowsVirtualKeyCode: 13,
    }, session);
    await settle(browser, session);

    const selected = await evaluate(browser, session, `(function () {
      function exposed(selector) {
        var target = document.querySelector(selector);
        if (!target || !target.checkVisibility({ checkOpacity: true, checkVisibilityCSS: true })) return false;
        var rect = target.getBoundingClientRect();
        var left = Math.max(0, rect.left), top = Math.max(0, rect.top);
        var right = Math.min(innerWidth, rect.right), bottom = Math.min(innerHeight, rect.bottom);
        if (right <= left || bottom <= top) return false;
        var hit = document.elementFromPoint((left + right) / 2, (top + bottom) / 2);
        return !!hit && (hit === target || target.contains(hit));
      }
      var svg = document.querySelector('.diagram-container > svg');
      var chip = document.getElementById('focus-chip');
      var node = Array.from(svg.querySelectorAll('[data-node-id]')).find(function (item) {
        return item.getAttribute('data-node-id') === ${JSON.stringify(initial.node.id)};
      });
      var rows = Array.from(document.querySelectorAll('#relationship-lens-list [data-relationship-target]'));
      return { active: Archify.focus.active(), focused: svg.getAttribute('data-focus-active'),
        selected: node?.hasAttribute('data-focus-selected'), pressed: node?.getAttribute('aria-pressed'),
        chipHidden: chip.hidden, chipVisible: chip.checkVisibility({ checkOpacity: true, checkVisibilityCSS: true }),
        detailExposed: exposed('#focus-label') && exposed('#focus-id'),
        label: document.getElementById('focus-label')?.textContent?.trim(),
        passportId: document.getElementById('focus-id')?.textContent?.trim(),
        summary: document.getElementById('focus-summary')?.textContent?.trim(),
        rows: rows.length, firstRelation: rows[0] ? {
          from: rows[0].getAttribute('data-relationship-from'),
          to: rows[0].getAttribute('data-relationship-to'),
          text: rows[0].textContent.trim(),
          visible: rows[0].checkVisibility({ checkOpacity: true, checkVisibilityCSS: true }),
          exposed: exposed('#relationship-lens-list [data-relationship-target]')
        } : null, events: __archifySmokeEvents.slice() };
    })()`);
    receipt.selection = selected;
    requireCheck(selected.events.some(event => event.action === 'node-enter' && event.trusted
      && event.nodeId === initial.node.id), 'The node activation was not a trusted browser key event.');
    requireCheck(selected.active === initial.node.id && selected.focused === initial.node.id
      && selected.selected && selected.pressed === 'true',
    `Node ${initial.node.id} did not become the selected graph node.`);
    receipt.checks.nodeActivation = 'pass';
    requireCheck(!selected.chipHidden && selected.chipVisible && selected.detailExposed
      && selected.label === initial.node.label
      && selected.passportId === initial.node.id,
    `Node ${initial.node.id} did not show its matching detail panel.`);
    receipt.checks.detail = 'pass';
    if (expectedNoRelationships) {
      requireCheck(selected.rows === 0,
        `Node ${initial.node.id} showed relationship rows despite the expected empty graph.`);
      const empty = await evaluate(browser, session, `(function () {
        var list = document.getElementById('relationship-lens-list');
        var placeholder = list?.querySelector('.relationship-lens-empty');
        var button = document.getElementById('btn-focus-relations');
        return { rows: list?.querySelectorAll('[data-relationship-target]').length,
          text: placeholder?.textContent?.trim(),
          visible: placeholder?.checkVisibility({ checkOpacity: true, checkVisibilityCSS: true }),
          exposed: (function () {
            if (!placeholder) return false;
            var rect = placeholder.getBoundingClientRect();
            var x = (Math.max(0, rect.left) + Math.min(innerWidth, rect.right)) / 2;
            var y = (Math.max(0, rect.top) + Math.min(innerHeight, rect.bottom)) / 2;
            var hit = document.elementFromPoint(x, y);
            return rect.width > 0 && rect.height > 0 && x >= 0 && x < innerWidth
              && y >= 0 && y < innerHeight && !!hit
              && (hit === placeholder || placeholder.contains(hit));
          })(),
          buttonText: button?.textContent?.trim() };
      })()`);
      receipt.emptyRelationships = empty;
      requireCheck(empty.rows === 0 && Boolean(empty.text) && empty.visible && empty.exposed
        && empty.buttonText?.includes('0'),
      `Node ${initial.node.id} did not show a visible empty relationship state.`);
      receipt.checks.emptyRelationships = 'pass';
      receipt.checks.feedback = 'not-applicable';
    } else {
      requireCheck(selected.rows > 0 && Boolean(selected.summary?.trim())
        && Boolean(selected.firstRelation?.text) && selected.firstRelation.visible
        && selected.firstRelation.exposed,
      `Node ${initial.node.id} has relationships but the detail panel showed no relationship feedback.`);
      receipt.checks.feedback = 'pass';
    }

    await nativeClick(browser, session, '#btn-focus-clear');
    await settle(browser, session);
    const cleared = await evaluate(browser, session, `(function () {
      var svg = document.querySelector('.diagram-container > svg');
      return { active: Archify.focus.active(), focused: svg.hasAttribute('data-focus-active'),
        selectedNodes: svg.querySelectorAll('[data-focus-selected]').length,
        pressedNodes: svg.querySelectorAll('[data-node-id][aria-pressed="true"]').length,
        chipHidden: document.getElementById('focus-chip').hidden,
        cameraScale: Number(svg.getAttribute('data-view-scale')),
        cameraMode: document.querySelector('.diagram-container').getAttribute('data-camera-mode'),
        hash: location.hash, events: __archifySmokeEvents.slice() };
    })()`);
    receipt.returnToGraph = cleared;
    requireCheck(cleared.events.some(event => event.action === 'clear-click' && event.trusted),
      'The close action was not a trusted browser click.');
    requireCheck(cleared.active == null && !cleared.focused && cleared.selectedNodes === 0
      && cleared.pressedNodes === 0 && cleared.chipHidden && cleared.cameraScale === 1
      && cleared.cameraMode === 'overview' && !cleared.hash,
    'Closing the detail panel did not restore the full graph.');
    receipt.checks.returnToGraph = 'pass';
    receipt.runtimeErrors = await runtimeErrors(browser, session);
    requireCheck(receipt.runtimeErrors.length === 0,
      `Viewer runtime error: ${receipt.runtimeErrors[0]?.message}`);
    requireCheck(sha256(fs.readFileSync(artifact)) === receipt.artifact.sha256,
      'The delivered HTML changed during the interaction smoke.');
    receipt.status = 'pass';
    receipt.ok = true;
  } catch (error) {
    receipt.error = error.message;
    if (browser) {
      try { receipt.runtimeErrors = await runtimeErrors(browser, await browser.sessionPromise); }
      catch { /* Preserve the first browser failure. */ }
    }
  } finally {
    if (browser) {
      try { await browser.close(); }
      catch (error) {
        receipt.status = 'fail';
        receipt.ok = false;
        receipt.error = `Browser cleanup failed: ${error.message}`;
      }
    }
    if (snapshotDirectory) fs.rmSync(snapshotDirectory, { recursive: true, force: true });
  }
  return receipt;
}

export async function runBasicInteractionSmoke(artifactPath, {
  expectedNoRelationships = false, allViewports = false,
} = {}) {
  if (!allViewports) return await runViewportSmoke(artifactPath, VIEWPORT, expectedNoRelationships);
  const viewports = [];
  for (const viewport of ALL_VIEWPORTS) {
    viewports.push(await runViewportSmoke(artifactPath, viewport, expectedNoRelationships));
  }
  const firstHash = viewports[0].artifact.sha256;
  const hashMismatch = Boolean(firstHash) && viewports.some(item => item.artifact.sha256 !== firstHash);
  const ok = Boolean(firstHash) && !hashMismatch && viewports.every(item => item.ok);
  const receipt = {
    schemaVersion: 3,
    command: 'basic-interaction-smoke',
    status: ok ? 'pass' : 'fail',
    ok,
    artifact: viewports[0].artifact,
    relationshipExpectation: expectedNoRelationships ? 'none' : 'present',
    viewports,
  };
  if (!ok) {
    const failures = viewports.filter(item => !item.ok)
      .map(item => `${item.viewport.width}x${item.viewport.height}: ${item.error}`);
    if (hashMismatch) failures.push('The delivered HTML differed between viewport checks.');
    receipt.error = `Viewport checks failed: ${failures.join('; ')}`;
  }
  return receipt;
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const args = process.argv.slice(2);
  const options = args.slice(1);
  const valid = args.length >= 1 && !args[0].startsWith('--')
    && options.every(option => ['--json', '--expected-no-relationships', '--all-viewports'].includes(option))
    && new Set(options).size === options.length;
  if (!valid) {
    console.error('Usage: node scripts/basic-interaction-smoke.mjs <output.html> [--json] [--expected-no-relationships] [--all-viewports]');
    process.exitCode = 2;
  } else {
    const receipt = await runBasicInteractionSmoke(args[0], {
      expectedNoRelationships: options.includes('--expected-no-relationships'),
      allViewports: options.includes('--all-viewports'),
    });
    console.log(JSON.stringify(receipt));
    if (!receipt.ok) process.exitCode = 1;
  }
}
