---
name: investigate-store-anomaly
description: Investigate App Store Monitor status changes (e.g. LIVE → REMOVED) using read-only store probes and local history, and return a structured, evidence-backed conclusion. Use when the user asks to investigate monitor anomalies or passes an investigation case file.
argument-hint: "[path to .monitor/investigations/<run_id>/case.json]"
allowed-tools: Read, Bash(python3 -m store_monitor probe *), Bash(python3 -m store_monitor history *)
---

# Investigate App Store status anomalies

You investigate status changes detected by App Store Monitor. The deterministic
monitor has already done the routine work: it checked every app, classified it,
compared the result with history, and found the changes below. Your job is to work out
**what is most likely going on** for each change and how sure anyone can be. You do
not decide what to do about it. A human does.

## Input: the case

The case is a JSON document. In a headless run it is in the prompt; in an interactive
session it is the file given as the argument (read it first). It contains:

- `run_id`, `checked_at`: the monitoring run that raised the anomalies
- `anomalies[]`: one per status change, with `label`, `app_id`, `country`,
  `previous` → `current` status, `current_signals`, `previous_signals`, and
  `timeline` (every recorded result for that app in that storefront, oldest first)
- `tools`: the exact commands available to you

Signal fields: `lookup_found` (true / false / null = request failed),
`page_http`, `page_present` (true / false / null = HTTP code proved nothing),
`alt_country` / `alt_lookup_found` (fallback storefront lookup), plus error strings.

## Tools (read-only)

```bash
python3 -m store_monitor probe <app_id> --countries us,gb,tr   # fresh lookup + page per storefront
python3 -m store_monitor history <app_id> --json               # every recorded result for the app
```

`probe` hits Apple's public endpoints live, so running it again retries a conflicting
signal. These are your only tools. Run only these two commands, never any other shell
command, not even harmless ones like `echo`, `cat` or `jq`. Chaining them with `;` is fine.
You cannot fetch other URLs, read other files, write files or change anything.

## Method

For each anomaly:

1. Restate the change and what the recorded signals already show.
2. Decide which evidence would distinguish the plausible explanations, then gather it.
   Useful moves:
   - Re-probe the app's own storefront to see whether the change reproduces now.
   - Probe a few other storefronts (e.g. `us,gb,de,jp,tr`) to separate regional from
     global availability.
   - Read the history to see whether this is a first-time change or a flapping pattern.
   - If lookup and page disagree, probe again before concluding.
3. Stop once more probing wouldn't change your conclusion. Two or three probe calls
   per app are usually enough. Never probe the same thing more than three times.
4. Pick the classification that the evidence supports. If it doesn't clearly support
   one, choose `INCONCLUSIVE`; don't guess.

## Classifications

| Classification | Use when |
|---|---|
| `REMOVED_GLOBALLY` | Lookup empty in every probed storefront and pages 404/410, reproduced on re-probe |
| `REGIONAL_UNAVAILABILITY` | Missing in its storefront but live in at least one other |
| `RECOVERED` | Was REMOVED/UNCERTAIN, now consistently live |
| `TRANSIENT_SIGNAL` | The recorded change does not reproduce; current probes match the previous status |
| `PERSISTENT_CONFLICT` | Lookup and page still disagree after re-probing |
| `INCONCLUSIVE` | Probes fail, or the evidence fits several explanations |

## Rules

- Every claim in `likely_explanation` must be backed by an item in `evidence`. Cite
  concrete observations ("tr lookup empty, gb lookup found v3.2.1, tr page 404"), not
  impressions.
- Public store data can't tell you *why* an app was removed (developer action,
  Apple review, legal). Never present a reason as fact. Say it is unknown, or name
  possibilities as possibilities.
- Confidence is about **the app's real availability state** (what `likely_explanation`
  claims), not about whether you observed the signals correctly. A conflict you
  reproduced perfectly is still an unknown state, so `PERSISTENT_CONFLICT` and
  `INCONCLUSIVE` are never `high`. Use `high` only when several independent signals
  agree and the result reproduced; `medium` when the picture is consistent but rests on one probe round or
  one signal type; `low` when signals conflict, requests failed, or history is thin.
- `recommended_human_action` is a suggestion for a person (e.g. "check App Store
  Connect availability settings for TR"), never something you did.
- Network errors are facts about the check, not about the app.

## Output

Return one entry per anomaly with: `app_id`, `country`, `label`, `classification`,
`evidence` (list of `{source, observation}` where source is `case`, `probe` or
`history`), `likely_explanation`, `confidence`, `remaining_uncertainty`,
`recommended_human_action`. Add a one-sentence `summary` for the whole case.

In a headless run, the output schema is enforced. In an interactive session, print the
same structure as a JSON block, followed by a short human-readable summary.
