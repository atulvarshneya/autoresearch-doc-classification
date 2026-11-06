# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repo is

An "autoresearch" loop for optimizing an LLM classification prompt. An agent repeatedly edits `prompt.txt`, runs `evaluate.py` to score it against labeled documents, and the evaluator itself decides whether to keep the change (git commit) or throw it away (git checkout).

`program.md` is the instruction file for the agent running that loop. Its rules apply whenever you act as the optimizing agent:
- Read and modify **only** `prompt.txt`. Do not edit `evaluate.py` or `program.md`.
- Review `ledger.txt` before every attempt to see which strategies worked or failed.
- Loop: run `python evaluate.py` → read the console output and `ledger.txt` → form a hypothesis from the errors → edit `prompt.txt` → repeat.

## Commands

```bash
python evaluate.py   # score prompt.txt, append to ledger.txt, then auto-commit or auto-revert prompt.txt
```

Dependencies (there is no manifest): `pip install anthropic python-dotenv`. There is no build, lint, or test suite.

## How evaluate.py works

- It reads `prompt.txt` as the system prompt and calls `call_llm(prompt, text)` for each labeled sample. A prediction counts as correct only if it matches the label exactly after `.strip().lower()`.
- It appends `Score: XX.XX% | Errors Sampled: N` (plus up to 3 sample errors) to `ledger.txt`. It takes the best previous score by parsing every `Score:` line in that file.
- **It runs git commands itself.** If accuracy beats the best score in the ledger, it runs `git add prompt.txt && git commit`. Otherwise it runs `git checkout HEAD -- prompt.txt`, which discards any uncommitted edits to `prompt.txt`. Commit or stash `prompt.txt` changes you want to keep before you run it.
- `load_dataset` reads `dataset/labels.json` (`{"labeled_data": [{"file", "label"}]}`) and loads each document's text from `dataset/files/`.
- `call_llm` calls `claude-haiku-5-5` (effort `low`, 60s timeout, 2 SDK retries) via the `anthropic` SDK. `ANTHROPIC_API_KEY` comes from `.env`, which is loaded with `python-dotenv` and gitignored. API errors and refusals come back as `ERROR: ...` and are scored wrong. A missing API key stops the script before anything is written to the ledger.
- The reply format is enforced by the API (structured outputs, `RESPONSE_SCHEMA` in `evaluate.py`): always JSON with `classname` (string) and `rationale` (list of strings), and `call_llm` returns the `classname`. Formatting can't cost points, so the prompt's wording only affects which class is chosen. `classname` is deliberately not restricted to an enum, so **the list of valid classes lives only in `prompt.txt`**. A misspelled or invented class name scores wrong.

## Dataset

`dataset/files/` holds 15 synthetic commercial real-estate / lending documents in markdown. Only 5 are labeled in `dataset/labels.json`. The rest include document types outside the 5 target classes (tax return, audited and company-prepared financials, debt schedule, covenant compliance certificate) and several unlabeled lease examples. Those could serve as distractors or as extra labeled samples.
