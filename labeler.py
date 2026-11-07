"""Local web app for labeling the documents in dataset/files/.

Run:  python labeler.py [--port 8765]   then open http://127.0.0.1:8765
Every label change is saved to dataset/labels.json immediately (atomic write), so you can stop at any time.
"""
import os
import re
import json
import argparse
import threading
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

import markdown

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATASET_DIR = os.path.join(BASE_DIR, "dataset")
DOCUMENTS_DIR = os.path.join(DATASET_DIR, "files")
LABELS_FILE = os.path.join(DATASET_DIR, "labels.json")
CLASSES_FILE = os.path.join(DATASET_DIR, "classes.json")
PAGE_FILE = os.path.join(BASE_DIR, "labeler.html")

CLASS_NAME_RE = re.compile(r"^[a-z0-9_]+$")
_lock = threading.Lock()  # serializes read-modify-write of the JSON files


# --- storage ---

def _read_json(path, default):
    if not os.path.exists(path):
        return default
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def _write_atomic(path, text):
    # Write to a temp file and swap it in, so a crash can never leave a half-written file.
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, path)

def _write_json_atomic(path, data):
    _write_atomic(path, json.dumps(data, indent="\t", ensure_ascii=False) + "\n")

def _write_labels_atomic(data):
    # One entry per line (the file's original hand-written layout), so each label change is a one-line git diff.
    entries = ",\n".join(f'\t\t{{ "file": {json.dumps(e["file"])}, "label": {json.dumps(e["label"])} }}'
                         for e in data["labeled_data"])
    parts = ['\t"labeled_data":\n\t[\n' + entries + ("\n" if entries else "") + "\t]"]
    parts += [f"\t{json.dumps(k)}: {json.dumps(v)}" for k, v in data.items() if k != "labeled_data"]
    _write_atomic(LABELS_FILE, "{\n" + ",\n".join(parts) + "\n}\n")

def read_labels():
    data = _read_json(LABELS_FILE, {"labeled_data": []})
    data.setdefault("labeled_data", [])
    return data

def read_classes():
    # classes.json: {"classes": [{"name": "...", "hint": "..."}, ...]}. Bare-string entries are tolerated.
    raw = _read_json(CLASSES_FILE, {"classes": []}).get("classes", [])
    return [c if isinstance(c, dict) else {"name": c, "hint": ""} for c in raw]

def list_documents():
    return sorted(name for name in os.listdir(DOCUMENTS_DIR)
                  if os.path.isfile(os.path.join(DOCUMENTS_DIR, name)) and not name.startswith("."))

def build_state():
    labels = {e["file"]: e["label"] for e in read_labels()["labeled_data"]}
    files = list_documents()
    known = read_classes()
    known_names = {c["name"] for c in known}
    # Labels already used in labels.json but missing from classes.json are still shown, flagged.
    unknown = sorted({l for l in labels.values() if l not in known_names})
    return {
        "classes": [{"name": c["name"], "hint": c.get("hint", ""), "unknown": False} for c in known]
                   + [{"name": c, "hint": "", "unknown": True} for c in unknown],
        "files": [{"name": f, "label": labels.get(f)} for f in files],
        "orphans": sorted(set(labels) - set(files)),
    }

def render_document(name):
    with open(os.path.join(DOCUMENTS_DIR, name), "r", encoding="utf-8") as f:
        raw = f.read()
    # Neutralize raw HTML (e.g. <script>) in documents before rendering; existing entities like &amp; still work.
    html = markdown.markdown(raw.replace("<", "&lt;"), extensions=["tables"])
    return {"name": name, "raw": raw, "html": html}

def set_label(name, label):
    with _lock:
        if name not in list_documents():
            raise ValueError(f"unknown file: {name}")
        allowed = {c["name"] for c in build_state()["classes"]}
        if label is not None and label not in allowed:
            raise ValueError(f"unknown class: {label}")
        data = read_labels()
        entries = data["labeled_data"]
        existing = next((e for e in entries if e["file"] == name), None)
        if label is None:
            data["labeled_data"] = [e for e in entries if e["file"] != name]
        elif existing:
            existing["label"] = label
        else:
            entries.append({"file": name, "label": label})
        _write_labels_atomic(data)

def add_class(name, hint):
    # Every class needs a hint: the description an LLM uses to recognize documents of that class.
    with _lock:
        if not isinstance(name, str) or not CLASS_NAME_RE.match(name):
            raise ValueError("class names may only contain lowercase letters, digits and underscores")
        hint = hint.strip() if isinstance(hint, str) else ""
        if not hint:
            raise ValueError("a hint describing the class is required")
        data = _read_json(CLASSES_FILE, {"classes": []})
        classes = read_classes()
        if any(c["name"] == name for c in classes):
            raise ValueError(f"class already exists: {name}")
        data["classes"] = classes + [{"name": name, "hint": hint}]
        _write_json_atomic(CLASSES_FILE, data)


# --- HTTP ---

class Handler(BaseHTTPRequestHandler):
    def _send(self, status, body, content_type="application/json; charset=utf-8"):
        payload = body if isinstance(body, bytes) else json.dumps(body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(payload)

    def _error(self, status, message):
        self._send(status, {"error": message})

    def do_GET(self):
        url = urlparse(self.path)
        if url.path == "/":
            with open(PAGE_FILE, "rb") as f:
                self._send(200, f.read(), "text/html; charset=utf-8")
        elif url.path == "/api/state":
            self._send(200, build_state())
        elif url.path == "/api/file":
            name = parse_qs(url.query).get("name", [""])[0]
            if name not in list_documents():  # exact match against the listing: no path traversal
                return self._error(404, "file not found")
            try:
                self._send(200, render_document(name))
            except UnicodeDecodeError:
                self._error(422, f"{name} is not a UTF-8 text file")
        else:
            self._error(404, "not found")

    def do_POST(self):
        try:
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length) or b"{}")
        except (ValueError, json.JSONDecodeError):
            return self._error(400, "invalid JSON body")
        try:
            if self.path == "/api/label":
                set_label(body.get("file"), body.get("label"))
            elif self.path == "/api/classes":
                add_class(body.get("name"), body.get("hint"))
            else:
                return self._error(404, "not found")
        except ValueError as e:
            return self._error(400, str(e))
        self._send(200, build_state())

    def log_message(self, fmt, *args):
        pass  # keep the console quiet


def main():
    parser = argparse.ArgumentParser(description="Label the documents in dataset/files/.")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"Labeler running at http://127.0.0.1:{args.port}  (Ctrl+C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")

if __name__ == "__main__":
    main()
