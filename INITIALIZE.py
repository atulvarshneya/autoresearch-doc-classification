"""Create the starting prompt.txt from prompt_template.txt and dataset/classes.json.

Run once, after creating your classes (with hints) in the labeler:  python INITIALIZE.py
Changing the set of classes later means restarting the whole optimization:  python INITIALIZE.py --force
(--force archives the old ledger.txt and runs/ and replaces prompt.txt.)
"""
import os
import re
import sys
import json
import shutil
import argparse
import subprocess
from datetime import datetime, timezone

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TEMPLATE_FILE = os.path.join(BASE_DIR, "prompt_template.txt")
CLASSES_FILE = os.path.join(BASE_DIR, "dataset", "classes.json")
LABELS_FILE = os.path.join(BASE_DIR, "dataset", "labels.json")
PROMPT_FILE = os.path.join(BASE_DIR, "prompt.txt")
PROMPT_CLASSES_FILE = os.path.join(BASE_DIR, "prompt_classes.json")  # the class set prompt.txt was built for
LEDGER_FILE = os.path.join(BASE_DIR, "ledger.txt")
RUNS_DIR = os.path.join(BASE_DIR, "runs")
ARCHIVE_DIR = os.path.join(BASE_DIR, "archive")

CLASS_NAME_RE = re.compile(r"^[a-z0-9_]+$")


def fail(message):
    print(f"ERROR: {message}", file=sys.stderr)
    sys.exit(1)

def load_classes():
    if not os.path.exists(CLASSES_FILE):
        fail(f"{os.path.relpath(CLASSES_FILE, BASE_DIR)} not found. Create your classes with `python labeler.py`.")
    with open(CLASSES_FILE, "r", encoding="utf-8") as f:
        entries = json.load(f).get("classes", [])
    classes, seen = [], set()
    for entry in entries:
        if not isinstance(entry, dict):
            fail(f"every class in classes.json must be an object with 'name' and 'hint'; found {entry!r}")
        name, hint = entry.get("name"), entry.get("hint")
        if not isinstance(name, str) or not CLASS_NAME_RE.match(name):
            fail(f"invalid class name {name!r} (lowercase letters, digits and underscores only)")
        if name in seen:
            fail(f"duplicate class name: {name}")
        if not isinstance(hint, str) or not hint.strip():
            fail(f"class '{name}' has no hint. Every class needs a hint describing it.")
        seen.add(name)
        classes.append({"name": name, "hint": " ".join(hint.split())})  # one line per hint bullet
    if len(classes) < 2:
        fail("classes.json needs at least 2 classes. Create them with `python labeler.py`.")
    return classes

def render_prompt(classes):
    with open(TEMPLATE_FILE, "r", encoding="utf-8") as f:
        template = f.read()
    values = {
        "CLASS_COUNT": str(len(classes)),
        "CLASS_LIST": "\n".join(f'* "{c["name"]}"' for c in classes),
        "CLASS_HINTS": "\n".join(f"* '{c['name']}': {c['hint']}" for c in classes),
        "EXAMPLE_CLASS": classes[0]["name"],
    }
    prompt = template
    for key, value in values.items():
        prompt = prompt.replace("{{" + key + "}}", value)
    leftover = re.findall(r"\{\{[A-Z_]+\}\}", prompt)
    if leftover:
        fail(f"prompt_template.txt has unknown placeholders: {', '.join(sorted(set(leftover)))}")
    return prompt

def archive_previous_runs():
    # A restart starts a new experiment; keep the old ledger and run details for reference instead of deleting them.
    targets = [p for p in (LEDGER_FILE, RUNS_DIR) if os.path.exists(p)]
    if not targets:
        return None
    dest = os.path.join(ARCHIVE_DIR, datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"))
    os.makedirs(dest, exist_ok=True)
    for path in targets:
        shutil.move(path, os.path.join(dest, os.path.basename(path)))
    return dest

def warn_about_labels(class_names):
    if not os.path.exists(LABELS_FILE):
        return
    with open(LABELS_FILE, "r", encoding="utf-8") as f:
        labels = json.load(f).get("labeled_data", [])
    unknown = sorted({e["label"] for e in labels} - set(class_names))
    if unknown:
        print(f"WARNING: labels.json uses classes not in classes.json: {', '.join(unknown)}. "
              f"evaluate.py will refuse to run until those labels are changed.")
    return len(labels)

def git_commit(message):
    # Commit only the two generated files, so nothing else that happens to be staged is swept in.
    paths = ["prompt.txt", "prompt_classes.json"]
    inside = subprocess.run(["git", "rev-parse", "--is-inside-work-tree"], cwd=BASE_DIR,
                            capture_output=True, text=True)
    if inside.returncode != 0:
        print("WARNING: not a git repository. evaluate.py needs git to keep or revert prompt.txt.")
        return
    subprocess.run(["git", "add", "--", *paths], cwd=BASE_DIR, check=True)
    result = subprocess.run(["git", "commit", "-m", message, "--", *paths], cwd=BASE_DIR,
                            capture_output=True, text=True)
    if result.returncode == 0:
        print("Committed prompt.txt and prompt_classes.json.")
    else:
        print("Nothing new to commit (prompt.txt and prompt_classes.json are unchanged in git).")

def main():
    parser = argparse.ArgumentParser(description="Create prompt.txt from prompt_template.txt and dataset/classes.json.")
    parser.add_argument("--force", action="store_true",
                        help="restart: archive ledger.txt and runs/, and overwrite the existing prompt.txt")
    args = parser.parse_args()

    if os.path.exists(PROMPT_FILE) and not args.force:
        fail("prompt.txt already exists, and may hold the optimization loop's progress. Not overwriting it.\n"
             "If you changed the set of classes and want to restart the whole process, run:\n"
             "    python INITIALIZE.py --force\n"
             "(this archives ledger.txt and runs/ and replaces prompt.txt)")

    classes = load_classes()
    class_names = [c["name"] for c in classes]
    prompt = render_prompt(classes)
    archived = archive_previous_runs() if args.force else None

    with open(PROMPT_FILE, "w", encoding="utf-8") as f:
        f.write(prompt)
    with open(PROMPT_CLASSES_FILE, "w", encoding="utf-8") as f:
        json.dump({"_note": "Written by INITIALIZE.py: the class set prompt.txt was built for. Do not edit; "
                            "evaluate.py compares it with dataset/classes.json and labels.json.",
                   "created": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                   "classes": class_names}, f, indent="\t")
        f.write("\n")

    print(f"Wrote prompt.txt for {len(classes)} classes: {', '.join(class_names)}")
    if archived:
        print(f"Archived the previous ledger.txt / runs/ to {os.path.relpath(archived, BASE_DIR)}")
    n_labels = warn_about_labels(class_names)
    git_commit(f"Initialize prompt.txt for {len(classes)} classes")

    print("\nNext steps:")
    if not n_labels or n_labels < 2:
        print("  - Label your documents with `python labeler.py` (at least 2; ideally many per class).")
    print("  - Run `python evaluate.py` to score the starting prompt, then start the optimization loop.")
    print("  - Labeling more files is fine at any time. But if you add, remove or rename a class, the whole")
    print("    process must restart: `python INITIALIZE.py --force`.")

if __name__ == "__main__":
    main()
