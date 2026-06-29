#!/usr/bin/env node
const http = require('http');
const readline = require('readline');
const { spawn, execSync } = require('child_process');
const fs = require('fs');
const path = require('path');

const CAMOFOX_URL = process.env.CAMOFOX_URL || 'http://localhost:9377';
const API_KEY = process.env.CAMOFOX_API_KEY || 'local-dev-key';
const USER_ID = 'claude-mcp';
const SESSION_KEY = 'default';
const IS_WINDOWS = process.platform === 'win32';

// Auto-patch the camofox-browser package to fix the isMobile viewport bug.
// Playwright >=1.58 sends isMobile:false in viewport config but Camoufox
// (Firefox-based) doesn't support that property — setting viewport:null skips it.
function applyViewportPatch() {
  try {
    const globalRoot = execSync('npm root -g', { encoding: 'utf8' }).trim();
    const serverPath = path.join(globalRoot, '@askjo', 'camofox-browser', 'server.js');
    if (!fs.existsSync(serverPath)) return;
    const original = fs.readFileSync(serverPath, 'utf8');
    const patched = original.replace(/viewport: \{ width: 1280, height: 720 \}/g, 'viewport: null');
    if (patched !== original) fs.writeFileSync(serverPath, patched, 'utf8');
  } catch (_) {
    // Non-fatal: server may still work if already patched or on a fixed version
  }
}

function startCamofox() {
  applyViewportPatch();
  return new Promise((resolve, reject) => {
    const cmd = IS_WINDOWS ? 'camofox-browser.cmd' : 'camofox-browser';
    const proc = spawn(cmd, [], {
      stdio: ['ignore', 'pipe', 'pipe'],
      detached: false,
      shell: IS_WINDOWS,
    });
    proc.on('error', (err) => {
      reject(new Error(`Failed to start camofox-browser: ${err.message}. Is it installed? Run: npm install -g @askjo/camofox-browser`));
    });
    process.on('exit', () => { try { proc.kill(); } catch (_) {} });
    process.on('SIGINT', () => { try { proc.kill(); } catch (_) {} process.exit(); });
    process.on('SIGTERM', () => { try { proc.kill(); } catch (_) {} process.exit(); });
    setTimeout(() => resolve(proc), 2000);
  });
}

startCamofox().catch((err) => {
  process.stderr.write(`[camofox-mcp] ${err.message}\n`);
  process.exit(1);
});

function callCamofox(method, endpoint, data = null) {
  return new Promise((resolve, reject) => {
    const url = new URL(endpoint, CAMOFOX_URL);
    const body = data ? JSON.stringify(data) : null;
    const options = {
      method,
      headers: {
        'Content-Type': 'application/json',
        'X-API-Key': API_KEY,
        ...(body ? { 'Content-Length': Buffer.byteLength(body) } : {}),
      },
    };
    const req = http.request(url, options, (res) => {
      let raw = '';
      res.on('data', (chunk) => (raw += chunk));
      res.on('end', () => {
        try { resolve({ status: res.statusCode, body: JSON.parse(raw || '{}') }); }
        catch { resolve({ status: res.statusCode, body: raw }); }
      });
    });
    req.on('error', reject);
    if (body) req.write(body);
    req.end();
  });
}

const TOOLS = [
  {
    name: 'browse',
    description: 'Open a URL in Camofox browser — returns a tabId for subsequent actions',
    inputSchema: {
      type: 'object',
      properties: { url: { type: 'string', description: 'URL to navigate to' } },
      required: ['url'],
    },
  },
  {
    name: 'snapshot',
    description: 'Get the accessible DOM snapshot of the current page (use tabId from browse)',
    inputSchema: {
      type: 'object',
      properties: { tabId: { type: 'string', description: 'Tab ID from browse' } },
      required: ['tabId'],
    },
  },
  {
    name: 'screenshot',
    description: 'Take a screenshot of the current page',
    inputSchema: {
      type: 'object',
      properties: { tabId: { type: 'string' } },
      required: ['tabId'],
    },
  },
  {
    name: 'click',
    description: 'Click an element on the page using its ref from snapshot',
    inputSchema: {
      type: 'object',
      properties: {
        tabId: { type: 'string' },
        ref: { type: 'string', description: 'Element ref from snapshot' },
      },
      required: ['tabId', 'ref'],
    },
  },
  {
    name: 'type',
    description: 'Type text into an element',
    inputSchema: {
      type: 'object',
      properties: {
        tabId: { type: 'string' },
        ref: { type: 'string' },
        text: { type: 'string' },
      },
      required: ['tabId', 'ref', 'text'],
    },
  },
  {
    name: 'navigate',
    description: 'Navigate an existing tab to a new URL',
    inputSchema: {
      type: 'object',
      properties: {
        tabId: { type: 'string' },
        url: { type: 'string' },
      },
      required: ['tabId', 'url'],
    },
  },
];

async function handleToolCall(name, args) {
  switch (name) {
    case 'browse':
      return callCamofox('POST', '/tabs', { userId: USER_ID, sessionKey: SESSION_KEY, url: args.url });
    case 'navigate':
      return callCamofox('POST', `/tabs/${args.tabId}/navigate`, { url: args.url, userId: USER_ID });
    case 'snapshot':
      return callCamofox('GET', `/tabs/${args.tabId}/snapshot?userId=${USER_ID}`);
    case 'screenshot':
      return callCamofox('GET', `/tabs/${args.tabId}/screenshot?userId=${USER_ID}`);
    case 'click':
      return callCamofox('POST', `/tabs/${args.tabId}/click`, { ref: args.ref, userId: USER_ID });
    case 'type':
      return callCamofox('POST', `/tabs/${args.tabId}/type`, { ref: args.ref, text: args.text, userId: USER_ID });
    default:
      throw new Error(`Unknown tool: ${name}`);
  }
}

function send(obj) {
  process.stdout.write(JSON.stringify(obj) + '\n');
}

const rl = readline.createInterface({ input: process.stdin, terminal: false });

rl.on('line', async (line) => {
  let msg;
  try { msg = JSON.parse(line); } catch { return; }

  const { id, method, params } = msg;

  if (method === 'initialize') {
    send({
      jsonrpc: '2.0', id,
      result: {
        protocolVersion: '2024-11-05',
        serverInfo: { name: 'camofox', version: '1.0.0' },
        capabilities: { tools: {} },
      },
    });
  } else if (method === 'tools/list') {
    send({ jsonrpc: '2.0', id, result: { tools: TOOLS } });
  } else if (method === 'tools/call') {
    try {
      const result = await handleToolCall(params.name, params.arguments || {});
      send({
        jsonrpc: '2.0', id,
        result: { content: [{ type: 'text', text: JSON.stringify(result, null, 2) }] },
      });
    } catch (err) {
      send({
        jsonrpc: '2.0', id,
        result: { content: [{ type: 'text', text: `Error: ${err.message}` }], isError: true },
      });
    }
  } else if (id !== undefined) {
    send({ jsonrpc: '2.0', id, error: { code: -32601, message: 'Method not found' } });
  }
});
