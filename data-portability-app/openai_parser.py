"""Parsing helpers for OpenAI / ChatGPT ``conversations.json`` exports.

Mirrors ``claude_parser``'s public interface so the app can drive either one the
same way, but the shapes differ a lot:

* A ChatGPT export is a list of conversations, each with a ``title``, ``id``,
  unix ``create_time`` / ``update_time``, and a ``mapping`` — a dict of message
  *nodes* linked into a tree by ``parent`` / ``children``, plus a ``current_node``
  marking the active leaf. The visible conversation is the path from that leaf
  back to the root; branches (edits, regenerations) hang off to the side.
* Message text lives at ``message.content.parts`` (text) or ``.text`` (code),
  and system / tool / hidden nodes are filtered out.

The editable blob passed around by the app's undo/redo machinery is
``{"mapping": ..., "current_node": ...}`` — enough to edit and to keep the export
valid (a deleted node can't be left as ``current_node``).
"""

import copy
import json
from datetime import datetime, timezone

import redaction
import sensitivity


ID_FIELD = "id"


class EditError(Exception):
    """An in-place edit couldn't be applied."""


# --------------------------------------------------------------------------
# Loading & listing
# --------------------------------------------------------------------------

def load_raw(file_path):
    with open(file_path, "r") as f:
        return json.load(f)


def export_conversation(conversation):
    """A deep copy of one conversation with redactions stripped for export.

    Redacted runs live wrapped in markers so they can be shown back as boxes;
    here the markers and their content are removed from every message node's
    text, so the downloaded file carries nothing that was redacted. The graph
    shape is otherwise untouched.
    """
    result = copy.deepcopy(conversation)
    for node in (result.get("mapping") or {}).values():
        message = node.get("message") if isinstance(node, dict) else None
        if not message:
            continue
        content = message.get("content") or {}
        parts = content.get("parts")
        if isinstance(parts, list):
            content["parts"] = [redaction.strip(p) if isinstance(p, str) else p
                                for p in parts]
        if isinstance(content.get("text"), str):
            content["text"] = redaction.strip(content["text"])
    return result


def list_conversations(raw):
    """Conversations the table lists, in order (untitled ones dropped)."""
    return [c for c in raw if (c.get("title") or "") != ""]


# --------------------------------------------------------------------------
# Message extraction from the mapping graph
# --------------------------------------------------------------------------

def _iso(ts):
    """Unix seconds -> ISO 8601 string, so the UI can format it like Claude's."""
    if ts is None:
        return None
    try:
        return datetime.fromtimestamp(float(ts), tz=timezone.utc).isoformat()
    except (TypeError, ValueError, OSError):
        return None


def _role(message):
    return ((message.get("author") or {}).get("role")) or ""


def _node_text(message):
    """User-visible text of a message node ("" for tool/code/empty nodes)."""
    content = message.get("content") or {}
    ctype = content.get("content_type")
    if ctype in ("code", "execution_output", "tether_browsing_display"):
        return ""  # tool machinery, not conversation text
    parts = content.get("parts")
    if isinstance(parts, list):
        return "\n".join(p for p in parts if isinstance(p, str) and p.strip())
    if isinstance(content.get("text"), str):
        return content["text"]
    return ""


def _is_visible(message):
    if not message:
        return False
    if _role(message) in ("system", "tool"):
        return False
    if (message.get("metadata") or {}).get("is_visually_hidden_from_conversation"):
        return False
    return bool(_node_text(message).strip())


def _ordered_node_ids(mapping, current_node):
    """Node ids of the visible thread, root-first.

    Normally the path from ``current_node`` back to the root. If that's missing
    we walk children from the root(s), taking the latest branch at each step, so
    there's always a sensible linear reading.
    """
    if current_node and current_node in mapping:
        path, node_id, seen = [], current_node, set()
        while node_id and node_id in mapping and node_id not in seen:
            seen.add(node_id)
            path.append(node_id)
            node_id = mapping[node_id].get("parent")
        path.reverse()
        return path

    order, seen = [], set()
    roots = [k for k, v in mapping.items() if not v.get("parent")]
    for root in roots:
        node_id = root
        while node_id and node_id in mapping and node_id not in seen:
            seen.add(node_id)
            order.append(node_id)
            children = mapping[node_id].get("children") or []
            node_id = children[-1] if children else None
    return order


def _transcript(conversation):
    """The visible user/assistant messages of one conversation, in order."""
    mapping = conversation.get("mapping") or {}
    current = conversation.get("current_node")
    messages = []
    for node_id in _ordered_node_ids(mapping, current):
        message = (mapping.get(node_id) or {}).get("message")
        if not _is_visible(message):
            continue
        messages.append({
            "uuid": node_id,
            "sender": "human" if _role(message) == "user" else "assistant",
            "created_at": _iso(message.get("create_time")),
            "text": _node_text(message),
        })
    return messages


# --------------------------------------------------------------------------
# Metadata rows
# --------------------------------------------------------------------------

def _fmt_duration(seconds):
    if seconds < 60:
        return f"{int(seconds)}s"
    if seconds < 3600:
        return f"{int(seconds // 60)}m"
    if seconds < 86400:
        hours, minutes = divmod(int(seconds // 60), 60)
        return f"{hours}h {minutes}m" if minutes else f"{hours}h"
    days = int(seconds // 86400)
    hours = int((seconds % 86400) // 3600)
    return f"{days}d {hours}h" if hours else f"{days}d"


def _duration(iso_times):
    stamps = []
    for t in iso_times:
        if not t:
            continue
        try:
            stamps.append(datetime.fromisoformat(t))
        except ValueError:
            pass
    if len(stamps) < 2:
        return "—", 0.0
    seconds = (max(stamps) - min(stamps)).total_seconds()
    return _fmt_duration(seconds), float(seconds)


def _row(index, conversation, messages):
    """One table row. Sensitivity covers the whole conversation, both sides."""
    # Redacted runs are stripped from the export, so they don't count toward the
    # tag or the word count.
    texts = [redaction.strip(m["text"]) for m in messages]
    tag = sensitivity.classify("\n".join(texts))
    duration, duration_seconds = _duration([m["created_at"] for m in messages])
    return {
        "index": int(index),
        "uuid": conversation.get("id"),
        "name": conversation.get("title") or "(untitled)",
        "created_at": _iso(conversation.get("create_time")),
        "updated_at": _iso(conversation.get("update_time")),
        "message_count": len(messages),
        "word_count": sum(len(t.split()) for t in texts),
        "duration": duration,
        "duration_seconds": duration_seconds,
        "sensitivity": tag["tag"],
        "sensitivity_rank": tag["rank"],
        "sensitivity_reasons": tag["reasons"],
    }


def build_metadata(file_path):
    raw = load_raw(file_path)
    if not isinstance(raw, list):
        raise ValueError("A ChatGPT conversations.json should be a list of conversations.")
    rows = []
    for index, conversation in enumerate(list_conversations(raw)):
        if "mapping" not in conversation:
            raise ValueError("This doesn't look like a ChatGPT export — missing 'mapping'.")
        rows.append(_row(index, conversation, _transcript(conversation)))
    return rows


def build_transcript(file_path, conversation_index):
    conversations = list_conversations(load_raw(file_path))
    if not 0 <= conversation_index < len(conversations):
        return {"name": "", "messages": []}
    conversation = conversations[conversation_index]
    # ``text`` becomes the display text (markers removed, content kept) so it
    # equals the concatenation of ``segments``; redacted runs are boxed and the
    # still-flagged ones highlighted. The export strip happens at build time.
    messages = [
        {**m,
         "text": redaction.unwrap(m["text"]),
         "segments": redaction.display_segments(m["text"], sensitivity.segment)}
        for m in _transcript(conversation)
    ]
    return {"name": conversation.get("title") or "(untitled)", "messages": messages}


# --------------------------------------------------------------------------
# Editing — the blob is {"mapping", "current_node"}
# --------------------------------------------------------------------------

def _find(raw, conversation_id):
    return next((c for c in raw if c.get("id") == conversation_id), None)


def _row_index(raw, conversation):
    index = 0
    for candidate in raw:
        if candidate is conversation:
            return index
        if (candidate.get("title") or "") != "":
            index += 1
    return index


def conversation_messages(file_path, conversation_id):
    """The editable blob for one conversation, or None if it's gone."""
    conversation = _find(load_raw(file_path), conversation_id)
    if conversation is None:
        return None
    return {
        "mapping": conversation.get("mapping") or {},
        "current_node": conversation.get("current_node"),
    }


def set_messages(file_path, conversation_id, blob):
    """Write an edited blob back into the working copy; return the fresh row."""
    raw = load_raw(file_path)
    conversation = _find(raw, conversation_id)
    if conversation is None:
        raise EditError("That conversation is no longer in the file.")

    conversation["mapping"] = blob["mapping"]
    conversation["current_node"] = blob.get("current_node")
    with open(file_path, "w") as f:
        json.dump(raw, f, ensure_ascii=False)

    return _row(_row_index(raw, conversation), conversation, _transcript(conversation))


def _node_message(blob, node_id):
    node = blob["mapping"].get(node_id)
    message = node.get("message") if node else None
    if not message:
        raise EditError("That message is no longer in the conversation.")
    return message


def _set_node_text(message, new_text):
    """Store ``new_text`` back into a node, keeping its content shape valid."""
    content = message.get("content") or {}
    if isinstance(content.get("text"), str) and "parts" not in content:
        content["text"] = new_text
    else:
        content["parts"] = [new_text]
    message["content"] = content


def _nth_index(text, sub, n):
    start = idx = 0
    for _ in range(n + 1):
        idx = text.find(sub, start)
        if idx < 0:
            return -1
        start = idx + len(sub)
    return idx


def redact_in_message(blob, node_id, target):
    """Remove every copy of a flagged run from one message node."""
    if not target:
        raise EditError("Nothing to redact.")
    blob = copy.deepcopy(blob)
    message = _node_message(blob, node_id)

    plain = _node_text(message)
    flagged = {seg["text"]
               for seg in redaction.display_segments(plain, sensitivity.segment)
               if seg.get("tier")}
    if target not in flagged:
        raise EditError("That text isn't currently flagged in this message.")

    _set_node_text(message, redaction.wrap_target(plain, target))
    return blob


def delete_text_in_message(blob, node_id, selected, occurrence=0):
    """Remove one highlighted run of text from a message node."""
    if not selected:
        raise EditError("No text selected.")
    try:
        occurrence = max(0, int(occurrence))
    except (TypeError, ValueError):
        occurrence = 0

    blob = copy.deepcopy(blob)
    message = _node_message(blob, node_id)

    plain = _node_text(message)
    new_plain, wrapped = redaction.wrap_occurrence(plain, selected, occurrence)
    if not wrapped:
        raise EditError("That selection no longer matches the message text.")

    _set_node_text(message, new_plain)
    return blob


def remove_messages(blob, node_ids):
    """Drop whole message nodes, re-linking their children to the parent.

    Returns ``(new_blob, removed_count)``. If a removed node was the active leaf,
    ``current_node`` moves up to its parent so the export stays valid.
    """
    ids = {n for n in node_ids if n}
    if not ids:
        raise EditError("No messages selected.")

    blob = copy.deepcopy(blob)
    mapping = blob["mapping"]
    present = [n for n in ids if n in mapping]
    if not present:
        raise EditError("None of those messages are in the conversation.")

    for node_id in present:
        node = mapping.get(node_id)
        if not node:
            continue
        parent = node.get("parent")
        children = node.get("children") or []

        if parent and parent in mapping:
            siblings = mapping[parent].get("children") or []
            rewired = []
            for child in siblings:
                rewired.extend(children if child == node_id else [child])
            mapping[parent]["children"] = rewired
        for child in children:
            if child in mapping:
                mapping[child]["parent"] = parent

        if blob.get("current_node") == node_id:
            blob["current_node"] = parent
        del mapping[node_id]

    return blob, len(present)
