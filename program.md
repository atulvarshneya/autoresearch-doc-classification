# Goal
Optimize the system prompt in `prompt.txt` to achieve the highest possible classification accuracy across the provided list of classes.

# System Constraints
- You are ONLY allowed to read and modify `prompt.txt`.
- Do NOT modify `evaluate.py` or `program.md`.
- You must review `ledger.txt` before every attempt to see what strategies failed or succeeded.

# Instructions for the Iteration Loop
1. Run `python evaluate.py` in the terminal to test the current prompt.
2. Read the console output and the updated `ledger.txt` to see the accuracy score.
3. If the score improved, the new prompt is committed. If it got worse, revert `prompt.txt` using Git.
4. Formulate a new hypothesis based on the errors, edit `prompt.txt`, and repeat.
