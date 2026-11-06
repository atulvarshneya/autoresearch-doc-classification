# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repo is

An "autoresearch" loop for optimizing an LLM classification prompt. An agent repeatedly edits `prompt.txt`, runs `evaluate.py` to score it against labeled documents, and the evaluator itself decides whether to keep the change (git commit) or throw it away (git checkout).

`program.md` is the instruction file for the agent running that loop. When you act as the optimizing agent, follow it: modify only `prompt.txt`; read only `prompt.txt`, `ledger.txt` and `runs/*.json` (never the documents in `dataset/`); and don't write rules that name specific files, tenants, amounts or dates.

The current dataset (10 labeled files, 6 classes including a catch-all `other` for documents that fit no class) is a toy for developing the framework. The real use is ~100–200 multi-page documents and 30–40 classes, so design decisions (ledger size, split, metrics) should target that scale.

## Commands

```bash
python evaluate.py   # score prompt.txt, write ledger.txt + runs/<run_id>.json, then auto-commit or auto-revert prompt.txt
```

Dependencies (there is no manifest): `pip install anthropic python-dotenv`. There is no build, lint, or test suite.

## How evaluate.py works

- **Data:** `load_dataset` reads `dataset/labels.json` (`{"labeled_data": [{"file", "label"}]}`) and loads each document's text from `dataset/files/`.
- **Train/held-out split:** each file's side is fixed by a hash of `SPLIT_SALT` + filename, with `HOLDOUT_FRACTION` of files held out. Files never switch sides as the dataset grows. The agent sees per-file results only for train files. Held-out per-file results are never written anywhere.
- **LLM call:** `call_llm` calls `claude-haiku-5-5` (effort `low`, 60s timeout, 2 SDK retries). `ANTHROPIC_API_KEY` comes from `.env`, which is gitignored. Structured outputs (`RESPONSE_SCHEMA`) force a JSON reply with `classname` (string) and `rationale` (list of strings). `classname` is deliberately not an enum, so **the list of valid classes lives only in `prompt.txt`**. API errors and refusals become `ERROR: ...` and are scored as one `<error>` label. A missing API key stops the script before anything is written.
- **Metrics:** accuracy and macro-F1 per split. Macro-F1 averages over every label seen as expected *or* predicted, so an invented class name adds a class with F1 = 0.
- **Keep/revert:** decided by **held-out macro-F1**, which must beat the best previous run by more than `MIN_IMPROVEMENT`. Only runs with the same `fingerprint` count: a hash of the dataset (files, labels, text), model, effort, schema and split settings. Changing any of these starts a fresh best score.
- **It runs git commands itself.** On improvement it runs `git add prompt.txt && git commit`. Otherwise it runs `git checkout HEAD -- prompt.txt`, which discards any uncommitted edits to `prompt.txt`. Commit `prompt.txt` changes you want to keep before running it.

## Ledger: two layers

- `ledger.txt`: one compact JSON line per run, sized so the agent can read all of it. It holds held-out and train scores, the decision, the best score before the run, the top train confusions, the weakest train classes, regressions/fixes against the best run's train results, the prompt diff against the best prompt (truncated at `MAX_DIFF_CHARS`), and the path of the detail file. Non-JSON lines (old format) are ignored.
- `runs/<run_id>.json`: full prompt text, per-class train metrics, and every train file's outcome. The rationale is included only for errors. Regressions/fixes are computed by loading the best run's detail file, so don't delete detail files of kept runs.

Both are untracked; `prompt.txt` is the only file `evaluate.py` touches in git.
