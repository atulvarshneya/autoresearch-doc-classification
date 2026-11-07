# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repo is

An "autoresearch" loop for optimizing an LLM classification prompt. An agent repeatedly edits `prompt.txt`, runs `evaluate.py` to score it against labeled documents, and the evaluator itself decides whether to keep the change (git commit) or throw it away (git checkout).

`program.md` is the instruction file for the agent running that loop. When you act as the optimizing agent, follow it: modify only `prompt.txt`; read only `prompt.txt`, `ledger.txt` and `runs/*.json` (never the documents in `dataset/`); never add, remove or rename classes; and don't write rules that name specific files, tenants, amounts or dates.

The repo ships as a template: `dataset/files/` is empty, kept in git by `.gitkeep`; `labels.json` and `classes.json` have no entries; and there is no `prompt.txt`.

Setup order:
1. Create the classes, each with a hint, in the labeler.
2. Run `python INITIALIZE.py` to generate and commit `prompt.txt`.
3. Label documents.
4. Run `evaluate.py`, then the loop.

The real use is ~100–200 multi-page documents and 30–40 classes, so design decisions (ledger size, split, metrics) should target that scale. `evaluate.py` exits before doing anything until at least 2 files are labeled.

## Commands

```bash
python evaluate.py   # score prompt.txt, write ledger.txt + runs/<run_id>.json, then auto-commit or auto-revert prompt.txt
python labeler.py    # labeling web app at http://127.0.0.1:8765 (--port to change; --host 0.0.0.0 to allow other machines, no auth)
python INITIALIZE.py # create prompt.txt from prompt_template.txt + dataset/classes.json (refuses if prompt.txt exists)
python INITIALIZE.py --force   # restart after changing the class set: archive ledger/runs, regenerate + commit prompt.txt
./run_loop.sh 20     # unattended loop: baseline if needed, then one fresh `claude -p` session per iteration
```

**Running the optimization loop:**
- **Unattended:** `run_loop.sh` runs a baseline `evaluate.py` if the ledger is empty, then one non-interactive Claude Code session per iteration. It allows only `Read`, `Edit(prompt.txt)` and `Bash(python evaluate.py)`.
- **Safety checks:** it stops if a session errors or if two iterations in a row don't run the evaluator. It warns if an iteration left `prompt.txt` unchanged, which can mean the edit rule blocked the agent; `EDIT_RULE=Edit` loosens it.
- **Model:** `MODEL=` picks the agent's model.
- **Working folder:** it `cd`s to its own folder, so it is safe to call from anywhere.
- **Interactive:** paste `ClaudeCodeInstructions.txt` into a Claude Code session opened in the repo. It defers to `program.md` and runs 5 iterations.

## Starting prompt and the class set

- **Template:** `prompt_template.txt` has the placeholders `{{CLASS_COUNT}}`, `{{CLASS_LIST}}`, `{{CLASS_HINTS}}` and `{{EXAMPLE_CLASS}}`.
- **What `INITIALIZE.py` does:** it validates `classes.json` (at least 2 classes, unique names, non-empty hints), renders the template into `prompt.txt`, and writes `prompt_classes.json`, the class set the prompt was built for. It then commits only those two files.
- **Generated once:** `prompt.txt` is created a single time, because the loop then reshapes it unpredictably. Nothing ever re-injects classes into an existing prompt.
- **Changing classes = restart:** adding, removing or renaming a class means running `INITIALIZE.py --force`. That moves `ledger.txt` and `runs/` to `archive/<timestamp>/` and regenerates and commits the prompt. Committing matters: otherwise a 0-score first run would make `evaluate.py` restore the old prompt from git.
- **The check in evaluate.py:** before any API call, `check_setup()` stops if any of these hold: `prompt.txt` or `prompt_classes.json` is missing; the class names in `classes.json` differ from `prompt_classes.json`; or a label in `labels.json` is outside that set. It compares files, never prompt text.
- **Labeling more files** needs no re-initialization.

Dependencies (there is no manifest): `pip install anthropic python-dotenv markdown`. There is no build, lint, or test suite.

## How evaluate.py works

- **Data:** `load_dataset` reads `dataset/labels.json` (`{"labeled_data": [{"file", "label"}]}`) and loads each document's text from `dataset/files/`.
- **Train/held-out split:** each file's side is fixed by a hash of `SPLIT_SALT` + filename, with `HOLDOUT_FRACTION` of files held out. Files never switch sides as the dataset grows. The agent sees per-file results only for train files. Held-out per-file results are never written anywhere.
- **LLM call:** `call_llm` calls `claude-haiku-5-5` (effort `low`, 60s timeout, 2 SDK retries). `ANTHROPIC_API_KEY` comes from `.env`, which is gitignored. Structured outputs (`RESPONSE_SCHEMA`) force a JSON reply with `classname` (string) and `rationale` (list of strings). `classname` is deliberately not an enum, so **the classes the LLM chooses from are defined only in `prompt.txt`**. `evaluate.py` reads `classes.json` only to compare class names in `check_setup()`. API errors and refusals become `ERROR: ...` and are scored as one `<error>` label. A missing API key stops the script before anything is written.
- **Metrics:** accuracy and macro-F1 per split. Macro-F1 averages over every label seen as expected *or* predicted, so an invented class name adds a class with F1 = 0.
- **Keep/revert:** decided by **held-out macro-F1**, which must beat the best previous run by more than `MIN_IMPROVEMENT`. Only runs with the same `fingerprint` count: a hash of the dataset (files, labels, text), model, effort, schema and split settings. Changing any of these starts a fresh best score.
- **It runs git commands itself.** On improvement it runs `git add prompt.txt && git commit -- prompt.txt`; the pathspec means nothing else that is staged gets committed. Otherwise it runs `git checkout HEAD -- prompt.txt`, which discards any uncommitted edits to `prompt.txt`. Commit `prompt.txt` changes you want to keep before running it.

## Ledger: two layers

- `ledger.txt`: one compact JSON line per run, sized so the agent can read all of it. It holds held-out and train scores, the decision, the best score before the run, the top train confusions, the weakest train classes, regressions/fixes against the best run's train results, the prompt diff against the best prompt (truncated at `MAX_DIFF_CHARS`), and the path of the detail file. Non-JSON lines (old format) are ignored.
- `runs/<run_id>.json`: full prompt text, per-class train metrics, and every train file's outcome. The rationale is included only for errors. Regressions/fixes are computed by loading the best run's detail file, so don't delete detail files of kept runs.

Both are gitignored, along with `archive/`. `prompt.txt` is the only file `evaluate.py` touches in git.

## Labeling tool

`labeler.py` (stdlib `http.server`, bound to 127.0.0.1) serves `labeler.html`. It lists every file in `dataset/files/` and offers the classes from `dataset/classes.json` as radio buttons.
- **Autosave:** every selection saves `dataset/labels.json` immediately via an atomic temp-file-and-rename write. It keeps the file's one-entry-per-line layout, so each label change is a one-line git diff. While a save is in flight, the page locks navigation (buttons and arrow keys), the radios, the skip switch and Add class (`busy` / `withLock` in `labeler.html`). Saves time out after 15s, so the page can't stay locked.
- **Class list:** `dataset/classes.json` holds `{"classes": [{"name", "hint"}]}`. Any other top-level key, such as `_note`, is ignored and preserved. The hint is the description an LLM uses to recognize the class; `INITIALIZE.py` copies the hints into the starting `prompt.txt`. The UI can add a class, and a non-empty hint is required, enforced by both the server and the page. Hints are shown as tooltips on the radio buttons. Removing or renaming a class is a hand edit. Labels used in `labels.json` but missing from `classes.json` are still shown, flagged.
- **Rendering:** documents are rendered as markdown with raw HTML neutralized (`<` escaped first). A Raw view shows exactly the text the LLM sees.
- **Layout:** the document panel defaults to letter-page (8.5×11) proportions. A drag handle (`#splitter`) lets the user resize it, within 360px for the document and 300px for the label panel. The width persists in `localStorage`, and double-clicking the handle resets it.
- **Path safety:** `/api/file` only serves names that exactly match the directory listing.
- **Effect on evaluate.py:** any label change alters its dataset fingerprint, so the next run starts a fresh best score; this is expected.
- **Class changes after INITIALIZE:** adding a class in the labeler after `INITIALIZE.py` makes `evaluate.py` refuse to run until you restart with `INITIALIZE.py --force`.
