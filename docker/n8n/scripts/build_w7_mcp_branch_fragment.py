"""Emit docker/n8n/workflows/fragments/w7_mcp_branch.nodes.json with valid JSON."""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "workflows" / "fragments" / "w7_mcp_branch.nodes.json"

READ_PENDING = r"""// w7-mcp-read-pending (PRO-122)
const fs = require('fs');
const MCP = '/miru-data/mcp_gateway_pending_writes.jsonl';
const data = $input.item.json;
const traceId = data.trace_id;
if (!traceId) {
  return { json: { ...data, _mcp_error: 'missing trace_id' } };
}
let content = '';
try {
  if (fs.existsSync(MCP)) content = fs.readFileSync(MCP, 'utf8');
} catch (e) {
  return { json: { ...data, _mcp_error: 'read mcp_gateway_pending_writes: ' + e.message } };
}
const lines = content.split(String.fromCharCode(10)).filter(Boolean);
let row = null;
for (let i = lines.length - 1; i >= 0; i--) {
  let r;
  try { r = JSON.parse(lines[i]); } catch (e2) { continue; }
  if (r.kind === 'intent' && r.request_id === traceId) {
    row = r;
    break;
  }
}
if (!row) {
  return { json: { ...data, _mcp_error: 'no matching intent row for request_id', _mcp_trace: traceId } };
}
const op = row.operation;
let base = String($env.N8N_INTERNAL_API_BASE || 'http://127.0.0.1:5678');
while (base.endsWith('/')) base = base.slice(0, -1);
const apiKey = $env.N8N_API_KEY;
if (!apiKey) {
  return { json: { ...data, _mcp_error: 'N8N_API_KEY not set on n8n container' } };
}
let n8n_method;
let n8n_url;
if (op === 'create_workflow') {
  n8n_method = 'POST';
  n8n_url = base + '/api/v1/workflows';
} else if (op === 'update_workflow') {
  const wid = row.workflow_id;
  if (!wid) {
    return { json: { ...data, _mcp_error: 'update_workflow missing workflow_id' } };
  }
  n8n_method = 'PUT';
  n8n_url = base + '/api/v1/workflows/' + wid;
} else {
  return { json: { ...data, _mcp_error: 'unknown operation: ' + op } };
}
const bodyObj = row.workflow_json;
if (!bodyObj || typeof bodyObj !== 'object') {
  return { json: { ...data, _mcp_error: 'workflow_json missing or invalid' } };
}
const decided_at = new Date().toISOString();
return { json: {
  ...data,
  _mcp_pending_row: row,
  n8n_method,
  n8n_url,
  mcp_mutation_body: bodyObj,
  decided_at,
  action_label: 'Approve (MCP n8n write)'
}};
"""

MARK_APPROVED = r"""// w7-mcp-mark-decided-approved (PRO-122)
const fs = require('fs');
const PENDING = '/miru-data/pending_callbacks.jsonl';
const prior = $('w7-mcp-read-pending').item.json;
const httpOut = $input.item.json;
const decided_at = prior.decided_at || new Date().toISOString();
let applyOk = true;
let applyErr = null;
if (httpOut && httpOut.error) {
  applyOk = false;
  applyErr = (httpOut.error.message && String(httpOut.error.message)) || JSON.stringify(httpOut.error);
} else if (httpOut && httpOut.message && String(httpOut.message).toLowerCase().indexOf('error') >= 0) {
  applyOk = false;
  applyErr = String(httpOut.message);
}
const sc = httpOut.statusCode != null ? httpOut.statusCode : httpOut.status;
if (sc != null && (sc < 200 || sc >= 300)) {
  applyOk = false;
  applyErr = applyErr || ('HTTP ' + sc);
}
const decidedRow = {
  kind: 'decided',
  token: prior.token,
  action: prior.action,
  action_label: prior.action_label || 'Approve (MCP n8n write)',
  decided_at,
  decided_by_user_id: prior.from_user_id != null ? prior.from_user_id : null,
  trace_id: prior.trace_id,
  task_id: prior.issue_id || null,
  task_identifier: prior.issue_identifier || null,
  edit_message_ok: applyOk,
  mcp_n8n_write_outcome: applyOk ? 'applied' : 'n8n_http_failed',
  mcp_n8n_write_error: applyErr
};
fs.appendFileSync(PENDING, JSON.stringify(decidedRow) + String.fromCharCode(10));
return { json: { ...prior, _mcp_apply_ok: applyOk, _mcp_apply_err: applyErr } };
"""

TRIAGE_MARK = r"""// w7-mcp-triage-mark (PRO-122)
const fs = require('fs');
const PENDING = '/miru-data/pending_callbacks.jsonl';
const d = $input.item.json;
const decided_at = new Date().toISOString();
const action_label = 'Triage (declined MCP n8n write)';
const decidedRow = {
  kind: 'decided',
  token: d.token,
  action: d.action,
  action_label,
  decided_at,
  decided_by_user_id: d.from_user_id != null ? d.from_user_id : null,
  trace_id: d.trace_id,
  task_id: d.issue_id || null,
  task_identifier: d.issue_identifier || null,
  edit_message_ok: true,
  mcp_n8n_write_outcome: 'declined_triage'
};
fs.appendFileSync(PENDING, JSON.stringify(decidedRow) + String.fromCharCode(10));
return { json: { ...d, decided_at, action_label } };
"""


def main() -> None:
    nodes = [
        {
            "parameters": {
                "conditions": {
                    "options": {
                        "caseSensitive": True,
                        "leftValue": "",
                        "typeValidation": "strict",
                    },
                    "conditions": [
                        {
                            "id": "mcpb1",
                            "leftValue": "={{ $json.button_set }}",
                            "rightValue": "mcp_n8n_write",
                            "operator": {"type": "string", "operation": "equals"},
                        }
                    ],
                    "combinator": "and",
                },
                "options": {},
            },
            "id": "w7007b-mcp-n8n-if",
            "name": "w7007b-mcp-n8n-if",
            "type": "n8n-nodes-base.if",
            "typeVersion": 2.2,
            "position": [1520, 200],
        },
        {
            "parameters": {
                "conditions": {
                    "options": {
                        "caseSensitive": True,
                        "leftValue": "",
                        "typeValidation": "strict",
                    },
                    "conditions": [
                        {
                            "id": "mcpa1",
                            "leftValue": "={{ $json.action }}",
                            "rightValue": "a",
                            "operator": {"type": "string", "operation": "equals"},
                        }
                    ],
                    "combinator": "and",
                },
                "options": {},
            },
            "id": "w7-mcp-action-if",
            "name": "w7-mcp-action-if",
            "type": "n8n-nodes-base.if",
            "typeVersion": 2.2,
            "position": [1680, 120],
        },
        {
            "parameters": {
                "language": "javaScript",
                "mode": "runOnceForEachItem",
                "jsCode": READ_PENDING,
            },
            "id": "w7-mcp-read-pending",
            "name": "w7-mcp-read-pending",
            "type": "n8n-nodes-base.code",
            "typeVersion": 2,
            "position": [1840, 40],
        },
        {
            "parameters": {
                "conditions": {
                    "options": {
                        "caseSensitive": True,
                        "leftValue": "",
                        "typeValidation": "strict",
                    },
                    "conditions": [
                        {
                            "id": "mcpc1",
                            "leftValue": "={{ $json._mcp_pending_row.operation }}",
                            "rightValue": "create_workflow",
                            "operator": {"type": "string", "operation": "equals"},
                        }
                    ],
                    "combinator": "and",
                },
                "options": {},
            },
            "id": "w7-mcp-if-create",
            "name": "w7-mcp-if-create",
            "type": "n8n-nodes-base.if",
            "typeVersion": 2.2,
            "position": [2000, 40],
        },
        {
            "parameters": {
                "method": "POST",
                "url": "={{ $('w7-mcp-read-pending').item.json.n8n_url }}",
                "sendHeaders": True,
                "headerParameters": {
                    "parameters": [
                        {"name": "X-N8N-API-KEY", "value": "={{ $env.N8N_API_KEY }}"},
                        {"name": "Content-Type", "value": "application/json"},
                    ]
                },
                "sendBody": True,
                "specifyBody": "json",
                "jsonBody": "={{ JSON.stringify($('w7-mcp-read-pending').item.json.mcp_mutation_body) }}",
                "options": {},
            },
            "id": "w7-mcp-http-post",
            "name": "w7-mcp-http-post",
            "type": "n8n-nodes-base.httpRequest",
            "typeVersion": 4.2,
            "position": [2180, -40],
            "retryOnFail": True,
            "maxTries": 2,
            "waitBetweenTries": 3000,
            "continueOnFail": True,
        },
        {
            "parameters": {
                "method": "PUT",
                "url": "={{ $('w7-mcp-read-pending').item.json.n8n_url }}",
                "sendHeaders": True,
                "headerParameters": {
                    "parameters": [
                        {"name": "X-N8N-API-KEY", "value": "={{ $env.N8N_API_KEY }}"},
                        {"name": "Content-Type", "value": "application/json"},
                    ]
                },
                "sendBody": True,
                "specifyBody": "json",
                "jsonBody": "={{ JSON.stringify($('w7-mcp-read-pending').item.json.mcp_mutation_body) }}",
                "options": {},
            },
            "id": "w7-mcp-http-put",
            "name": "w7-mcp-http-put",
            "type": "n8n-nodes-base.httpRequest",
            "typeVersion": 4.2,
            "position": [2180, 120],
            "retryOnFail": True,
            "maxTries": 2,
            "waitBetweenTries": 3000,
            "continueOnFail": True,
        },
        {
            "parameters": {
                "language": "javaScript",
                "mode": "runOnceForEachItem",
                "jsCode": MARK_APPROVED,
            },
            "id": "w7-mcp-mark-decided-approved",
            "name": "w7-mcp-mark-decided-approved",
            "type": "n8n-nodes-base.code",
            "typeVersion": 2,
            "position": [2360, 40],
        },
        {
            "parameters": {
                "method": "POST",
                "url": "=https://api.telegram.org/bot{{ $env.TELEGRAM_BOT_TOKEN }}/editMessageText",
                "sendHeaders": True,
                "headerParameters": {
                    "parameters": [{"name": "Content-Type", "value": "application/json"}]
                },
                "sendBody": True,
                "specifyBody": "json",
                "jsonBody": (
                    "={{ (() => { const d = $('w7-mcp-read-pending').item.json; const r = $json; "
                    "const esc = (s) => String(s == null ? '' : s).replace(/&/g,'&amp;').replace(/</g,'&lt;')"
                    ".replace(/>/g,'&gt;'); const ok = r._mcp_apply_ok; const msg = ok ? 'Applied to n8n.' : "
                    "('FAILED: ' + esc(r._mcp_apply_err || 'unknown')); return JSON.stringify({ chat_id: "
                    "d.chat_id, message_id: d.message_id, text: '<b>MCP n8n write \\u2014 Approve</b>\\n<i>' + "
                    "esc(msg) + '</i>', parse_mode: 'HTML', reply_markup: { inline_keyboard: [] } }); })() }}"
                ),
                "options": {},
            },
            "id": "w7-mcp-edit-approved",
            "name": "w7-mcp-edit-approved",
            "type": "n8n-nodes-base.httpRequest",
            "typeVersion": 4.2,
            "position": [2540, 40],
            "continueOnFail": True,
        },
        {
            "parameters": {
                "language": "javaScript",
                "mode": "runOnceForEachItem",
                "jsCode": TRIAGE_MARK,
            },
            "id": "w7-mcp-triage-mark",
            "name": "w7-mcp-triage-mark",
            "type": "n8n-nodes-base.code",
            "typeVersion": 2,
            "position": [1840, 280],
        },
        {
            "parameters": {
                "method": "POST",
                "url": "=https://api.telegram.org/bot{{ $env.TELEGRAM_BOT_TOKEN }}/editMessageText",
                "sendHeaders": True,
                "headerParameters": {
                    "parameters": [{"name": "Content-Type", "value": "application/json"}]
                },
                "sendBody": True,
                "specifyBody": "json",
                "jsonBody": (
                    "={{ (() => { const d = $('w7-mcp-triage-mark').item.json; const esc = (s) => String(s == null "
                    "? '' : s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); return JSON.stringify"
                    "({ chat_id: d.chat_id, message_id: d.message_id, text: '<b>MCP n8n write \\u2014 Triage</b>"
                    "\\n<i>Declined. No n8n mutation.</i>', parse_mode: 'HTML', reply_markup: { inline_keyboard: [] }"
                    " }); })() }}"
                ),
                "options": {},
            },
            "id": "w7-mcp-edit-triage",
            "name": "w7-mcp-edit-triage",
            "type": "n8n-nodes-base.httpRequest",
            "typeVersion": 4.2,
            "position": [2020, 280],
            "continueOnFail": True,
        },
    ]
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(nodes, indent=2) + "\n", encoding="utf-8")
    print("wrote", OUT)


if __name__ == "__main__":
    main()
