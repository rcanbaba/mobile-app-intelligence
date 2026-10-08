# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

Public portfolio repo: hybrid deterministic + agentic tooling for public mobile-store intelligence. The only component so far is **App Store Monitor** (`store_monitor/`). README.md is the design document. Keep it truthful: mark unbuilt parts 🚧 and update the milestone table when a part ships.

## Commands

Python 3.10+, standard library only (no runtime or test dependencies).

```bash
python3 -m store_monitor check examples/apps.example.txt   # run against public examples
python3 -m unittest                                        # all tests (offline)
python3 -m unittest tests.test_checker.ClassificationTest.test_globally_removed_app  # one test
```

## Architecture

- `store_client.py` is the **only** module that does network I/O (`StoreClient`, with an injectable `opener` and `sleep`). Retries cover network errors, 429 and 5xx. The page signal is tri-state: only 404/410 mean "gone", other failures are `None` (unknown).
- `checker.py` maps the two signals (lookup + page, plus one fallback-storefront lookup) to `Status`. It records raw signals in `CheckResult.signals` for history and investigations.
- `history.py` stores one JSON file per run in `.monitor/runs/` (run ID = UTC timestamp, so names sort by time). `build_baselines` finds each app's last known non-ERROR result, keyed by `app_id@country`. `changes.py` compares the current run with those baselines (NEW / CHECK_FAILED / STATUS_CHANGE). Only STATUS_CHANGE counts as an anomaly worth investigating.
- `investigation.py` runs only after human approval (the `[y/N]` prompt in `check`, or the `investigate` command). It builds a case (one run's anomalies plus their timelines), then runs headless `claude -p` with `--tools Bash`, an allowlist of `store_monitor probe`/`history`, `--setting-sources project`, `--strict-mcp-config`, a budget cap, and `--json-schema` (`CONCLUSION_SCHEMA`). It re-validates the output in `validate_conclusion` and writes `case.json`, `trace.jsonl` and `conclusion.json` to `.monitor/investigations/<run_id>/`.
- The agent's instructions live only in `.claude/skills/investigate-store-anomaly/SKILL.md`. The headless run appends that file's body as the system prompt. Keep its classification list in sync with `CLASSIFICATIONS` (a test enforces this).
- `probe.py` returns raw per-storefront signals (no verdict). It is the agent's main tool and validates its arguments, because they come from the model.
- `models.py` holds the shared contracts. `parsing.py` handles input lines. `report.py` only presents results. `cli.py` is the argparse entry point (`check`, `history`, `probe`, `investigate`).
- Tests use `tests/fakes.py` (`FakeClient` for checker scenarios, `ScriptedOpener` for HTTP behavior). Never hit the network in tests.

## Design rules (from the project brief)

- Facts stay deterministic. The LLM/agent is used only to investigate ambiguous changes, and only after the user approves (human in the loop). The agent is read-only and works through the project's own CLI probe commands, not arbitrary web access.
- Don't add backends, databases, queues, MCP servers, multi-agent setups or frameworks unless there's a demonstrated need.
- Privacy: the repo is public. Real app lists (`apps.txt`), CSVs, `.monitor/` state and reports are git-ignored. Only well-known public apps go in `examples/`. Check `git status` for private data before committing.
- Git: commit as `rcanbaba`. Work on feature branches with PRs, and ask the user before committing, pushing or opening PRs.
