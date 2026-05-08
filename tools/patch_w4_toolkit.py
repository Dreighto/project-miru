"""One-shot script to patch w4021-assemble-prompt with toolkit packing logic."""

from __future__ import annotations

import json
import sys
from pathlib import Path

WORKFLOW_PATH = Path("docker/n8n/workflows/w4-dispatch-button-handler.json")

TOOLKIT_JS = r"""
// --- Toolkit packing (PRO-324) ---
let toolkitBlock = '';
try {
  const rulesRaw = fs.readFileSync('/miru-data/config/toolkit_signal_rules.json', 'utf8');
  const rulesDoc = JSON.parse(rulesRaw);
  const labelsList = (prior.labels || []).map(l => typeof l === 'string' ? l : (l.name || ''));
  const combinedText = (title + ' ' + description + ' ' + labelsList.join(' ')).toLowerCase();

  const matchedFiles = [];
  const matchedTools = [];
  const matchedServices = [];
  const extraNotes = [];

  for (const rule of rulesDoc.signal_rules) {
    const hit = (rule.keywords || []).some(kw => new RegExp(kw, 'i').test(combinedText));
    if (hit) {
      matchedFiles.push(...(rule.files || []));
      matchedTools.push(...(rule.tools || []));
      matchedServices.push(...(rule.services || []));
      if (rule.extra_note) extraNotes.push(rule.extra_note);
    }
  }

  const dedup = arr => [...new Set(arr)];
  const files = dedup(matchedFiles);
  const tools = dedup(matchedTools);
  const services = dedup(matchedServices);
  const dontTouch = rulesDoc.global_dont_touch || [];
  const readOnly = dedup(rulesDoc.global_read_only || []);

  if (files.length || tools.length || services.length) {
    const sections = ['## Toolkit Context (auto-generated)'];
    if (services.length) {
      sections.push('');
      sections.push('### Service boundaries (in scope)');
      services.forEach(s => sections.push('- `' + s + '`'));
    }
    if (files.length) {
      sections.push('');
      sections.push('### Start here (relevant files)');
      files.forEach(f => sections.push('- `' + f + '`'));
    }
    if (tools.length) {
      sections.push('');
      sections.push('### Useful tools');
      tools.forEach(t => sections.push('- `' + t + '`'));
    }
    if (dontTouch.length) {
      sections.push('');
      sections.push('### Do NOT modify');
      dontTouch.forEach(d => sections.push('- `' + d + '`'));
    }
    if (readOnly.length) {
      sections.push('');
      sections.push('### Read-only (append-only or protected)');
      readOnly.forEach(r => sections.push('- `' + r + '`'));
    }
    if (extraNotes.length) {
      sections.push('');
      sections.push('### Notes');
      extraNotes.forEach(n => sections.push('- ' + n));
    }
    toolkitBlock = sections.join('\n');
  }
} catch (_toolkitErr) {
  // Toolkit packing is advisory - do not fail the dispatch
}
"""

TOOLKIT_PROMPT_LINE = "  toolkitBlock ? toolkitBlock : '',"


def patch() -> None:
    wf = json.loads(WORKFLOW_PATH.read_text(encoding="utf-8"))

    nodes = wf.get("nodes", [])
    target = None
    for node in nodes:
        if node.get("id") == "w4021-assemble-prompt":
            target = node
            break

    if target is None:
        print("ERROR: w4021-assemble-prompt node not found", file=sys.stderr)
        sys.exit(1)

    code = target["parameters"]["jsCode"]

    if "toolkitBlock" in code:
        print("Already patched — toolkitBlock found in w4021. Skipping.")
        return

    # Insert toolkit JS right before the promptText array
    anchor = "const promptText = ["
    if anchor not in code:
        print(f"ERROR: anchor '{anchor}' not found in jsCode", file=sys.stderr)
        sys.exit(1)

    code = code.replace(anchor, TOOLKIT_JS.strip() + "\n\n" + anchor)

    # Insert toolkit block into the prompt array, after the "Before starting work" section
    # and before the "## Ticket description" section
    ticket_desc_anchor = "  '## Ticket description',"
    if ticket_desc_anchor not in code:
        print(f"ERROR: anchor '{ticket_desc_anchor}' not found", file=sys.stderr)
        sys.exit(1)

    code = code.replace(
        ticket_desc_anchor,
        TOOLKIT_PROMPT_LINE + "\n  '',\n" + ticket_desc_anchor,
    )

    target["parameters"]["jsCode"] = code
    WORKFLOW_PATH.write_text(json.dumps(wf, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print("Patched w4021-assemble-prompt with toolkit packing logic.")


if __name__ == "__main__":
    patch()
