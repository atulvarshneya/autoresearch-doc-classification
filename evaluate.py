import os
import json
import subprocess

# --- CONFIGURATION ---
LABELED_DATA_FILES = ["dataset_part1.json", "dataset_part2.json"] # Path to your files
PROMPT_FILE = "prompt.txt"
LEDGER_FILE = "ledger.txt"

def load_dataset():
    # Replace this with your actual data loading logic
    # Expected format: [{"text": "...", "label": "Category1"}, ...]
    dataset = []
    for file in LABELED_DATA_FILES:
        with open(file, 'r') as f:
            dataset.extend(json.load(f))
    return dataset

def call_llm(system_prompt, document_text):
    # TODO: Implement your actual API call here (Anthropic/OpenAI)
    # Make sure to enforce a strict timeout so the loop doesn't hang
    pass

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
        scores = [float(line.split("|")[1].strip().replace("%", "")) for line in lines if "Score:" in line]
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
