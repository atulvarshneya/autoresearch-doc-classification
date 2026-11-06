# Goal
Optimize the system prompt in `prompt.txt` to achieve the highest possible classification quality across the provided list of classes, measured as macro-F1 on a held-out set of documents.

# System Constraints
- You are ONLY allowed to modify `prompt.txt`.
- You may read `prompt.txt`, `ledger.txt`, and the run detail files in `runs/`. Do not read the documents in `dataset/`.
- Do NOT modify `evaluate.py`, `program.md`, `ledger.txt`, or anything in `runs/`.
- You must review `ledger.txt` before every attempt to see what strategies failed or succeeded.

# How scoring works
- The labeled documents are split into a train set and a held-out set. You only ever see per-document results for the train set.
- A prompt is kept only if it raises the held-out macro-F1 above the best so far; otherwise `evaluate.py` reverts `prompt.txt` itself.
- Because the held-out documents are unseen, improve the prompt's description of each document type in general terms. Do not mention specific files, tenants, property names, amounts, or dates from the train errors: that overfits to the train set and will not raise the held-out score.

# Instructions for the Iteration Loop
1. Run `python evaluate.py` in the terminal to test the current prompt.
2. Read the newest line of `ledger.txt`: held-out macro-F1, the decision (kept/reverted), the most frequent train confusions, the weakest train classes, regressions/fixes compared with the best run, and the diff of the prompt that was tested.
3. Open the detail file named in that line (`runs/<run_id>.json`) for the misclassified train documents and the model's rationale for each mistake.
4. Review earlier ledger lines to avoid repeating changes (shown in their prompt diffs) that already failed.
5. Formulate a new hypothesis based on the errors, edit `prompt.txt`, and repeat.
