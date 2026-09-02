"""Flask backend for the conversation filtering mini app.

Everything runs locally: an uploaded conversations.json is written to a
temporary working directory, parsed with pandas, and the filtered result is
rebuilt from the *original* JSON objects so the download keeps exactly the
same structure as the file that came in.
"""

import copy
import json
import os
import shutil
import tempfile
import uuid as uuid_lib

from flask import Flask, jsonify, request, send_file, render_template

import claude_parser
import gemini_parser
import openai_parser

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 512 * 1024 * 1024  # 512 MB

WORK_DIR = os.path.join(tempfile.gettempdir(), "conversation-filter-sessions")
os.makedirs(WORK_DIR, exist_ok=True)

# token -> {"source": <path to upload>, "result": <path to built export>, "platform": str}
SESSIONS = {}

# Each platform's parser exposes the same interface; the app stays generic by
# looking one up per session instead of hard-coding Claude.
PARSERS = {"claude": claude_parser, "openai": openai_parser, "gemini": gemini_parser}
SUPPORTED_PLATFORMS = set(PARSERS)


def _session_dir(token):
    return os.path.join(WORK_DIR, token)


def _parser(session):
    return PARSERS[session["platform"]]


def _require_session(token):
    session = SESSIONS.get(token)
    if session is None:
        return None, (jsonify({"error": "Session not found. Please upload your file again."}), 404)
    return session, None


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/upload", methods=["POST"])
def upload():
    platform = (request.form.get("platform") or "claude").lower()
    if platform not in SUPPORTED_PLATFORMS:
        return jsonify({"error": f"'{platform}' is not supported yet."}), 400

    uploaded = request.files.get("file")
    if uploaded is None or not uploaded.filename:
        return jsonify({"error": "No file was uploaded."}), 400
    if not uploaded.filename.lower().endswith(".json"):
        return jsonify({"error": "Please upload a .json file."}), 400

    parser = PARSERS[platform]
    token = uuid_lib.uuid4().hex
    directory = _session_dir(token)
    os.makedirs(directory, exist_ok=True)
    source = os.path.join(directory, "conversations.json")
    uploaded.save(source)

    try:
        raw = parser.load_raw(source)
    except json.JSONDecodeError as exc:
        shutil.rmtree(directory, ignore_errors=True)
        return jsonify({"error": f"That file isn't valid JSON ({exc.msg})."}), 400

    if not isinstance(raw, list):
        shutil.rmtree(directory, ignore_errors=True)
        return jsonify(
            {"error": "This conversations.json should be a list of conversations."}
        ), 400

    if not raw:
        shutil.rmtree(directory, ignore_errors=True)
        return jsonify({"error": "That file contains no conversations."}), 400

    try:
        records = parser.build_metadata(source)
    except (KeyError, ValueError) as exc:
        shutil.rmtree(directory, ignore_errors=True)
        message = str(exc) if isinstance(exc, ValueError) else \
            f"This doesn't look like a {platform} export — missing field {exc}."
        return jsonify({"error": message}), 400

    # `history` maps a conversation uuid to its undo/redo timeline — a stack of
    # message-list snapshots plus a cursor. Redactions and deletions both push a
    # new snapshot; undo/redo just move the cursor. Cumulative edit tallies ride
    # on each snapshot so the modified-row counts move with undo/redo too.
    SESSIONS[token] = {
        "source": source,
        "result": None,
        "platform": platform,
        "history": {},
    }

    return jsonify(
        {
            "token": token,
            "filename": uploaded.filename,
            "total_in_file": len(raw),
            "conversations": records,
        }
    )


@app.route("/api/transcript")
def transcript():
    session, error = _require_session(request.args.get("token"))
    if error:
        return error

    try:
        index = int(request.args.get("index", ""))
    except ValueError:
        return jsonify({"error": "Invalid conversation index."}), 400

    result = _parser(session).build_transcript(session["source"], index)
    uuid = _uuid_for_index(session, index)
    result.update(_history_flags(session, uuid))
    return jsonify(result)


# --------------------------------------------------------------------------
# Editing with undo/redo
#
# Every edit produces a new message-list snapshot pushed onto that
# conversation's history stack; the working file always mirrors the snapshot at
# the cursor. Undo/redo just move the cursor and rewrite the file to match.
# --------------------------------------------------------------------------

def _uuid_for_index(session, index):
    """Map a table row index back to its conversation id (listed-only frame)."""
    parser = _parser(session)
    listed = parser.list_conversations(parser.load_raw(session["source"]))
    return listed[index][parser.ID_FIELD] if 0 <= index < len(listed) else None


def _seed_history(session, conversation_uuid):
    """Start a conversation's timeline from its current state, if not tracked."""
    hist = session["history"].get(conversation_uuid)
    if hist is None:
        messages = _parser(session).conversation_messages(session["source"], conversation_uuid)
        if messages is None:
            return None
        hist = {
            "stack": [{"messages": copy.deepcopy(messages), "redactions": 0, "deletions": 0}],
            "cursor": 0,
        }
        session["history"][conversation_uuid] = hist
    return hist


def _history_flags(session, conversation_uuid):
    hist = session["history"].get(conversation_uuid)
    return {
        "can_undo": bool(hist and hist["cursor"] > 0),
        "can_redo": bool(hist and hist["cursor"] < len(hist["stack"]) - 1),
    }


def _apply_snapshot(session, conversation_uuid, snapshot):
    """Write a snapshot's messages to the working file and shape the response."""
    row = _parser(session).set_messages(session["source"], conversation_uuid, snapshot["messages"])
    session["result"] = None  # any file built before this edit is now stale
    return jsonify({
        "row": row,
        "redactions": snapshot["redactions"],
        "deletions": snapshot["deletions"],
        **_history_flags(session, conversation_uuid),
    })


def _commit(session, conversation_uuid, new_messages, *, add_redactions=0, add_deletions=0):
    """Push a new state onto the timeline (dropping any redo tail) and apply it."""
    hist = session["history"][conversation_uuid]
    current = hist["stack"][hist["cursor"]]
    snapshot = {
        "messages": copy.deepcopy(new_messages),
        "redactions": current["redactions"] + add_redactions,
        "deletions": current["deletions"] + add_deletions,
    }
    del hist["stack"][hist["cursor"] + 1:]
    hist["stack"].append(snapshot)
    hist["cursor"] += 1
    return _apply_snapshot(session, conversation_uuid, snapshot)


@app.route("/api/redact", methods=["POST"])
def redact():
    payload = request.get_json(silent=True) or {}
    session, error = _require_session(payload.get("token"))
    if error:
        return error

    conversation_uuid = payload.get("conversation_uuid")
    message_uuid = payload.get("message_uuid")
    target = payload.get("text")
    if not conversation_uuid or not message_uuid or not target:
        return jsonify({"error": "Missing redaction target."}), 400

    parser = _parser(session)
    if _seed_history(session, conversation_uuid) is None:
        return jsonify({"error": "That conversation is no longer in the file."}), 400
    messages = parser.conversation_messages(session["source"], conversation_uuid)

    try:
        new_messages = parser.redact_in_message(messages, message_uuid, target)
    except parser.EditError as exc:
        return jsonify({"error": str(exc)}), 400

    return _commit(session, conversation_uuid, new_messages, add_redactions=1)


@app.route("/api/delete_messages", methods=["POST"])
def delete_messages():
    payload = request.get_json(silent=True) or {}
    session, error = _require_session(payload.get("token"))
    if error:
        return error

    conversation_uuid = payload.get("conversation_uuid")
    message_uuids = payload.get("message_uuids") or []
    if not conversation_uuid or not message_uuids:
        return jsonify({"error": "No messages selected to delete."}), 400

    parser = _parser(session)
    if _seed_history(session, conversation_uuid) is None:
        return jsonify({"error": "That conversation is no longer in the file."}), 400
    messages = parser.conversation_messages(session["source"], conversation_uuid)

    try:
        new_messages, removed = parser.remove_messages(messages, message_uuids)
    except parser.EditError as exc:
        return jsonify({"error": str(exc)}), 400

    return _commit(session, conversation_uuid, new_messages, add_deletions=removed)


@app.route("/api/delete_text", methods=["POST"])
def delete_text():
    payload = request.get_json(silent=True) or {}
    session, error = _require_session(payload.get("token"))
    if error:
        return error

    conversation_uuid = payload.get("conversation_uuid")
    message_uuid = payload.get("message_uuid")
    selected = payload.get("text")
    occurrence = payload.get("occurrence", 0)
    if not conversation_uuid or not message_uuid or not selected:
        return jsonify({"error": "No text selected to delete."}), 400

    parser = _parser(session)
    if _seed_history(session, conversation_uuid) is None:
        return jsonify({"error": "That conversation is no longer in the file."}), 400
    messages = parser.conversation_messages(session["source"], conversation_uuid)

    try:
        new_messages = parser.delete_text_in_message(
            messages, message_uuid, selected, occurrence
        )
    except parser.EditError as exc:
        return jsonify({"error": str(exc)}), 400

    # Removing text is a redaction, so it rolls into the same tally.
    return _commit(session, conversation_uuid, new_messages, add_redactions=1)


@app.route("/api/undo", methods=["POST"])
def undo():
    session, conversation_uuid, error = _history_request()
    if error:
        return error
    hist = session["history"].get(conversation_uuid)
    if not hist or hist["cursor"] == 0:
        return jsonify({"error": "Nothing to undo."}), 400
    hist["cursor"] -= 1
    return _apply_snapshot(session, conversation_uuid, hist["stack"][hist["cursor"]])


@app.route("/api/redo", methods=["POST"])
def redo():
    session, conversation_uuid, error = _history_request()
    if error:
        return error
    hist = session["history"].get(conversation_uuid)
    if not hist or hist["cursor"] >= len(hist["stack"]) - 1:
        return jsonify({"error": "Nothing to redo."}), 400
    hist["cursor"] += 1
    return _apply_snapshot(session, conversation_uuid, hist["stack"][hist["cursor"]])


def _history_request():
    payload = request.get_json(silent=True) or {}
    session, error = _require_session(payload.get("token"))
    if error:
        return None, None, error
    conversation_uuid = payload.get("conversation_uuid")
    if not conversation_uuid:
        return None, None, (jsonify({"error": "Missing conversation."}), 400)
    return session, conversation_uuid, None


@app.route("/api/delete_conversations", methods=["POST"])
def delete_conversations():
    """Drop whole conversations from the working copy.

    Unlike the message-level edits above this is not undoable, so the client
    confirms first. Removing entries renumbers everything after them — the row
    indices the client holds are positions in the listed set — so the fresh
    metadata is returned for the caller to re-render from rather than trying to
    patch the old rows.
    """
    payload = request.get_json(silent=True) or {}
    session, error = _require_session(payload.get("token"))
    if error:
        return error

    selected = set(payload.get("uuids") or [])
    if not selected:
        return jsonify({"error": "No conversations selected to delete."}), 400

    parser = _parser(session)
    id_field = parser.ID_FIELD
    raw = parser.load_raw(session["source"])
    kept = [c for c in raw if c.get(id_field) not in selected]

    removed = len(raw) - len(kept)
    if not removed:
        return jsonify({"error": "Those conversations are no longer in the file."}), 400

    # Whole-list rewrite, so it stays parser-agnostic: only ID_FIELD differs.
    with open(session["source"], "w") as f:
        json.dump(kept, f, ensure_ascii=False)

    for conversation_uuid in selected:
        session["history"].pop(conversation_uuid, None)
    session["result"] = None  # any file built before this delete is now stale

    return jsonify(
        {
            "removed": removed,
            "total_in_file": len(kept),
            "conversations": parser.build_metadata(session["source"]) if kept else [],
        }
    )


@app.route("/api/build", methods=["POST"])
def build():
    payload = request.get_json(silent=True) or {}
    session, error = _require_session(payload.get("token"))
    if error:
        return error

    mode = payload.get("mode")
    if mode not in {"export", "remove"}:
        return jsonify({"error": "Mode must be 'export' or 'remove'."}), 400

    selected = set(payload.get("uuids") or [])
    if not selected:
        return jsonify({"error": "No conversations selected."}), 400

    parser = _parser(session)
    id_field = parser.ID_FIELD
    raw = parser.load_raw(session["source"])
    if mode == "export":
        filtered = [c for c in raw if c.get(id_field) in selected]
    else:
        filtered = [c for c in raw if c.get(id_field) not in selected]

    # Strip redaction markers (and their content) so nothing redacted reaches the
    # download. Parsers that don't do redaction fall back to an identity copy.
    sanitize = getattr(parser, "export_conversation", lambda c: c)
    cleaned = [sanitize(c) for c in filtered]

    result_path = os.path.join(os.path.dirname(session["source"]), "filtered_conversations.json")
    with open(result_path, "w") as f:
        json.dump(cleaned, f, indent=2, ensure_ascii=False)

    session["result"] = result_path
    session["result_name"] = (
        "exported_conversations.json" if mode == "export" else "conversations_filtered.json"
    )

    return jsonify(
        {
            "mode": mode,
            "filename": session["result_name"],
            "selected_count": len(selected),
            "conversations_in_file": len(filtered),
            "original_count": len(raw),
            "size_bytes": os.path.getsize(result_path),
        }
    )


@app.route("/api/download")
def download():
    session, error = _require_session(request.args.get("token"))
    if error:
        return error
    if not session.get("result"):
        return jsonify({"error": "Nothing has been built yet."}), 400

    return send_file(
        session["result"],
        mimetype="application/json",
        as_attachment=True,
        download_name=session.get("result_name", "conversations.json"),
    )


@app.route("/api/reset", methods=["POST"])
def reset():
    token = (request.get_json(silent=True) or {}).get("token")
    if token in SESSIONS:
        shutil.rmtree(_session_dir(token), ignore_errors=True)
        SESSIONS.pop(token, None)
    return jsonify({"ok": True})


@app.errorhandler(413)
def too_large(_):
    return jsonify({"error": "That file is larger than the 512 MB limit."}), 413


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=True)
