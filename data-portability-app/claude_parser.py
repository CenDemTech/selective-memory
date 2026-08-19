"""Parsing helpers for Claude ``conversations.json`` exports.

The two core functions below (``claude_json_to_dataframe`` and
``get_claude_conversation_text``) use the pandas logic supplied for this
project. Everything else in this module is a thin layer on top of them that
shapes the results for the web UI.
"""

import copy
import json

import pandas as pd

import sensitivity


# --------------------------------------------------------------------------
# Core pandas logic
# --------------------------------------------------------------------------

def claude_json_to_dataframe(file_path):
    """Load a Claude conversations.json file into a DataFrame of conversations."""
    with open(file_path, "r") as f:
        data = json.load(f)
    df = pd.DataFrame.from_dict(data)
    df = df[df["name"] != ""]
    df.reset_index(drop=True, inplace=True)
    return df


def get_claude_conversation_text(file_path):
    """Explode every conversation into one row per chat message."""
    df = claude_json_to_dataframe(file_path)
    df_exploded = df.explode("chat_messages")
    messages_df = pd.json_normalize(df_exploded["chat_messages"])
    messages_df["chat_name"] = df_exploded["name"].values
    conv_id = messages_df.index
    messages_df.reset_index(drop=True, inplace=True)
    messages_df["conversation_id"] = conv_id

    # ``pd.json_normalize`` hands back a fresh 0..n-1 index, so the
    # ``conversation_id`` above ends up numbering *messages*, not
    # conversations. Carry the exploded frame's index across as well so
    # messages can actually be grouped back onto their parent conversation.
    messages_df["conversation_index"] = df_exploded.index.values
    return messages_df


# --------------------------------------------------------------------------
# Helpers for the web UI
# --------------------------------------------------------------------------

def _clean(value):
    """Turn pandas' NaN placeholders into None so the result is JSON-safe."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    return value


def _message_text(row):
    """Best-effort plain text for a single chat message row."""
    text = row.get("text")
    if isinstance(text, str) and text.strip():
        return text

    parts = []
    content = row.get("content")
    if isinstance(content, list):
        for block in content:
            if not isinstance(block, dict):
                continue
            if isinstance(block.get("text"), str):
                parts.append(block["text"])
            elif block.get("type"):
                parts.append(f"[{block['type']}]")
    return "\n\n".join(p for p in parts if p)


def _duration(timestamps):
    """Span between the first and last message, as (label, seconds)."""
    stamps = pd.to_datetime(pd.Series(timestamps), errors="coerce", utc=True).dropna()
    if len(stamps) < 2:
        return "—", 0.0

    seconds = (stamps.max() - stamps.min()).total_seconds()
    if seconds < 60:
        label = f"{int(seconds)}s"
    elif seconds < 3600:
        label = f"{int(seconds // 60)}m"
    elif seconds < 86400:
        hours, minutes = divmod(int(seconds // 60), 60)
        label = f"{hours}h {minutes}m" if minutes else f"{hours}h"
    else:
        days = int(seconds // 86400)
        hours = int((seconds % 86400) // 3600)
        label = f"{days}d {hours}h" if hours else f"{days}d"
    return label, float(seconds)


# The key that identifies a conversation, and the ordered list of the ones the
# table shows — the app uses these generically so it can treat any platform's
# parser the same way.
ID_FIELD = "uuid"


def list_conversations(raw):
    """Conversations the table lists, in order (blank-named ones dropped)."""
    return [c for c in raw if c.get("name") != ""]


def build_metadata(file_path):
    """Return one metadata record per (named) conversation, for the table view."""
    df = claude_json_to_dataframe(file_path)
    messages_df = get_claude_conversation_text(file_path)

    grouped = {}
    if not messages_df.empty:
        for index, group in messages_df.groupby("conversation_index"):
            grouped[index] = group.to_dict("records")

    return [
        build_record(index, row, grouped.get(index, []))
        for index, row in df.iterrows()
    ]


def build_record(index, conversation, messages):
    """One row of table metadata.

    Shared by the initial pandas load and by the single-conversation recompute
    after a redaction, so the two can't drift apart. ``conversation`` supplies
    the conversation-level fields (a DataFrame row or a raw dict) and
    ``messages`` the per-message dicts (from ``pd.json_normalize`` or straight
    from the JSON) — both are read through ``.get``.
    """
    # `explode` leaves an all-NaN row behind for a conversation with no messages.
    real = [m for m in messages
            if _clean(m.get("uuid")) is not None or _message_text(m)]
    texts = [_message_text(m) for m in real]
    duration, duration_seconds = _duration([_clean(m.get("created_at")) for m in real])

    # Sensitivity covers the whole conversation — the user's disclosures and the
    # model's replies alike, since a leaked key or PII in the AI output matters
    # just as much. This is exactly the text the transcript highlights, so the
    # tag and the highlights always agree. The title is left out: Claude
    # generates it, it isn't shown as a highlightable message, and scanning it
    # could tag a conversation with nothing highlighted to point at.
    tag = sensitivity.classify("\n".join(texts))

    return {
        "index": int(index),
        "uuid": _clean(conversation.get("uuid")),
        "name": _clean(conversation.get("name")) or "(untitled)",
        "created_at": _clean(conversation.get("created_at")),
        "updated_at": _clean(conversation.get("updated_at")),
        "message_count": len(real),
        "word_count": sum(len(t.split()) for t in texts),
        "duration": duration,
        "duration_seconds": duration_seconds,
        "sensitivity": tag["tag"],
        "sensitivity_rank": tag["rank"],
        "sensitivity_reasons": tag["reasons"],
    }


def build_transcript(file_path, conversation_index):
    """Return the ordered message list for one conversation."""
    messages_df = get_claude_conversation_text(file_path)
    if messages_df.empty:
        return {"name": "", "messages": []}

    group = messages_df[messages_df["conversation_index"] == conversation_index]
    if group.empty:
        return {"name": "", "messages": []}

    name = group["chat_name"].iloc[0]
    messages = []
    for _, row in group.iterrows():
        record = row.to_dict()
        text = _message_text(record)
        if _clean(record.get("uuid")) is None and not text:
            continue  # conversation with no messages -> explode left a NaN row

        messages.append(
            {
                "uuid": _clean(record.get("uuid")),
                "sender": _clean(record.get("sender")) or "unknown",
                "created_at": _clean(record.get("created_at")),
                "text": text,
                # Every message is scanned for display, the model's replies
                # included. Note this is deliberately wider than the tag, which
                # ``build_metadata`` decides from the user's messages alone.
                # Pre-sliced runs so the client can highlight what was flagged
                # without doing any offset arithmetic of its own.
                "segments": sensitivity.segment(text),
            }
        )

    return {"name": name, "messages": messages}


def load_raw(file_path):
    """The untouched parsed JSON, used to rebuild an identically shaped export."""
    with open(file_path, "r") as f:
        return json.load(f)


class EditError(Exception):
    """An in-place edit (redaction or deletion) couldn't be applied."""


# Kept under the old name too so existing callers/imports don't break.
RedactionError = EditError


def _row_index(raw, conversation):
    """The conversation's position in the *named-only* frame the table shows.

    Row indices follow ``build_metadata``, which drops blank-named conversations,
    so an edited row's index has to be counted the same way.
    """
    index = 0
    for candidate in raw:
        if candidate is conversation:
            return index
        if candidate.get("name") != "":
            index += 1
    return index


# --------------------------------------------------------------------------
# Edit transforms
#
# These return a *new* message list rather than touching the file, so the app
# can snapshot each state for undo/redo before committing it with
# ``set_messages``. The file only ever changes through ``set_messages``.
# --------------------------------------------------------------------------

def redact_in_message(messages, message_uuid, target):
    """Return a copy of ``messages`` with ``target`` scrubbed from one message.

    Every copy of ``target`` inside that message goes, across both ``text`` and
    the ``content`` blocks — the two duplicate each other in Claude exports, so
    removing it from only one would leave the secret in the download. Only text
    the classifier currently flags may be removed; this is a redaction tool, not
    a general find-and-delete.
    """
    if not target:
        raise EditError("Nothing to redact.")

    result = copy.deepcopy(messages)
    message = next((m for m in result if m.get("uuid") == message_uuid), None)
    if message is None:
        raise EditError("That message is no longer in the conversation.")

    flagged = {seg["text"] for seg in sensitivity.segment(_message_text(message))
               if seg.get("tier")}
    if target not in flagged:
        raise EditError("That text isn't currently flagged in this message.")

    if isinstance(message.get("text"), str):
        message["text"] = message["text"].replace(target, "")
    for block in message.get("content") or []:
        if isinstance(block, dict) and isinstance(block.get("text"), str):
            block["text"] = block["text"].replace(target, "")

    return result


def remove_messages(messages, message_uuids):
    """Return ``(kept, removed_count)`` — messages minus any whose uuid matches."""
    ids = {u for u in message_uuids if u}
    if not ids:
        raise EditError("No messages selected.")
    kept = [m for m in messages if m.get("uuid") not in ids]
    if len(kept) == len(messages):
        raise EditError("None of those messages are in the conversation.")
    return kept, len(messages) - len(kept)


def _nth_index(text, sub, n):
    """Index of the n-th (0-based) non-overlapping occurrence of ``sub``, or -1.

    Non-overlapping to match how the client counts occurrences (a plain split),
    so the two agree on *which* copy of a repeated selection is meant.
    """
    start = idx = 0
    for _ in range(n + 1):
        idx = text.find(sub, start)
        if idx < 0:
            return -1
        start = idx + len(sub)
    return idx


def _sync_content(message, new_text, removed):
    """Keep a message's content blocks consistent with its edited text.

    The common Claude shape is a single text block mirroring ``text`` — that one
    is set exactly. With several blocks we strip ``removed`` from each instead,
    which is leak-safe even if it clears more than the single selected copy.
    """
    text_blocks = [b for b in (message.get("content") or [])
                   if isinstance(b, dict) and isinstance(b.get("text"), str)]
    if len(text_blocks) == 1:
        text_blocks[0]["text"] = new_text
    else:
        for block in text_blocks:
            block["text"] = block["text"].replace(removed, "")


def delete_text_in_message(messages, message_uuid, selected, occurrence=0):
    """Return a copy of ``messages`` with one highlighted run of text removed.

    Unlike ``redact_in_message`` this removes *any* selected text, not only what
    the classifier flagged. ``occurrence`` (the count of identical copies before
    the selection) disambiguates when the string appears more than once, so only
    the copy the user actually highlighted is deleted rather than every match.
    """
    if not selected:
        raise EditError("No text selected.")
    try:
        occurrence = max(0, int(occurrence))
    except (TypeError, ValueError):
        occurrence = 0

    result = copy.deepcopy(messages)
    message = next((m for m in result if m.get("uuid") == message_uuid), None)
    if message is None:
        raise EditError("That message is no longer in the conversation.")

    plain = _message_text(message)
    idx = _nth_index(plain, selected, occurrence)
    if idx < 0:
        raise EditError("That selection no longer matches the message text.")
    new_plain = plain[:idx] + plain[idx + len(selected):]

    # `text` is what _message_text returns when it's a non-empty string; leave an
    # empty text field empty so the content fallback keeps driving the display.
    if isinstance(message.get("text"), str) and message["text"].strip():
        message["text"] = new_plain
    _sync_content(message, new_plain, selected)
    return result


def set_messages(file_path, conversation_uuid, messages):
    """Replace one conversation's messages in the working copy and rewrite it.

    Only the app's temp copy is touched; the uploaded file is never written to.
    Returns the conversation's refreshed metadata row.
    """
    raw = load_raw(file_path)
    conversation = next((c for c in raw if c.get("uuid") == conversation_uuid), None)
    if conversation is None:
        raise EditError("That conversation is no longer in the file.")

    conversation["chat_messages"] = messages
    with open(file_path, "w") as f:
        json.dump(raw, f, ensure_ascii=False)

    return build_record(_row_index(raw, conversation), conversation, messages)


def conversation_messages(file_path, conversation_uuid):
    """The current message list for one conversation, or None if it's gone."""
    conversation = next(
        (c for c in load_raw(file_path) if c.get("uuid") == conversation_uuid), None
    )
    return None if conversation is None else (conversation.get("chat_messages") or [])
