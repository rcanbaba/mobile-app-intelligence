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
- `models.py` holds the shared contracts. `parsing.py` handles input lines. `report.py` only presents results. `cli.py` is the argparse entry point (`python3 -m store_monitor <command>`).
- Tests use `tests/fakes.py` (`FakeClient` for checker scenarios, `ScriptedOpener` for HTTP behavior). Never hit the network in tests.

## Design rules (from the project brief)

- Facts stay deterministic. The LLM/agent is used only to investigate ambiguous changes, and only after the user approves (human in the loop). The agent is read-only and works through the project's own CLI probe commands, not arbitrary web access.
- Don't add backends, databases, queues, MCP servers, multi-agent setups or frameworks unless there's a demonstrated need.
- Privacy: the repo is public. Real app lists (`apps.txt`), CSVs, `.monitor/` state and reports are git-ignored. Only well-known public apps go in `examples/`. Check `git status` for private data before committing.
- Git: commit as `rcanbaba`. Work on feature branches with PRs, and ask the user before committing, pushing or opening PRs.
