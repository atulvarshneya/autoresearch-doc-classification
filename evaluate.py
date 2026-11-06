import os
import json
import subprocess

import anthropic
from dotenv import load_dotenv

# --- CONFIGURATION ---
load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))  # provides ANTHROPIC_API_KEY

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

DATASET_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dataset")
LABELS_FILE = os.path.join(DATASET_DIR, "labels.json")
DOCUMENTS_DIR = os.path.join(DATASET_DIR, "files")
PROMPT_FILE = "prompt.txt"
LEDGER_FILE = "ledger.txt"

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

_client = None

def _get_client():
    global _client
    if _client is None:
        _client = anthropic.Anthropic(timeout=LLM_TIMEOUT_SECONDS, max_retries=LLM_MAX_RETRIES)
    return _client

def _extract_classname(response_text):
    # The prompt asks for {"classname": ..., "rationale": [...]}; pull out the classname.
    # Tolerates surrounding prose or ```json fences by parsing the outermost {...} span.
    start, end = response_text.find("{"), response_text.rfind("}")
    if start != -1 and end > start:
        try:
            parsed = json.loads(response_text[start:end + 1])
            if isinstance(parsed, dict) and isinstance(parsed.get("classname"), str):
                return parsed["classname"]
        except json.JSONDecodeError:
            pass
    # Not valid JSON with a classname: return the raw text so it shows up in the ledger's errors.
    return response_text

def call_llm(system_prompt, document_text):
    # Returns the predicted class name, or an "ERROR: ..." string (scored as wrong) on failure,
    # so one bad request doesn't abort the whole evaluation run.
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
        return f"ERROR: {type(e).__name__}: {e}"

    if response.stop_reason == "refusal":
        return "ERROR: refusal"
    text = "".join(block.text for block in response.content if block.type == "text")
    if response.stop_reason == "max_tokens" and not text:
        return "ERROR: max_tokens reached before any answer"
    return _extract_classname(text)

def evaluate_prompt():
    with open(PROMPT_FILE, "r") as f:
        current_prompt = f.read().strip()
        
    dataset = load_dataset()
    correct = 0
    total = len(dataset)
    errors = []

    print(f"Evaluating prompt against {total} samples...")
    
    for item in dataset:
        predicted = call_llm(current_prompt, item["text"])
        if predicted.strip().lower() == item["label"].strip().lower():
            correct += 1
        else:
            errors.append({"text": item["text"][:100], "expected": item["label"], "got": predicted})

    accuracy = (correct / total) * 100 if total > 0 else 0
    return accuracy, errors

def get_last_best_score():
    if not os.path.exists(LEDGER_FILE):
        return 0.0
    with open(LEDGER_FILE, "r") as f:
        lines = f.readlines()
        # Score lines look like "Score: 80.00% | Errors Sampled: 1"; the number is before the "|".
        # startswith() avoids matching "Score:" inside the "Sample Errors:" JSON lines.
        scores = [float(line.split("|")[0].replace("Score:", "").replace("%", "").strip())
                  for line in lines if line.startswith("Score:")]
        return max(scores) if scores else 0.0

def main():
    accuracy, errors = evaluate_prompt()
    last_best = get_last_best_score()
    
    print(f"\n--- RESULTS ---")
    print(f"Current Accuracy: {accuracy:.2f}%")
    print(f"Previous Best: {last_best:.2f}%")
    
    # Update the ledger
    with open(LEDGER_FILE, "a") as f:
        f.write(f"Score: {accuracy:.2f}% | Errors Sampled: {len(errors)}\n")
        if errors:
            f.write(f"Sample Errors: {json.dumps(errors[:3])}\n")
            
    # Mechanical Git control for Keep vs Revert
    if accuracy > last_best:
        print("🎉 Improvement found! Committing changes.")
        subprocess.run(["git", "add", PROMPT_FILE])
        subprocess.run(["git", "commit", "-m", f"Improve prompt accuracy to {accuracy:.2f}%"])
    else:
        print("❌ No improvement. Reverting prompt back to baseline.")
        subprocess.run(["git", "checkout", "HEAD", "--", PROMPT_FILE])

if __name__ == "__main__":
    main()
