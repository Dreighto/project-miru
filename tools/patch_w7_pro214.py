#!/usr/bin/env python3
"""
PRO-214: Add stale manual-label callback re-mint flow to W7 workflow.

When w7005-validate-branch rejects with stale timestamp AND action=='d'
(manual-label dispatch button), instead of silently noop:
1. Edit original message -> remove keyboard, show expiry notice
2. Mint fresh callback token (same anchor, new 10-min window)
3. Send new Telegram message with fresh inline keyboard
4. Write new pending_callbacks.jsonl entry for fresh token
"""

import json

WORKFLOW_PATH = "docker/n8n/workflows/w7-telegram-callback-handler.json"

# ── Node definitions ───────────────────────────────────────────────────────────

STALE_LOOKUP_CODE = r"""// w7-stale-lookup
// Stale manual-label dispatch: read pending_callbacks.jsonl to find the original
// dispatch row by token so we have chat_id, message_id, worker, and issue context.
const fs = require('fs');
const PENDING = '/miru-data/pending_callbacks.jsonl';

const data = $input.item.json;
const targetToken = data.token;

let content = '';
try {
  if (fs.existsSync(PENDING)) {
    content = fs.readFileSync(PENDING, 'utf8');
  }
} catch (e) {
  throw new Error('pending_callbacks read error: ' + e.message);
}

const lines = content.split('\n').filter(Boolean);
let dispatchRow = null;
for (let i = lines.length - 1; i >= 0; i--) {
  let row;
  try { row = JSON.parse(lines[i]); } catch (e) { continue; }
  if (row.token !== targetToken) continue;
  if (row.kind === 'dispatch' && row.manual_label) {
    dispatchRow = row;
    break;
  }
}

if (!dispatchRow) {
  throw new Error('no manual-label dispatch row found for token: ' + targetToken);
}

const originalText = data.message_text || '';
const expiredText = (originalText ? originalText + '\n\n' : '') + '⚠️ Approval expired — re-minting...';

const telegram_edit_body = {
  chat_id: dispatchRow.dispatch_chat_id,
  message_id: dispatchRow.dispatch_message_id,
  text: expiredText,
  parse_mode: 'HTML',
  reply_markup: { inline_keyboard: [] }
};

return { json: {
  ...data,
  dispatch_row: dispatchRow,
  dispatch_chat_id: dispatchRow.dispatch_chat_id,
  dispatch_message_id: dispatchRow.dispatch_message_id,
  original_worker: dispatchRow.worker,
  original_issue_id: dispatchRow.issue_id,
  original_issue_identifier: dispatchRow.issue_identifier,
  original_issue_url: dispatchRow.issue_url,
  original_trace_id: dispatchRow.trace_id,
  original_triaged_first: dispatchRow.triaged_first || false,
  telegram_edit_body
}};"""

STALE_MINT_FRESH_CODE = r"""// w7-stale-mint-fresh
// Re-mint a fresh dispatch callback for an expired manual-label button.
// Writes a new pending_callbacks row and builds the sendMessage body.
// HMAC anchor MUST match w7004-hmac-validate (1767225600 = 2026-01-01T00:00:00Z).
const crypto = require('crypto');
const fs = require('fs');
const PENDING = '/miru-data/pending_callbacks.jsonl';
const ANCHOR_UNIX = 1767225600;

const data = $input.item.json;
const secret = $env.TELEGRAM_CALLBACK_SECRET;
const chatId = $env.TELEGRAM_CHAT_ID;
if (!secret) throw new Error('TELEGRAM_CALLBACK_SECRET not set');
if (!chatId) throw new Error('TELEGRAM_CHAT_ID not set');

const worker = data.original_worker;
const LABEL_MAP = {
  'claude-code': '🚀 Dispatch Claude Code',
  'codex': '🚀 Dispatch Codex',
  'gemini': '🚀 Dispatch Gemini CLI',
  'cursor': '🔗 Open in Linear (Cursor)'
};
const buttonText = LABEL_MAP[worker];
if (!buttonText) throw new Error('no button label for worker: ' + worker);

const token = crypto.randomBytes(8).toString('hex').slice(0, 12);
const nonce = crypto.randomBytes(4).toString('hex');
const ts_minutes = Math.floor((Math.floor(Date.now()/1000) - ANCHOR_UNIX) / 60);
const ts_hex = ts_minutes.toString(16).padStart(8, '0').slice(-8);
const payload = token + 'd' + nonce + ts_hex;
const hmac = crypto.createHmac('sha256', secret).update(payload).digest('hex').slice(0, 32);
const callback_data = token + 'd' + nonce + ts_hex + hmac;
if (callback_data.length !== 61) throw new Error('callback_data length mismatch: ' + callback_data.length);

function esc(s) { return String(s == null ? '' : s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }

const issueIdent = esc(data.original_issue_identifier || '?');
const issueTitle = esc(data.dispatch_row && data.dispatch_row.issue_title ? data.dispatch_row.issue_title : '');
const stateLabel = data.original_triaged_first ? 'triaged-then-labeled' : 'pre-labeled';
const text = '<b>🏷️ Manually labeled: ' + esc(worker) + '</b>\n'
  + '<i>' + issueIdent + (issueTitle ? (' — ' + issueTitle) : '') + '</i>\n'
  + '<i>(' + stateLabel + ') — re-minted</i>';

const telegram_send_body = {
  chat_id: data.dispatch_chat_id || chatId,
  text,
  parse_mode: 'HTML',
  reply_markup: { inline_keyboard: [[{ text: buttonText, callback_data }]] }
};

const issue_url = data.original_issue_url || (data.original_issue_identifier
  ? ('https://linear.app/project-miru/issue/' + data.original_issue_identifier)
  : null);

const row = {
  schema_version: 'v1',
  kind: 'dispatch',
  token,
  trace_id: data.original_trace_id || token,
  worker,
  flow: 'manual-label',
  issue_id: data.original_issue_id || null,
  issue_identifier: data.original_issue_identifier || null,
  issue_url,
  dispatch_chat_id: data.dispatch_chat_id || null,
  dispatch_message_id: null,
  dispatch_callback_data: callback_data,
  prompt_path: data.dispatch_row && data.dispatch_row.prompt_path ? data.dispatch_row.prompt_path : null,
  status: 'awaiting',
  created_at: new Date().toISOString(),
  send_message_ok: null,
  manual_label: true,
  triaged_first: data.original_triaged_first || false,
  reminted_from_token: data.token
};
fs.appendFileSync(PENDING, JSON.stringify(row) + '\n');

return { json: { ...data, fresh_token: token, fresh_callback_data: callback_data, telegram_send_body } };"""

HTTP_HEADERS = {"parameters": [{"name": "Content-Type", "value": "application/json"}]}

NEW_NODES = [
    {
        "id": "stale-check-001",
        "name": "w7-check-stale-dispatch",
        "type": "n8n-nodes-base.if",
        "typeVersion": 2,
        "position": [1200, 640],
        "parameters": {
            "conditions": {
                "options": {"caseSensitive": True, "leftValue": "", "typeValidation": "strict"},
                "conditions": [
                    {
                        "id": "sc1",
                        "leftValue": "={{ $json.reject_reason }}",
                        "rightValue": "older than 10 min",
                        "operator": {"type": "string", "operation": "contains"},
                    },
                    {
                        "id": "sc2",
                        "leftValue": "={{ $json.action }}",
                        "rightValue": "d",
                        "operator": {"type": "string", "operation": "equals"},
                    },
                ],
                "combinator": "and",
            }
        },
    },
    {
        "id": "stale-lookup-001",
        "name": "w7-stale-lookup",
        "type": "n8n-nodes-base.code",
        "typeVersion": 2,
        "position": [1420, 760],
        "parameters": {
            "language": "javaScript",
            "mode": "runOnceForEachItem",
            "jsCode": STALE_LOOKUP_CODE,
        },
    },
    {
        "id": "stale-edit-001",
        "name": "w7-stale-edit-expired",
        "type": "n8n-nodes-base.httpRequest",
        "typeVersion": 4.2,
        "position": [1640, 760],
        "parameters": {
            "method": "POST",
            "url": "=https://api.telegram.org/bot{{ $env.TELEGRAM_BOT_TOKEN }}/editMessageText",
            "sendHeaders": True,
            "headerParameters": HTTP_HEADERS,
            "sendBody": True,
            "specifyBody": "json",
            "jsonBody": "={{ JSON.stringify($json.telegram_edit_body) }}",
            "options": {"response": {"response": {"responseFormat": "json"}}},
        },
    },
    {
        "id": "stale-mint-001",
        "name": "w7-stale-mint-fresh",
        "type": "n8n-nodes-base.code",
        "typeVersion": 2,
        "position": [1860, 760],
        "parameters": {
            "language": "javaScript",
            "mode": "runOnceForEachItem",
            "jsCode": STALE_MINT_FRESH_CODE,
        },
    },
    {
        "id": "stale-send-001",
        "name": "w7-stale-send-fresh",
        "type": "n8n-nodes-base.httpRequest",
        "typeVersion": 4.2,
        "position": [2080, 760],
        "parameters": {
            "method": "POST",
            "url": "=https://api.telegram.org/bot{{ $env.TELEGRAM_BOT_TOKEN }}/sendMessage",
            "sendHeaders": True,
            "headerParameters": HTTP_HEADERS,
            "sendBody": True,
            "specifyBody": "json",
            "jsonBody": "={{ JSON.stringify($json.telegram_send_body) }}",
            "options": {"response": {"response": {"responseFormat": "json"}}},
        },
    },
]

NEW_CONNECTIONS = {
    "w7-check-stale-dispatch": {
        "main": [
            # TRUE: stale + action='d' -> stale lookup
            [{"node": "w7-stale-lookup", "type": "main", "index": 0}],
            # FALSE: bad HMAC / replay / non-dispatch -> existing noop
            [{"node": "w7-noop-rejected", "type": "main", "index": 0}],
        ]
    },
    "w7-stale-lookup": {"main": [[{"node": "w7-stale-edit-expired", "type": "main", "index": 0}]]},
    "w7-stale-edit-expired": {
        "main": [[{"node": "w7-stale-mint-fresh", "type": "main", "index": 0}]]
    },
    "w7-stale-mint-fresh": {
        "main": [[{"node": "w7-stale-send-fresh", "type": "main", "index": 0}]]
    },
}


def patch(path: str) -> None:
    with open(path, encoding="utf-8") as f:
        workflow = json.load(f)

    # 1. Reroute w7005-validate-branch FALSE output to w7-check-stale-dispatch
    conns = workflow["connections"]
    false_branch = conns["w7005-validate-branch"]["main"][1]
    assert (
        len(false_branch) == 1 and false_branch[0]["node"] == "w7-noop-rejected"
    ), f"Expected w7-noop-rejected at FALSE branch, got: {false_branch}"
    conns["w7005-validate-branch"]["main"][1] = [
        {"node": "w7-check-stale-dispatch", "type": "main", "index": 0}
    ]

    # 2. Add new nodes
    existing_names = {n["name"] for n in workflow["nodes"]}
    for node in NEW_NODES:
        assert node["name"] not in existing_names, f"Node already exists: {node['name']}"
        workflow["nodes"].append(node)

    # 3. Add new connections
    for src, conn in NEW_CONNECTIONS.items():
        assert src not in conns, f"Connection already exists for: {src}"
        conns[src] = conn

    with open(path, "w", encoding="utf-8") as f:
        json.dump(workflow, f, indent=2, ensure_ascii=False)
        f.write("\n")

    print(f"Patched {path}: +{len(NEW_NODES)} nodes, +{len(NEW_CONNECTIONS)} connection blocks")


if __name__ == "__main__":
    patch(WORKFLOW_PATH)
