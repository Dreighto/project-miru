# Overlay — adopted-lessons

```
Overlay: adopted-lessons
Architecture: MIRU-INSTRUCTIONS-v2
Load when: doing a non-trivial code change (more than typo or lint).
Last reviewed: 2026-05-08
```

Lessons promoted from Provisional to Adopted via the Lesson Promotion
Discipline (Notion canon, 2026-04-28). These are battle-tested patterns that
prevent specific failure modes we've already hit.

---

## Test the JS as it lives in the workflow JSON (PRO-189 retro, adopted 2026-04-28)

When testing JavaScript embedded in workflow JSON files (e.g. `docker/n8n/workflows/*.json`), the test MUST:

1. Load the JSON file from disk via `fs.readFileSync` and `JSON.parse`.
2. Extract the `jsCode` string from the relevant node.
3. Eval it as JS via `new Function(jsCode)` or `vm.Script(jsCode)` to confirm it parses without `SyntaxError`.
4. Exercise the algorithm against that loaded code path — NOT a clean extracted copy of the algorithm.

**Why this is a hard rule:** PRO-160 shipped with two latent bugs (SyntaxError from a literal newline inside a string literal, and a missing `$getWorkflowStaticData('global')` call). PRO-160's tests passed because they imported a clean copy of the diff function and exercised it directly. The deploy-time mangling and the embedded-newline bug both happened at the boundary between "JS source in the JSON file" and "JS that n8n actually runs," and the tests were structurally unable to see across that boundary. The watcher crashed on every poll for 12 minutes in production before being deactivated.

PRO-189 added the boundary-crossing test, which catches both bug classes and any future deploy-pipeline mangling.

**Applies to:** any change to a workflow JSON file under `docker/n8n/workflows/` that touches a `jsCode` field.

---

## Lock design in the Linear ticket description, not in the prompt wrapper (PRO-180 retro, adopted 2026-04-28)

When dispatching a non-trivial worker task, the design specification belongs in the Linear ticket description. The prompt wrapper handles execution mechanics (model, reasoning level, pre-flight, completion contract) and points back at the ticket for the design.

**What goes in the Linear ticket:**

- Schema, rules, scope.
- Don't-touch list.
- Done-when criteria.
- Provisional flag and promotion criteria if applicable.
- Investigation steps if the bug isn't fully understood yet.

**What stays in the prompt wrapper:**

- Worker selection (model, reasoning level).
- Pre-flight checks (branch hygiene, working tree state).
- Completion contract format.
- Escalation rules.
- Post-merge cleanup steps.

**Why this is a hard rule:** the design survives if the worker session restarts mid-task or if anyone else picks up the ticket later. The prompt wrapper does not — it's ephemeral. Putting the design in the ticket also makes ticket-only dispatch viable (operator taps Telegram dispatch button without Claude Chat drafting an elaborated prompt first), which is critical for autonomy.

PRO-180 shipped cleanly via ticket-only dispatch in 3 minutes. The Linear ticket description carried the full design; CC executed three coordinated edits across three files without needing my prompt wrapper.

**Applies to:** any worker dispatch that's more than a one-line change. Trivial fixes (typos, lint) don't need a locked design.
