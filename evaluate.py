import os
import json
import hashlib
import difflib
import subprocess
from collections import Counter
from datetime import datetime, timezone

import anthropic
from dotenv import load_dotenv

# --- CONFIGURATION ---
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE_DIR, ".env"))  # provides ANTHROPIC_API_KEY

LLM_MODEL = "claude-haiku-5-5"
LLM_EFFORT = "low"            # classification doesn't need deep thinking; Haiku 5.5 defaults to "medium"
LLM_MAX_TOKENS = 4096         # room for brief thinking + the JSON answer
LLM_TIMEOUT_SECONDS = 60      # per attempt
LLM_MAX_RETRIES = 2           # SDK retries 429/5xx/connection errors with backoff

# Structured outputs: the API guarantees the reply is JSON matching this schema.
# classname is deliberately a free string (not an enum) so the class list lives only in prompt.txt.
RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "classname": {"type": "string"},
        "rationale": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["classname", "rationale"],
    "additionalProperties": False,
}

DATASET_DIR = os.path.join(BASE_DIR, "dataset")
LABELS_FILE = os.path.join(DATASET_DIR, "labels.json")
DOCUMENTS_DIR = os.path.join(DATASET_DIR, "files")
PROMPT_FILE = "prompt.txt"
LEDGER_FILE = "ledger.txt"    # one compact JSON summary line per run; the agent reads all of it
RUNS_DIR = "runs"             # runs/<run_id>.json: full detail for one run

# Train / held-out split. Each file's side is fixed by a hash of its name, so files never switch
# sides as the dataset grows. The agent sees per-file errors only for train files; keep/revert is
# decided by held-out macro-F1, and held-out per-file results are never written anywhere.
HOLDOUT_FRACTION = 0.3
SPLIT_SALT = "autoresearch-split-v1"  # changing this reshuffles the split (and starts a fresh best score)
MIN_IMPROVEMENT = 0.0         # held-out macro-F1 (0-1 scale) must beat the best by MORE than this

# Ledger size limits, so ledger.txt stays readable in full as runs accumulate.
TOP_CONFUSIONS = 10
WEAKEST_CLASSES = 5
MAX_DIFF_CHARS = 4000

ERROR_LABEL = "<error>"       # all API/parse failures count as this one predicted label in metrics


# --- DATA ---

def load_dataset():
    # labels.json format: {"labeled_data": [{"file": "<name in files/>", "label": "<class>"}, ...]}
    # Returns: [{"file": "...", "text": "...", "label": "..."}, ...]
    with open(LABELS_FILE, "r", encoding="utf-8") as f:
        entries = json.load(f)["labeled_data"]

    dataset = []
    for entry in entries:
        doc_path = os.path.join(DOCUMENTS_DIR, entry["file"])
        with open(doc_path, "r", encoding="utf-8") as f:
            text = f.read()
        dataset.append({"file": entry["file"], "text": text, "label": entry["label"]})
    return dataset

def _split_score(filename):
    digest = hashlib.sha256(f"{SPLIT_SALT}:{filename}".encode("utf-8")).hexdigest()
    return int(digest[:8], 16) / 0xFFFFFFFF

def split_dataset(dataset):
    ordered = sorted(dataset, key=lambda item: _split_score(item["file"]))
    holdout = [item for item in ordered if _split_score(item["file"]) < HOLDOUT_FRACTION]
    train = [item for item in ordered if _split_score(item["file"]) >= HOLDOUT_FRACTION]
    # Tiny datasets: the threshold can leave one side empty; move the file nearest the threshold across.
    if not holdout:
        holdout, train = train[:1], train[1:]
    elif not train:
        holdout, train = holdout[:-1], holdout[-1:]
    return train, holdout

def config_fingerprint(dataset):
    # Scores are only comparable between runs with the same data, split and model settings.
    h = hashlib.sha256()
    for item in sorted(dataset, key=lambda i: i["file"]):
        h.update(item["file"].encode("utf-8") + b"\0" + item["label"].encode("utf-8") + b"\0")
        h.update(hashlib.sha256(item["text"].encode("utf-8")).digest())
    settings = {"model": LLM_MODEL, "effort": LLM_EFFORT, "schema": RESPONSE_SCHEMA,
                "holdout_fraction": HOLDOUT_FRACTION, "split_salt": SPLIT_SALT}
    h.update(json.dumps(settings, sort_keys=True).encode("utf-8"))
    return h.hexdigest()[:16]


# --- LLM ---

_client = None

def _get_client():
    global _client
    if _client is None:
        _client = anthropic.Anthropic(timeout=LLM_TIMEOUT_SECONDS, max_retries=LLM_MAX_RETRIES)
    return _client

def _parse_reply(response_text):
    # Structured outputs make this plain JSON; the {...} search is a safety net for stray text.
    start, end = response_text.find("{"), response_text.rfind("}")
    if start != -1 and end > start:
        try:
            parsed = json.loads(response_text[start:end + 1])
            if isinstance(parsed, dict) and isinstance(parsed.get("classname"), str):
                rationale = parsed.get("rationale")
                return {"classname": parsed["classname"],
                        "rationale": rationale if isinstance(rationale, list) else []}
        except json.JSONDecodeError:
            pass
    return {"classname": f"ERROR: unparseable reply: {response_text[:200]}", "rationale": []}

def call_llm(system_prompt, document_text):
    # Returns {"classname": ..., "rationale": [...]}. On failure classname is "ERROR: ..." (scored as
    # wrong) so one bad request doesn't abort the whole run. A missing API key still raises.
    try:
        response = _get_client().messages.create(
            model=LLM_MODEL,
            max_tokens=LLM_MAX_TOKENS,
            output_config={
                "effort": LLM_EFFORT,
                "format": {"type": "json_schema", "schema": RESPONSE_SCHEMA},
            },
            system=system_prompt,
            messages=[{"role": "user", "content": document_text}],
        )
    except anthropic.APIError as e:
        return {"classname": f"ERROR: {type(e).__name__}: {e}", "rationale": []}

    if response.stop_reason == "refusal":
        return {"classname": "ERROR: refusal", "rationale": []}
    text = "".join(block.text for block in response.content if block.type == "text")
    if response.stop_reason == "max_tokens" and not text:
        return {"classname": "ERROR: max_tokens reached before any answer", "rationale": []}
    return _parse_reply(text)


# --- METRICS ---

def _norm(label):
    label = label.strip().lower()
    return ERROR_LABEL if label.startswith("error:") else label

def compute_metrics(results):
    # Macro-F1 averages F1 over every label that appears as expected or predicted, so a hallucinated
    # class name counts as an extra class with F1 = 0.
    pairs = [(_norm(r["expected"]), _norm(r["predicted"])) for r in results]
    labels = sorted({e for e, _ in pairs} | {p for _, p in pairs})
    per_class, f1s = {}, []
    for c in labels:
        tp = sum(1 for e, p in pairs if e == c and p == c)
        fp = sum(1 for e, p in pairs if e != c and p == c)
        fn = sum(1 for e, p in pairs if e == c and p != c)
        precision = tp / (tp + fp) if tp + fp else 0.0
        recall = tp / (tp + fn) if tp + fn else 0.0
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        f1s.append(f1)
        per_class[c] = {"support": tp + fn, "precision": round(precision, 4),
                        "recall": round(recall, 4), "f1": round(f1, 4)}
    accuracy = sum(1 for e, p in pairs if e == p) / len(pairs) if pairs else 0.0
    macro_f1 = sum(f1s) / len(f1s) if f1s else 0.0
    confusions = Counter((e, p) for e, p in pairs if e != p).most_common(TOP_CONFUSIONS)
    return {
        "n": len(pairs),
        "accuracy": round(accuracy, 4),
        "macro_f1": round(macro_f1, 4),
        "per_class": per_class,
        "top_confusions": [{"expected": e, "predicted": p, "count": n} for (e, p), n in confusions],
    }


# --- LEDGER ---

def read_ledger():
    entries = []
    if not os.path.exists(LEDGER_FILE):
        return entries
    with open(LEDGER_FILE, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue  # e.g. lines in the old plain-text format
            if isinstance(entry, dict):
                entries.append(entry)
    return entries

def find_best_run(entries, fingerprint):
    kept = [e for e in entries if e.get("fingerprint") == fingerprint and e.get("decision") == "kept"]
    return max(kept, key=lambda e: e["holdout"]["macro_f1"], default=None)

def load_run_detail(run_id):
    path = os.path.join(RUNS_DIR, f"{run_id}.json")
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def prompt_diff(old_prompt, new_prompt):
    diff = "\n".join(difflib.unified_diff(old_prompt.splitlines(), new_prompt.splitlines(),
                                          "best_prompt", "this_prompt", n=1, lineterm=""))
    if len(diff) > MAX_DIFF_CHARS:
        diff = diff[:MAX_DIFF_CHARS] + "\n... (truncated; full prompt is in this run's detail file)"
    return diff


# --- MAIN ---

def classify(prompt, items, split_name):
    results = []
    for i, item in enumerate(items, 1):
        reply = call_llm(prompt, item["text"])
        correct = _norm(reply["classname"]) == _norm(item["label"])
        print(f"  [{split_name} {i}/{len(items)}] {'ok   ' if correct else 'WRONG'} {item['file']}")
        results.append({"file": item["file"], "expected": item["label"], "predicted": reply["classname"],
                        "correct": correct, "rationale": reply["rationale"]})
    return results

def main():
    with open(PROMPT_FILE, "r", encoding="utf-8") as f:
        prompt = f.read().strip()

    dataset = load_dataset()
    if len(dataset) < 2:
        raise SystemExit("Need at least 2 labeled files to form a train and a held-out split.")
    train_items, holdout_items = split_dataset(dataset)
    fingerprint = config_fingerprint(dataset)

    entries = read_ledger()
    best = find_best_run(entries, fingerprint)
    best_detail = load_run_detail(best["run_id"]) if best else None

    print(f"Evaluating prompt on {len(train_items)} train + {len(holdout_items)} held-out files...")
    train_results = classify(prompt, train_items, "train")
    holdout_results = classify(prompt, holdout_items, "held-out")

    train_metrics = compute_metrics(train_results)
    holdout_metrics = compute_metrics(holdout_results)
    holdout_f1 = holdout_metrics["macro_f1"]
    best_f1 = best["holdout"]["macro_f1"] if best else None
    improved = best is None or holdout_f1 > best_f1 + MIN_IMPROVEMENT
    decision = "kept" if improved else "reverted"

    # Compare per-file train results with the best run: what this prompt change broke and fixed.
    regressions, fixes, diff = [], [], None
    if best_detail:
        best_correct = {r["file"]: r["correct"] for r in best_detail["train"]["results"]}
        regressions = [r["file"] for r in train_results if best_correct.get(r["file"]) is True and not r["correct"]]
        fixes = [r["file"] for r in train_results if best_correct.get(r["file"]) is False and r["correct"]]
        diff = prompt_diff(best_detail["prompt"], prompt)

    now = datetime.now(timezone.utc)
    run_id = now.strftime("%Y%m%dT%H%M%SZ")
    if os.path.exists(os.path.join(RUNS_DIR, f"{run_id}.json")):  # two runs within the same second
        run_id += f"-{now.microsecond:06d}"
    detail_path = os.path.join(RUNS_DIR, f"{run_id}.json")

    # Detail file: full prompt, per-class train metrics, and every train file's outcome. Rationales are
    # kept only for errors (correct ones would dominate the size at 100+ files). Held-out files: aggregate only.
    detail = {
        "run_id": run_id,
        "fingerprint": fingerprint,
        "prompt": prompt,
        "train": {
            "metrics": train_metrics,
            "results": [{k: v for k, v in r.items() if k != "rationale" or not r["correct"]}
                        for r in train_results],
        },
        "holdout": {k: holdout_metrics[k] for k in ("n", "accuracy", "macro_f1")},
    }
    os.makedirs(RUNS_DIR, exist_ok=True)
    with open(detail_path, "w", encoding="utf-8") as f:
        json.dump(detail, f, indent=2)

    weakest = sorted(((c, m) for c, m in train_metrics["per_class"].items() if m["support"] and m["f1"] < 1.0),
                     key=lambda cm: cm[1]["f1"])[:WEAKEST_CLASSES]
    summary = {
        "run_id": run_id,
        "timestamp": now.isoformat(timespec="seconds"),
        "fingerprint": fingerprint,
        "decision": decision,
        "holdout": {k: holdout_metrics[k] for k in ("n", "accuracy", "macro_f1")},
        "best_holdout_macro_f1_before": best_f1,
        "compared_to_run": best["run_id"] if best else None,
        "train": {k: train_metrics[k] for k in ("n", "accuracy", "macro_f1")},
        "weakest_train_classes": [{"class": c, "f1": m["f1"], "support": m["support"]} for c, m in weakest],
        "top_train_confusions": train_metrics["top_confusions"],
        "regressions_vs_best": regressions,
        "fixes_vs_best": fixes,
        "prompt_diff_vs_best": diff,
        "detail_file": detail_path,
    }
    with open(LEDGER_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(summary) + "\n")

    print("\n--- RESULTS ---")
    print(f"Train:    accuracy {train_metrics['accuracy']:.2%}  macro-F1 {train_metrics['macro_f1']:.4f}")
    print(f"Held-out: accuracy {holdout_metrics['accuracy']:.2%}  macro-F1 {holdout_f1:.4f}  (decides keep/revert)")
    print(f"Best held-out macro-F1 before this run: {'none (first run with this data/config)' if best is None else f'{best_f1:.4f}'}")
    print(f"Details: {detail_path}")

    # Mechanical Git control for Keep vs Revert
    if improved:
        print("🎉 Improvement found! Committing changes.")
        subprocess.run(["git", "add", PROMPT_FILE])
        subprocess.run(["git", "commit", "-m", f"Improve held-out macro-F1 to {holdout_f1:.4f}"])
    else:
        print("❌ No improvement. Reverting prompt back to baseline.")
        subprocess.run(["git", "checkout", "HEAD", "--", PROMPT_FILE])

if __name__ == "__main__":
    main()
