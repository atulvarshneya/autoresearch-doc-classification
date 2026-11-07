# autoresearch: automatic prompt optimization for document classification

Use this repo to get a better system prompt for an LLM document classifier without tuning it by hand.

You do three things:
1. Define your document classes.
2. Label a set of example documents.
3. Let Claude Code improve the prompt in a loop.

Each iteration of the loop works like this:

1. **Claude Code edits `prompt.txt`.** It first reads the results of earlier runs: what was misclassified, and why.
2. **`evaluate.py` scores the new prompt.** It classifies every labeled document with Claude Haiku 5.5 and computes the macro-F1 (the F1 score averaged over all classes).
3. **`evaluate.py` decides what to keep.** If the score on the *held-out* documents beats the best so far, it commits `prompt.txt` to git. Otherwise it reverts the file.

The labeled documents are split into a **train** set and a **held-out** set. The agent sees per-document results only for the train set, and a change is kept only if it raises the held-out score. This guards against a prompt that memorizes the train documents rather than describing each class well.

The design targets roughly 100–200 documents, which can be many pages long, and 30–40 classes.

## What's in the repo

| File | Purpose |
|---|---|
| `labeler.py`, `labeler.html` | Local web app for creating classes and labeling documents |
| `prompt_template.txt` | The starting prompt, with placeholders for your classes |
| `INITIALIZE.py` | Builds `prompt.txt` from the template and your classes |
| `evaluate.py` | Scores `prompt.txt`, records the run, and keeps or reverts the prompt |
| `program.md` | The rules the optimizing agent follows |
| `run_loop.sh` | Runs the loop unattended |
| `ClaudeCodeInstructions.txt` | Instructions to give an interactive Claude Code session |
| `dataset/` | Your documents (`files/`), labels (`labels.json`) and classes (`classes.json`) |

## Requirements

- Python 3 with `pip install anthropic python-dotenv markdown`
- An Anthropic API key
- [Claude Code](https://claude.com/claude-code) installed (the `claude` command), to run the loop
- git

## Start each optimization project with a fresh clone

Each optimization project (one set of documents and one set of classes) should start from its own fresh clone:

```bash
git clone https://github.com/atulvarshneya/autoresearch-doc-classification.git my-classifier
cd my-classifier
```

The loop commits each improved `prompt.txt` into the clone's git history, and the run records hold results for one specific dataset. A fresh clone keeps each project's prompt history and results separate, and leaves the template untouched for the next project.

## Setup

1. **Add your API key.** Copy `env.example` to `.env` and fill in `ANTHROPIC_API_KEY`. `.env` is gitignored, so the key is never committed.

2. **Add your documents.** Put them in `dataset/files/` as text or markdown files. The LLM sees each file's text exactly as it is.

3. **Create your classes.** Run `python labeler.py` and open http://127.0.0.1:8765. Add each class with a name and a hint. The hint describes what documents of that class look like, and it goes into the starting prompt, so it is worth writing carefully. A catch-all class such as `other` helps with documents that fit no class.

4. **Generate the starting prompt:**
   ```bash
   python INITIALIZE.py
   ```
   This creates `prompt.txt` from your classes and commits it.

5. **Label your documents** in the labeler. Every selection is saved immediately, so you can stop and come back at any time. Aim for several documents per class.

To use the labeler from another machine, start it with `python labeler.py --host 0.0.0.0`. The labeler has no login, so do this only on a trusted network.

**The class set is fixed once `prompt.txt` exists.** The loop reshapes the prompt freely, so classes can't be added into it later. If you add, remove or rename a class, start over with `python INITIALIZE.py --force`. This archives the old results to `archive/` and builds a new starting prompt. Labeling more documents never requires starting over.

## Running the optimization loop

### Option 1: unattended, with `run_loop.sh`

```bash
./run_loop.sh 20
```

This scores the starting prompt if there are no results yet. Then it runs 20 iterations, each in a new, non-interactive Claude Code session that may only read files, edit `prompt.txt` and run `python evaluate.py`.

It stops early in three cases:
- a session fails
- two iterations in a row don't run the evaluator
- the held-out score reaches 1.0, so no change could ever be kept

To choose the agent's model, run `MODEL=<model> ./run_loop.sh 20`.

On your first use, try `./run_loop.sh 1` and check that the agent's edit went through. If the script warns that `prompt.txt` was unchanged, Claude Code may have blocked the edit; rerun with `EDIT_RULE=Edit ./run_loop.sh 20`.

### Option 2: interactive, in a Claude Code session

Open Claude Code in the repo folder and tell it:

```
Execute the instructions given in @ClaudeCodeInstructions.txt
```

It runs 5 iterations, then summarizes each change, its score, and whether it was kept. You can watch what it does, and it can decide to stop early, for example when there is nothing left to improve.

### Results

- **`prompt.txt`:** always holds the best prompt so far. Its git history shows each improvement.
- **`ledger.txt`:** one line per run, with:
  - the train and held-out scores
  - whether the prompt was kept or reverted
  - the most frequent confusions between classes
  - the change that was tried
- **`runs/<run_id>.json`:** the full details of each run, including every train document's result.

These records stay local and are gitignored.

## Git and pushing

`evaluate.py` and `INITIALIZE.py` commit to your local clone only, and nothing in this repo ever pushes.

- **Users without write access to this GitHub repo** can't push to it anyway, because GitHub refuses the push.
- **Users with write access** are protected too. When `INITIALIZE.py` creates the starting prompt, it disables pushing from that clone (`git remote set-url --push origin DISABLED`). An optimized prompt therefore can't be pushed to this repo by accident. `git pull` still works.

To push your project to a repository of your own, run `git remote set-url --push origin <your-repo-url>`.

`dataset/files/`, `.env`, `ledger.txt`, `runs/` and `archive/` are gitignored, so your documents and results stay on your machine even if you do push. `labels.json` and `classes.json` are tracked, and they contain your document filenames and class names.
