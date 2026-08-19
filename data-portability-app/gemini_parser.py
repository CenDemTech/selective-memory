"""Parsing helpers for Google Gemini ``conversations.json`` exports.

Gemini's export is a Google-Takeout *activity log*, not conversations with nested
messages: a flat list where each item is one turn. There's no message list and no
per-item id, so this parser maps each activity to a small two-message
conversation and uses the ISO ``time`` as the stable identity.

Per item:
* ``title`` — the user's prompt, prefixed with ``"Prompted "`` (or, for actions
  like "Created Gemini Canvas…", a description used as the prompt).
* ``safeHtmlItem[].html`` — the response, as HTML. Stripped to text for display;
  when it's just an image the ``subtitles`` (e.g. "1 generated image.") stand in.
* ``time`` — ISO timestamp, used as the conversation id (never edited, so it's
  stable across edits and unique enough in practice).

The editable blob is ``{title, safeHtmlItem, subtitles}`` — only the fields an
edit can touch, and only the ones the item actually has, so the export round-trips
byte-for-byte until something is changed. Message ids are the synthetic
``"prompt"`` / ``"response"``.
"""

import copy
import html as html_lib
import json
import re

import sensitivity


ID_FIELD = "time"
PROMPT_PREFIX = "Prompted "


class EditError(Exception):
    """An in-place edit couldn't be applied."""


def load_raw(file_path):
    with open(file_path, "r") as f:
        return json.load(f)


def list_conversations(raw):
    """Every activity with a timestamp — the id, and a stable filter that edits
    can't change (so row indices stay put across a session)."""
    return [c for c in raw if c.get("time")]


# --------------------------------------------------------------------------
# Text extraction (works on a raw item or an editable blob alike)
# --------------------------------------------------------------------------

_TAG = re.compile(r"<[^>]+>")


def _strip_html(fragment):
    return html_lib.unescape(_TAG.sub("", fragment or "")).strip()


def _prompt_prefix(item):
    return PROMPT_PREFIX if (item.get("title") or "").startswith(PROMPT_PREFIX) else ""


def _prompt_text(item):
    title = (item.get("title") or "").strip()
    if title.startswith(PROMPT_PREFIX):
        return title[len(PROMPT_PREFIX):].strip()
    return title


def _html_text(item):
    parts = [_strip_html(h.get("html", "")) for h in (item.get("safeHtmlItem") or [])]
    return "\n".join(p for p in parts if p).strip()


def _subtitle_text(item):
    names = [(s.get("name") or "").strip() for s in (item.get("subtitles") or [])]
    return "\n".join(n for n in names if n).strip()


def _response(item):
    """(text, source) — HTML preferred, subtitles as fallback (e.g. images)."""
    text = _html_text(item)
    if text:
        return text, "html"
    text = _subtitle_text(item)
    if text:
        return text, "subtitles"
    return "", ""


def _messages(item):
    messages = []
    prompt = _prompt_text(item)
    if prompt:
        messages.append({"uuid": "prompt", "sender": "human",
                         "created_at": item.get("time"), "text": prompt})
    response, _ = _response(item)
    if response:
        messages.append({"uuid": "response", "sender": "assistant",
                         "created_at": item.get("time"), "text": response})
    return messages


# --------------------------------------------------------------------------
# Metadata rows & transcript
# --------------------------------------------------------------------------

def _row(index, item, messages):
    texts = [m["text"] for m in messages]
    tag = sensitivity.classify("\n".join(texts))
    return {
        "index": int(index),
        "uuid": item.get("time"),
        "name": _prompt_text(item) or (texts[0][:60] if texts else "") or "(untitled)",
        "created_at": item.get("time"),
        "updated_at": item.get("time"),
        "message_count": len(messages),
        "word_count": sum(len(t.split()) for t in texts),
        # A single activity has one timestamp, so there's no span to report.
        "duration": "—",
        "duration_seconds": 0.0,
        "sensitivity": tag["tag"],
        "sensitivity_rank": tag["rank"],
        "sensitivity_reasons": tag["reasons"],
    }


def build_metadata(file_path):
    raw = load_raw(file_path)
    if not isinstance(raw, list):
        raise ValueError("A Gemini conversations.json should be a list of activities.")
    return [
        _row(index, item, _messages(item))
        for index, item in enumerate(list_conversations(raw))
    ]


def build_transcript(file_path, conversation_index):
    conversations = list_conversations(load_raw(file_path))
    if not 0 <= conversation_index < len(conversations):
        return {"name": "", "messages": []}
    item = conversations[conversation_index]
    messages = [
        {**m, "segments": sensitivity.segment(m["text"])}
        for m in _messages(item)
    ]
    return {"name": _prompt_text(item) or "(untitled)", "messages": messages}


# --------------------------------------------------------------------------
# Editing — blob is {title, safeHtmlItem, subtitles} (only the keys the item has)
# --------------------------------------------------------------------------

_EDITABLE = ("title", "safeHtmlItem", "subtitles")


def _find(raw, conversation_id):
    return next((c for c in raw if c.get("time") == conversation_id), None)


def _row_index(raw, item):
    index = 0
    for candidate in raw:
        if candidate is item:
            return index
        if candidate.get("time"):
            index += 1
    return index


def conversation_messages(file_path, conversation_id):
    item = _find(load_raw(file_path), conversation_id)
    if item is None:
        return None
    return {k: copy.deepcopy(item[k]) for k in _EDITABLE if k in item}


def set_messages(file_path, conversation_id, blob):
    raw = load_raw(file_path)
    item = _find(raw, conversation_id)
    if item is None:
        raise EditError("That conversation is no longer in the file.")

    for key in _EDITABLE:
        if key in blob:
            item[key] = blob[key]
    with open(file_path, "w") as f:
        json.dump(raw, f, ensure_ascii=False)

    return _row(_row_index(raw, item), item, _messages(item))


def _flagged(text):
    return {seg["text"] for seg in sensitivity.segment(text) if seg.get("tier")}


def _nth_index(text, sub, n):
    start = idx = 0
    for _ in range(n + 1):
        idx = text.find(sub, start)
        if idx < 0:
            return -1
        start = idx + len(sub)
    return idx


def _set_prompt(blob, new_text):
    blob["title"] = _prompt_prefix(blob) + new_text


def _set_response_plain(blob, new_text):
    """Collapse the response to a single plain-text slot (used by text deletion)."""
    if "safeHtmlItem" in blob:
        blob["safeHtmlItem"] = [{"html": html_lib.escape(new_text)}]
    elif "subtitles" in blob:
        blob["subtitles"] = [{"name": new_text}]


def redact_in_message(blob, message_uuid, target):
    """Remove every copy of a flagged run from the prompt or the response."""
    if not target:
        raise EditError("Nothing to redact.")
    blob = copy.deepcopy(blob)

    if message_uuid == "prompt":
        text = _prompt_text(blob)
        if target not in _flagged(text):
            raise EditError("That text isn't currently flagged in this message.")
        _set_prompt(blob, text.replace(target, ""))
    elif message_uuid == "response":
        text, source = _response(blob)
        if target not in _flagged(text):
            raise EditError("That text isn't currently flagged in this message.")
        if source == "html":
            # In-place so the response keeps its markup where possible.
            for item in blob.get("safeHtmlItem", []):
                if isinstance(item.get("html"), str):
                    item["html"] = item["html"].replace(target, "")
        else:
            for item in blob.get("subtitles", []):
                if isinstance(item.get("name"), str):
                    item["name"] = item["name"].replace(target, "")
    else:
        raise EditError("That message is no longer in the conversation.")

    return blob


def delete_text_in_message(blob, message_uuid, selected, occurrence=0):
    """Remove one highlighted run from the prompt or the response."""
    if not selected:
        raise EditError("No text selected.")
    try:
        occurrence = max(0, int(occurrence))
    except (TypeError, ValueError):
        occurrence = 0
    blob = copy.deepcopy(blob)

    if message_uuid == "prompt":
        text = _prompt_text(blob)
    elif message_uuid == "response":
        text, _ = _response(blob)
    else:
        raise EditError("That message is no longer in the conversation.")

    idx = _nth_index(text, selected, occurrence)
    if idx < 0:
        raise EditError("That selection no longer matches the message text.")
    new_text = text[:idx] + text[idx + len(selected):]

    if message_uuid == "prompt":
        _set_prompt(blob, new_text)
    else:
        _set_response_plain(blob, new_text)
    return blob


def remove_messages(blob, message_uuids):
    """Clear whole derived messages. Returns ``(new_blob, removed_count)``."""
    ids = {m for m in message_uuids if m}
    if not ids:
        raise EditError("No messages selected.")
    blob = copy.deepcopy(blob)

    present = ids & {m["uuid"] for m in _messages(blob)}
    if not present:
        raise EditError("None of those messages are in the conversation.")

    for message_uuid in present:
        if message_uuid == "prompt":
            blob["title"] = ""  # name falls back to the response snippet
        else:
            if "safeHtmlItem" in blob:
                blob["safeHtmlItem"] = []
            if "subtitles" in blob:
                blob["subtitles"] = []

    return blob, len(present)
