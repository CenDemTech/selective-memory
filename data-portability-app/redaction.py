"""Persistent redaction markers.

A redacted run of text is kept in the working copy wrapped in a pair of
Private-Use-Area sentinels (``\\uE000 … \\uE001``). That lets the run be shown
back to the user as a greyed-out box on *every* load — so a redaction is a
visible, permanent record of what was taken out — while still being:

* stripped out of the exported file (``strip`` removes the markers and their
  content, so nothing sensitive reaches the download), and
* ignored by the sensitivity classifier and word counts (which run on the
  stripped text), so redacting every flagged run turns a conversation Green.

The sentinels are characters no real export would contain. They never reach the
browser (the transcript splits them into ``segments``, and the plain ``text`` it
returns has the markers removed) and never reach the download (the build strips
them).
"""

OPEN = ""
CLOSE = ""


def wrap(text):
    """Wrap a run so it renders as a redaction box and exports as nothing."""
    return OPEN + text + CLOSE


def has_markers(text):
    return isinstance(text, str) and OPEN in text


def split(text):
    """Yield ``(chunk, is_redacted)`` pieces across ``text``, in order.

    Redacted chunks are the text between a matched ``OPEN``/``CLOSE`` pair;
    everything else is visible. An unmatched ``OPEN`` (shouldn't happen) is
    treated as visible text so nothing is silently dropped.
    """
    pos = 0
    n = len(text)
    while pos < n:
        start = text.find(OPEN, pos)
        if start < 0:
            yield text[pos:], False
            return
        end = text.find(CLOSE, start + 1)
        if end < 0:
            yield text[pos:], False
            return
        if start > pos:
            yield text[pos:start], False
        yield text[start + 1:end], True
        pos = end + 1


def strip(text):
    """Drop redaction markers *and* their content — what the export sees."""
    if not has_markers(text):
        return text
    return "".join(chunk for chunk, redacted in split(text) if not redacted)


def unwrap(text):
    """Drop only the markers, keeping the content — the display text.

    The concatenation of a transcript's ``segments`` equals this, so the two
    stay in lockstep for the client.
    """
    if not has_markers(text):
        return text
    return "".join(chunk for chunk, _ in split(text))


def wrap_target(text, target):
    """Wrap every visible copy of ``target``, leaving existing redactions intact.

    Used by redaction, which removes *all* copies of a flagged run from a
    message. Copies already inside a redaction box are left alone (re-wrapping
    would nest markers).
    """
    if not target:
        return text
    out = []
    for chunk, redacted in split(text):
        if redacted:
            out.append(wrap(chunk))
        else:
            out.append(chunk.replace(target, wrap(target)))
    return "".join(out)


def wrap_occurrence(text, target, occurrence):
    """Wrap the n-th (0-based) visible copy of ``target``.

    Returns ``(new_text, wrapped)``. Occurrences are counted over the visible
    chunks only and non-overlapping, matching how the client counts them over
    the rendered text (where markers are absent). ``wrapped`` is False when the
    occurrence wasn't found, so the caller can reject a stale selection.
    """
    if not target:
        return text, False
    seen = 0
    wrapped = False
    out = []
    for chunk, redacted in split(text):
        if redacted:
            out.append(wrap(chunk))
            continue
        rebuilt = []
        start = 0
        while True:
            idx = chunk.find(target, start)
            if idx < 0:
                rebuilt.append(chunk[start:])
                break
            rebuilt.append(chunk[start:idx])
            if seen == occurrence:
                rebuilt.append(wrap(target))
                wrapped = True
            else:
                rebuilt.append(target)
            seen += 1
            start = idx + len(target)
        out.append("".join(rebuilt))
    return "".join(out), wrapped


def display_segments(text, segment_plain):
    """Segments for one message: redacted runs plus flagged runs.

    ``segment_plain`` is ``sensitivity.segment`` (passed in to avoid an import
    cycle). It runs only on the visible chunks, so already-redacted text is
    never re-flagged, and the concatenation of the returned segment texts equals
    ``unwrap(text)``.
    """
    if not text:
        return []
    if not has_markers(text):
        return segment_plain(text)
    out = []
    for chunk, redacted in split(text):
        if not chunk:
            continue
        if redacted:
            out.append({"text": chunk, "redacted": True})
        else:
            out.extend(segment_plain(chunk))
    return out
