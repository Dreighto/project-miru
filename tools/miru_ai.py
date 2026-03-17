#!/usr/bin/env python
import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.miru_ai_onepiece import load_onepiece_knowledge


DEFAULT_MODEL = os.getenv("MIRU_AI_MODEL", "gpt-5-mini")
DEFAULT_TIMEOUT = float(os.getenv("MIRU_AI_TIMEOUT_SECONDS", "45"))
API_URL = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1/responses")

CODEX_PROMPT_HEADING = "Codex implementation prompt"
CODEX_SECTION_ORDER = (
    "Goal",
    "Context",
    "Files likely involved",
    "Requirements",
    "Constraints",
    "Verification",
)
CODEX_SECTION_ALIASES = {
    "goal": "Goal",
    "context": "Context",
    "relevant one piece tcg context": "Context",
    "files likely involved": "Files likely involved",
    "requirements": "Requirements",
    "constraints": "Constraints",
    "verification": "Verification",
    "objective": "Goal",
    "assumptions": "Context",
    "implementation outline": "Requirements",
    "risks / edge cases": "Constraints",
}


def build_common_instructions(task_instructions):
    return (
        "Return plain text only. No JSON, XML, markdown tables, bullets-as-data, "
        "or code fences. Give only the final answer with the requested section headings. "
        "Do not include hidden analysis, reasoning traces, or chain-of-thought. "
        "Keep the reply concise and easy to read in a terminal. "
        "If card identity or variant details are uncertain, state the assumption instead "
        "of inventing a fact.\n\n"
        + task_instructions
    )


def build_parser():
    parser = argparse.ArgumentParser(
        description="One Piece TCG knowledge engine for OP Miru with local analysis, matching, development, and Codex prompt support."
    )
    parser.add_argument(
        "--model",
        default=DEFAULT_MODEL,
        help=f"OpenAI model to use (default: {DEFAULT_MODEL})",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_TIMEOUT,
        help=f"Request timeout in seconds (default: {DEFAULT_TIMEOUT:g})",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    knowledge_parser = subparsers.add_parser(
        "knowledge", help="Return structured One Piece TCG knowledge for a query."
    )
    knowledge_parser.add_argument("prompt", help="Card, set, variant, or gameplay question")

    analysis_parser = subparsers.add_parser(
        "analysis", help="Analyze a card, effect, mechanic, or set using local One Piece knowledge."
    )
    analysis_parser.add_argument("prompt", help="Analysis request")

    matching_parser = subparsers.add_parser(
        "matching", help="Identify likely cards, sets, or variants from a rough description."
    )
    matching_parser.add_argument("prompt", help="Matching or identification request")

    development_parser = subparsers.add_parser(
        "development", help="Turn a Miru dashboard request into a local development brief with OPTCG context."
    )
    development_parser.add_argument("prompt", help="Miru development request")

    review_parser = subparsers.add_parser(
        "review", help="Review a local file and print concise feedback."
    )
    review_parser.add_argument("target", help="Path to the file to review")

    plan_parser = subparsers.add_parser(
        "plan", help="Turn a feature idea into an implementation plan."
    )
    plan_parser.add_argument("prompt", help="Feature description")

    debug_parser = subparsers.add_parser(
        "debug", help="Turn a bug description into a debug checklist and fix plan."
    )
    debug_parser.add_argument("prompt", help="Bug description")

    codex_parser = subparsers.add_parser(
        "codex-prompt",
        help="Turn a rough request into a paste-ready Codex implementation prompt with OPTCG understanding first.",
    )
    codex_parser.add_argument("prompt", help="Rough Codex prompt request")

    return parser


def require_api_key():
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if api_key:
        return api_key

    print("Error: OPENAI_API_KEY is not set.", file=sys.stderr)
    sys.exit(2)


def read_target_file(path_str):
    path = Path(path_str)
    if not path.is_file():
        print(f"Error: file not found: {path}", file=sys.stderr)
        sys.exit(2)

    try:
        return path.resolve(), path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        print(f"Error: could not decode file as UTF-8: {path}", file=sys.stderr)
        sys.exit(2)


def build_review_prompt(path, content, knowledge):
    domain_guidance = knowledge.build_review_guidance()
    return {
        "instructions": build_common_instructions(
            "You are reviewing code for OP Miru, a Flask dashboard project. "
            "Provide a concise code review in plain text. Prioritize bugs, regressions, "
            "safety issues, missing tests, and One Piece TCG-specific correctness checks. "
            "Findings first. If there are no major findings, say so directly, then list "
            "any residual risks or verification gaps."
        ),
        "input": (
            f"Review this file: {path}\n\n"
            "Write a short terminal-friendly review with two plain text sections:\n"
            "Findings\n"
            "Risks / verification gaps\n\n"
            f"{domain_guidance}\n\n"
            "File contents:\n"
            "```text\n"
            f"{content}\n"
            "```"
        ),
        "max_output_tokens": 1200,
    }


def build_plan_prompt(description, domain_context):
    return {
        "instructions": build_common_instructions(
            "You are helping plan a change for OP Miru. Treat One Piece TCG card, set, "
            "variant, and effect facts as domain-sensitive data. Keep the plan minimal, "
            "safe, and reversible. Respect the rule that app.py is authoritative unless "
            "explicitly replaced. Prefer sidecar tools and local edits over broad refactors."
        ),
        "input": (
            "Turn this rough request into a concise implementation plan.\n\n"
            f"Feature request:\n{description}\n\n"
            f"{domain_context}\n\n"
            "Output sections:\n"
            "Objective\n"
            "Assumptions\n"
            "Implementation outline\n"
            "Risks / edge cases\n"
            "Verification"
        ),
        "max_output_tokens": 1000,
    }


def build_debug_prompt(description, domain_context):
    return {
        "instructions": build_common_instructions(
            "You are debugging issues in OP Miru. Use One Piece TCG card, set, variant, "
            "and effect context when it is relevant. Focus on likely causes, the safest "
            "inspection order, and a practical fix plan. Keep the output concise."
        ),
        "input": (
            "Turn this bug report into a likely-cause checklist and fix plan.\n\n"
            f"Bug report:\n{description}\n\n"
            f"{domain_context}\n\n"
            "Output sections:\n"
            "Symptom\n"
            "Likely causes\n"
            "Relevant card-data or variant-data context\n"
            "Files / logic to inspect\n"
            "Verification steps"
        ),
        "max_output_tokens": 1000,
    }


def build_codex_prompt(description, domain_context):
    return {
        "instructions": build_common_instructions(
            "You are translating rough OP Miru requests into paste-ready Codex prompts. "
            "You are not writing a project plan. Write a terse implementation prompt "
            "another coding agent can execute immediately. Be concise, specific, and "
            "implementation-oriented. Keep each section short. Prefer imperative "
            "requirements over planning prose. Include One Piece TCG context only when "
            "it is relevant and supported by the provided facts. "
            "The first line must be exactly 'Codex implementation prompt'. "
            "Use exactly these section headings after that title: Goal, Context, "
            "Files likely involved, Requirements, Constraints, Verification. "
            "Do not use Plan-style headings such as Objective, Assumptions, or "
            "Implementation outline. Avoid rollout plans, phased workstreams, or wrapper "
            "service suggestions unless the request clearly asks for them."
        ),
        "input": (
            "Turn this rough request into a concise Codex implementation prompt.\n\n"
            f"Rough request:\n{description}\n\n"
            f"{domain_context}\n\n"
            "Return this exact shape:\n"
            "Codex implementation prompt\n"
            "Goal\n"
            "Context\n"
            "Files likely involved\n"
            "Requirements\n"
            "Constraints\n"
            "Verification"
        ),
        "max_output_tokens": 700,
    }


def normalize_section_heading(line: str) -> str:
    return line.strip().rstrip(":").strip().lower()


def trim_domain_context_for_codex(domain_context: str) -> str:
    lines = []
    for raw_line in (domain_context or "").splitlines():
        line = raw_line.strip()
        if not line or line == "One Piece TCG context":
            continue
        if "In Codex Prompt mode" in line:
            continue
        lines.append(line)
    return "\n".join(lines)


def parse_codex_like_sections(text: str) -> tuple[dict[str, list[str]], list[str]]:
    sections = {heading: [] for heading in CODEX_SECTION_ORDER}
    intro_lines: list[str] = []
    current_section = ""

    for raw_line in (text or "").splitlines():
        line = raw_line.rstrip()
        stripped = line.strip()
        if not stripped:
            if current_section:
                sections[current_section].append("")
            elif intro_lines:
                intro_lines.append("")
            continue
        if stripped.lower() == CODEX_PROMPT_HEADING:
            continue

        canonical_heading = CODEX_SECTION_ALIASES.get(normalize_section_heading(stripped))
        if canonical_heading:
            current_section = canonical_heading
            continue

        if current_section:
            sections[current_section].append(stripped)
        else:
            intro_lines.append(stripped)

    return sections, intro_lines


def join_section_lines(lines):
    cleaned = "\n".join(line.rstrip() for line in lines).strip()
    return cleaned


def split_prompt_units(text: str) -> list[str]:
    raw_units = []
    for line in (text or "").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        normalized = re.sub(r"^[\-\*\d\.\)\s]+", "", stripped).strip()
        if normalized:
            raw_units.append(normalized)

    units: list[str] = []
    seen = set()
    for raw_unit in raw_units:
        parts = re.split(r"(?<=[.!?])\s+(?=[A-Z0-9\[])", raw_unit)
        for part in parts:
            cleaned = re.sub(r"\s+", " ", part).strip(" -")
            if not cleaned:
                continue
            cleaned = cleaned.rstrip(".")
            cleaned = cleaned[:157].rstrip(" ,;:") + ("..." if len(cleaned) > 157 else "")
            key = cleaned.lower()
            if key in seen:
                continue
            seen.add(key)
            units.append(cleaned)
    return units


def format_prompt_units(units: list[str], *, max_units: int, bullet: bool) -> str:
    chosen = units[:max_units]
    if not chosen:
        return ""
    if bullet:
        return "\n".join(f"- {item}" for item in chosen)
    return chosen[0]


def clean_codex_goal(text: str, request_text: str) -> str:
    cleaned = re.sub(
        r"^(turn this request into (?:a )?(?:safe |concise |paste-ready )?(?:implementation change|codex prompt) for op miru:\s*)",
        "",
        text,
        flags=re.I,
    )
    cleaned = re.sub(
        r"^(plan a practical implementation path for:\s*)",
        "",
        cleaned,
        flags=re.I,
    )
    cleaned = cleaned.strip(" -")
    if not cleaned:
        cleaned = request_text.strip()
    return cleaned[:157].rstrip(" ,;:") + ("..." if len(cleaned) > 157 else "")


def infer_file_hints(request_text: str) -> list[str]:
    lower = (request_text or "").lower()
    hints = []
    explicit_files = (
        "dashboard/app.py",
        "tools/miru_ai.py",
        "tools/miru_ai_server.py",
        "tools/static/miru_ai.js",
        "tools/templates/miru_ai.html",
    )
    for explicit_file in explicit_files:
        if explicit_file.lower() in lower and explicit_file not in hints:
            hints.append(explicit_file)

    if not hints and ("app.py" in lower or any(token in lower for token in ("variant", "promo", "filename", "card code", "matching"))):
        hints.append("dashboard/app.py")

    return hints[:4]


def format_codex_prompt_output(raw_text: str, request_text: str, domain_context: str) -> str:
    sections, intro_lines = parse_codex_like_sections(raw_text)
    context_fallback = trim_domain_context_for_codex(domain_context)
    request_goal = re.sub(r"\s+", " ", (request_text or "").strip())

    if not join_section_lines(sections["Goal"]):
        sections["Goal"] = intro_lines or [request_goal]

    if not join_section_lines(sections["Context"]):
        if context_fallback:
            sections["Context"] = context_fallback.splitlines()
        else:
            sections["Context"] = [
                "Use explicit One Piece TCG facts only when they are confirmed. State assumptions instead of guessing."
            ]

    if not join_section_lines(sections["Files likely involved"]):
        inferred_files = infer_file_hints(request_text)
        sections["Files likely involved"] = inferred_files or [
            "Infer the minimal affected files from the repo before editing."
        ]

    if not join_section_lines(sections["Requirements"]):
        sections["Requirements"] = [
            "Implement the requested change with minimal scope and preserve current behavior unless the request says otherwise."
        ]

    if not join_section_lines(sections["Constraints"]):
        sections["Constraints"] = [
            "Respect OP Miru project constraints, keep One Piece TCG facts explicit, and do not hallucinate unsupported card details."
        ]

    if not join_section_lines(sections["Verification"]):
        sections["Verification"] = [
            "Verify the affected behavior directly and note any assumptions or gaps."
        ]

    output_sections = {}
    raw_goal = format_prompt_units(
        split_prompt_units(join_section_lines(sections["Goal"])) or [request_goal],
        max_units=1,
        bullet=False,
    )
    output_sections["Goal"] = clean_codex_goal(raw_goal, request_goal)
    output_sections["Context"] = format_prompt_units(
        split_prompt_units(join_section_lines(sections["Context"])) or split_prompt_units(context_fallback),
        max_units=4,
        bullet=True,
    )
    output_sections["Files likely involved"] = format_prompt_units(
        split_prompt_units(join_section_lines(sections["Files likely involved"])),
        max_units=4,
        bullet=True,
    )
    output_sections["Requirements"] = format_prompt_units(
        split_prompt_units(join_section_lines(sections["Requirements"])),
        max_units=4,
        bullet=True,
    )
    output_sections["Constraints"] = format_prompt_units(
        split_prompt_units(join_section_lines(sections["Constraints"])),
        max_units=4,
        bullet=True,
    )
    output_sections["Verification"] = format_prompt_units(
        split_prompt_units(join_section_lines(sections["Verification"])),
        max_units=3,
        bullet=True,
    )

    output_lines = [CODEX_PROMPT_HEADING, ""]
    for heading in CODEX_SECTION_ORDER:
        output_lines.append(heading)
        output_lines.append(output_sections[heading])
        output_lines.append("")

    return "\n".join(output_lines).strip()


def should_format_codex_prompt_text(text: str) -> bool:
    stripped = (text or "").strip()
    if not stripped:
        return False
    return not (
        stripped.startswith("API returned reasoning without a final text answer.")
        or stripped.startswith("No assistant text found.")
    )


def section_lines(title: str, values: list[str]) -> list[str]:
    lines = [title]
    for value in values:
        prefix = "" if str(value).startswith("-") else "- "
        lines.append(f"{prefix}{value}".rstrip())
    return lines


def render_structured_understanding(understanding: dict) -> str:
    sections = understanding.get("sections") or {}
    lines = ["OPTCG understanding", f"- Query focus: {', '.join(understanding.get('focuses') or ['general optcg knowledge'])}"]
    order = (
        "Detected card references",
        "Detected set references",
        "Detected variant language",
        "Detected gameplay mechanics",
        "Detected sub-questions",
        "Card metadata",
        "Missing or unknown fields",
        "Effect analysis",
        "Set context",
        "Possible card matches",
        "Answer breakdown",
        "Ambiguity notes",
        "Knowledge notes",
        "Possible follow-ups",
    )
    for heading in order:
        values = sections.get(heading) or []
        lines.extend(section_lines(heading, values))
    return "\n".join(lines)


def summarize_codex_context(understanding: dict) -> list[str]:
    references = understanding.get("references") or {}
    matches = understanding.get("matches") or []
    sections = understanding.get("sections") or {}
    lines = []
    if references.get("card_codes"):
        lines.append("Detected card codes: " + ", ".join(references["card_codes"]))
    if references.get("set_codes"):
        lines.append("Detected sets: " + ", ".join(references["set_codes"]))
    if references.get("variant", {}).get("signals"):
        lines.append("Detected variant hints: " + ", ".join(references["variant"]["signals"]))
    if matches:
        top = matches[0]
        lines.append(
            f"Best match: {top.canonical_code} {top.card_name} ({top.set_name or top.set_code})"
        )
    ambiguity = sections.get("Ambiguity notes") or []
    if ambiguity and ambiguity[0] != "No major ambiguity detected.":
        lines.append("Ambiguity remains explicit where card identity is not proven.")
    return lines or ["Use only the confirmed One Piece TCG facts resolved from the query."]


def build_local_codex_prompt(request_text: str, understanding: dict) -> str:
    requirement_units = [
        request_text.strip(),
        "Preserve explicit card-code and set-code matching when it is available.",
        "Keep variant markers such as alt art, SP, promo, manga, and reprint explicit instead of guessing.",
        "Do not weaken the local One Piece knowledge cache or source-priority rules.",
    ]
    if not understanding.get("references", {}).get("variant", {}).get("signals"):
        requirement_units = [item for item in requirement_units if "variant" not in item.lower()] + [
            "Keep base-card matching behavior stable."
        ]

    output_lines = [
        "Codex implementation prompt",
        "",
        "Goal",
        clean_codex_goal(request_text, request_text),
        "",
        "Context",
        format_prompt_units(split_prompt_units("\n".join(summarize_codex_context(understanding))), max_units=4, bullet=True),
        "",
        "Files likely involved",
        format_prompt_units(
            split_prompt_units("\n".join(infer_file_hints(request_text) or ["dashboard/app.py", "tools/miru_ai_onepiece.py", "tools/miru_ai.py"])),
            max_units=4,
            bullet=True,
        ),
        "",
        "Requirements",
        format_prompt_units(split_prompt_units("\n".join(requirement_units)), max_units=4, bullet=True),
        "",
        "Constraints",
        format_prompt_units(
            split_prompt_units(
                "\n".join(
                    [
                        "Keep dashboard runtime files authoritative unless the request explicitly changes them.",
                        "State uncertainty instead of inventing unsupported card, set, artist, or variant facts.",
                        "Keep the Flask sidecar architecture and local knowledge cache intact.",
                    ]
                )
            ),
            max_units=4,
            bullet=True,
        ),
        "",
        "Verification",
        format_prompt_units(
            split_prompt_units(
                "\n".join(
                    [
                        "Check at least one exact card-code case and one loose-name case.",
                        "Check at least one base-card match and one variant-sensitive case when variants matter.",
                        "Report any remaining ambiguity instead of masking it.",
                    ]
                )
            ),
            max_units=3,
            bullet=True,
        ),
    ]
    return "\n".join(output_lines)


def build_local_analysis_summary(understanding: dict) -> str:
    sections = understanding.get("sections") or {}
    lines = [
        "Card analysis",
        "- Prioritize the resolved card metadata and effect text before any downstream prompt-building.",
    ]
    analysis_items = sections.get("Effect analysis") or []
    if analysis_items and analysis_items[0] == "No card effect analysis was available for this query yet.":
        analysis_items = sections.get("Answer breakdown") or sections.get("Knowledge notes") or []
    for item in analysis_items[:4]:
        prefix = "" if item.startswith("-") else "- "
        lines.append(f"{prefix}{item}".rstrip())
    return "\n".join(lines)


def build_local_matching_summary(understanding: dict) -> str:
    matches = understanding.get("matches") or []
    lines = ["Matching recommendation"]
    if matches:
        top = matches[0]
        lines.append(
            f"- Best candidate: {top.canonical_code} | {top.card_name} | Confidence: {top.confidence} | Match: {top.reason}"
        )
        for match in matches[1:4]:
            lines.append(
                f"- Alternate candidate: {match.canonical_code} | {match.card_name} | Confidence: {match.confidence}"
            )
    else:
        lines.append("- No strong canonical match was resolved. Keep the query ambiguous instead of guessing.")
    return "\n".join(lines)


def build_local_development_brief(request_text: str, understanding: dict) -> str:
    output_lines = [
        "Miru development brief",
        "",
        "Objective",
        clean_codex_goal(request_text, request_text),
        "",
        "Relevant OPTCG context",
        format_prompt_units(split_prompt_units("\n".join(summarize_codex_context(understanding))), max_units=4, bullet=True),
        "",
        "Files likely involved",
        format_prompt_units(
            split_prompt_units("\n".join(infer_file_hints(request_text) or ["dashboard/app.py", "tools/miru_ai_onepiece.py", "tools/miru_ai.py"])),
            max_units=4,
            bullet=True,
        ),
        "",
        "Implementation notes",
        format_prompt_units(
            split_prompt_units(
                "\n".join(
                    [
                        "Keep the local One Piece knowledge cache as the first source of card identity context.",
                        "Prefer explicit card-code, set-code, and variant normalization over heuristic fallbacks.",
                        "Preserve uncertainty when multiple cards share a name or print family.",
                    ]
                )
            ),
            max_units=4,
            bullet=True,
        ),
        "",
        "Risks",
        format_prompt_units(
            split_prompt_units(
                "\n".join(
                    [
                        "Reprints, promos, and alternate prints can collapse onto the base card if print identity is under-modeled.",
                        "Hybrid set labels like OP14 and EB04 can collide if release-set aliases are not preserved.",
                    ]
                )
            ),
            max_units=3,
            bullet=True,
        ),
        "",
        "Verification",
        format_prompt_units(
            split_prompt_units(
                "\n".join(
                    [
                        "Check an exact card-code query.",
                        "Check a same-name ambiguity query.",
                        "Check at least one variant-sensitive file or prompt flow.",
                    ]
                )
            ),
            max_units=3,
            bullet=True,
        ),
    ]
    return "\n".join(output_lines)


def build_local_mode_output(command: str, request_text: str, knowledge) -> tuple[str, str]:
    understanding = knowledge.build_structured_understanding(request_text, mode=command)
    understanding_text = render_structured_understanding(understanding)

    if command == "knowledge":
        title = "Miru Ai card knowledge"
        text = understanding_text
    elif command == "analysis":
        title = "Miru Ai card analysis"
        text = understanding_text + "\n\n" + build_local_analysis_summary(understanding)
    elif command == "matching":
        title = "Miru Ai matching / identification"
        text = understanding_text + "\n\n" + build_local_matching_summary(understanding)
    elif command == "development":
        title = "Miru Ai development"
        text = understanding_text + "\n\n" + build_local_development_brief(request_text, understanding)
    else:
        title = "OP Miru Codex prompt"
        text = understanding_text + "\n\n" + build_local_codex_prompt(request_text, understanding)

    return title, text


def make_request(api_key, model, timeout, payload):
    body = {
        "model": model,
        "input": [
            {
                "role": "developer",
                "content": [
                    {
                        "type": "input_text",
                        "text": payload["instructions"],
                    }
                ],
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "input_text",
                        "text": payload["input"],
                    }
                ],
            },
        ],
        "max_output_tokens": payload["max_output_tokens"],
    }

    request = urllib.request.Request(
        API_URL,
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        details = exc.read().decode("utf-8", errors="replace")
        print(f"OpenAI API error ({exc.code}): {details}", file=sys.stderr)
        sys.exit(1)
    except urllib.error.URLError as exc:
        print(f"OpenAI API request failed: {exc}", file=sys.stderr)
        sys.exit(1)


def append_text(chunks, value):
    if isinstance(value, str):
        text = value.strip()
        if text:
            chunks.append(text)


def extract_text_like(value, chunks):
    if isinstance(value, str):
        append_text(chunks, value)
        return

    if isinstance(value, dict):
        append_text(chunks, value.get("output_text"))
        append_text(chunks, value.get("text"))
        append_text(chunks, value.get("value"))

        for key in ("content", "contents", "parts"):
            nested = value.get(key)
            if isinstance(nested, list):
                for item in nested:
                    extract_text_like(item, chunks)
        return

    if isinstance(value, list):
        for item in value:
            extract_text_like(item, chunks)


def collect_output_item_types(response_json):
    output_types = []
    seen = set()

    output = response_json.get("output")
    if not isinstance(output, list):
        return output_types

    for item in output:
        if not isinstance(item, dict):
            item_type = type(item).__name__
        else:
            item_type = item.get("type") or "unknown"

        if item_type not in seen:
            seen.add(item_type)
            output_types.append(item_type)

    return output_types


def unique_join(items):
    seen = set()
    ordered = []
    for item in items:
        if item not in seen:
            seen.add(item)
            ordered.append(item)
    return "\n\n".join(ordered)


def summarize_response_shape(response_json, output_types=None):
    keys = sorted(response_json.keys())
    parts = [f"keys={keys}"]

    output = response_json.get("output")
    if isinstance(output, list):
        parts.append(f"output_items={len(output)}")
        if output_types:
            parts.append(f"types={output_types}")
    else:
        parts.append(f"output={type(output).__name__}")

    return "Shape: " + " | ".join(parts)


def extract_output_text(response_json, model_name):
    chunks = []
    output_types = collect_output_item_types(response_json)

    extract_text_like(response_json.get("output_text"), chunks)
    append_text(chunks, response_json.get("text"))

    output = response_json.get("output")
    if isinstance(output, list):
        for item in output:
            if not isinstance(item, dict):
                extract_text_like(item, chunks)
                continue

            item_type = item.get("type")
            role = item.get("role")
            if item_type in {"message", "output_message"} or role == "assistant":
                extract_text_like(item.get("content"), chunks)
                extract_text_like(item.get("message"), chunks)
                append_text(chunks, item.get("output_text"))
                append_text(chunks, item.get("text"))
                continue

            extract_text_like(item.get("content"), chunks)
            extract_text_like(item.get("message"), chunks)
            append_text(chunks, item.get("output_text"))
            append_text(chunks, item.get("text"))

    if chunks:
        return unique_join(chunks)

    if "reasoning" in output_types:
        output_types_text = ", ".join(output_types) if output_types else "unknown"
        return (
            "API returned reasoning without a final text answer. "
            f"Model: {model_name}. "
            f"Output item types: {output_types_text}. "
            f"{summarize_response_shape(response_json, output_types)}"
        )

    return "No assistant text found. " + summarize_response_shape(
        response_json, output_types
    )


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

    parser = build_parser()
    args = parser.parse_args()
    knowledge = load_onepiece_knowledge()

    if args.command in {"knowledge", "analysis", "matching", "codex-prompt", "development"}:
        title, text = build_local_mode_output(args.command, args.prompt, knowledge)
        print(title)
        print("=" * len(title))
        print(text)
        return

    api_key = require_api_key()

    if args.command == "review":
        resolved_path, content = read_target_file(args.target)
        payload = build_review_prompt(resolved_path, content, knowledge)
        title = f"OP Miru review: {resolved_path}"
    elif args.command == "plan":
        domain_context = knowledge.build_prompt_context(args.prompt, mode="plan")["text"]
        payload = build_plan_prompt(args.prompt, domain_context)
        title = "OP Miru implementation plan"
    elif args.command == "codex-prompt":
        domain_context = knowledge.build_prompt_context(args.prompt, mode="codex prompt")["text"]
        payload = build_codex_prompt(args.prompt, domain_context)
        title = "OP Miru Codex prompt"
    else:
        domain_context = knowledge.build_prompt_context(args.prompt, mode="debug")["text"]
        payload = build_debug_prompt(args.prompt, domain_context)
        title = "OP Miru debug checklist"

    response_json = make_request(api_key, args.model, args.timeout, payload)
    text = extract_output_text(response_json, args.model)
    if args.command == "codex-prompt" and should_format_codex_prompt_text(text):
        text = format_codex_prompt_output(text, args.prompt, domain_context)

    print(title)
    print("=" * len(title))
    print(text)


if __name__ == "__main__":
    main()
