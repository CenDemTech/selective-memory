# Conversation Filter

A local mini app for filtering AI chat exports. Bring a `conversations.json`
file, review every conversation in a table, select the ones you care about, and
download a new JSON file with **the same structure** as the one you uploaded.

Claude and ChatGPT (OpenAI) exports are supported today. Gemini is stubbed out
on the first screen and will be wired up later.

## Two formats, one app

The two exports are shaped very differently, so each has its own parser
([`claude_parser.py`](claude_parser.py), [`openai_parser.py`](openai_parser.py))
exposing the same interface; the app looks one up per session from `PARSERS` and
stays format-agnostic. Everything downstream — the table, sensitivity tagging,
the transcript highlighter, redaction/deletion, undo/redo, and the export/remove
build — works identically regardless of platform.

- **Claude**: a list of conversations, each with a flat `chat_messages` list.
- **ChatGPT**: a list of conversations, each with a `mapping` — a *graph* of
  message nodes linked by `parent`/`children`, plus a `current_node` marking the
  active leaf. The visible thread is the path from that leaf back to the root, so
  branches (edits, regenerations) are skipped. System, tool, and hidden nodes are
  filtered out, the tool-authored answer node is kept, and unix `create_time`
  floats are converted to ISO so the UI formats them uniformly. Editing operates
  on node ids; deleting a message re-links its children to its parent and moves
  `current_node` up if needed, so the exported graph stays valid.

Each parser exposes `ID_FIELD` (`uuid` for Claude, `id` for ChatGPT) and
`list_conversations`, which is all the app needs to filter and index generically.

## Running it

```bash
./.venv/bin/python app.py
```

Then open http://127.0.0.1:5000.

First-time setup, if the virtualenv isn't there yet:

```bash
python3 -m venv .venv && ./.venv/bin/pip install -r requirements.txt
```

## The four pages

1. **Platform** — pick which service the export came from. Claude and ChatGPT are enabled; Gemini is coming.
2. **Upload** — drag and drop (or browse for) your `conversations.json`.
3. **Select** — a sortable, searchable table of every conversation with its name,
   start time, last update, message count, word count, duration, and a
   **Conversation sensitivity** tag (see below). Each row has a **See
   conversation text** button that opens the full human/assistant transcript in
   a modal. Tick any number of rows and an action bar slides up with the count
   and a **Continue** button.
4. **Export** — the two choices, as full cards rather than buttons:
   - **Export conversations** — build a file containing *only* the selected conversations.
   - **Remove conversations** — build a copy of the original file *without* the selected conversations.

   The selected conversations are listed as chips at the top so you can check
   them before committing. Choosing either card builds the file and reveals the
   filename, conversation count, size, and a **Download .json** button. Picking
   the other card rebuilds in place, and **Back to the table** returns to page 3
   with the selection intact.

## Conversation sensitivity

Every conversation is tagged **Green**, **Yellow**, or **Red** by
[`sensitivity.py`](sensitivity.py). There is no model in the loop — it is
lexical pattern matching: whitespace-normalised text, word-boundary regexes over
25 rules, and structural validators. Highest tier wins, so one red signal makes
the whole conversation red. Hovering a tag lists the categories that fired.

**The tag scans the whole conversation — your messages and the model's replies
alike.** Sensitive text is sensitive wherever it lands, so a conversation where
only Claude prints a live credential still reads Red. The one exclusion is the
conversation title: Claude generates it, and it isn't shown as a highlightable
message, so scanning it could tag a conversation with nothing to point at.

Because the tag and the transcript highlights are computed from the same text
(every message), they always agree: any highlighted run means a non-Green tag,
and a Green conversation never shows a highlight.

| Tag | Fires on |
| --- | --- |
| **Red** | User profile audits · regulated health/finance data · high-risk PII · live infrastructure secrets · malicious intent and exploits · legal and high-liability content |
| **Yellow** | Geographic and proximity queries · trackable indicators · memory retention requests · potential business ventures · interpersonal and professional communications · vague or indirect identifiers · corporate assets and IP · creative and copyrighted material |
| **Green** | Nothing matched |

Opening **See conversation text** on a Yellow or Red conversation highlights the
exact runs that triggered it, tinted by tier, with the category on hover. The
modal subtitle repeats the tag and its categories. Both sides of the transcript
are highlighted, and both tiers are shown even in a Red conversation — the
yellow signals are still worth seeing.

A **highlight finder** sits next to the modal's close button, working like a
browser's find bar. It opens showing the total ("4 highlights"), and the ↑ / ↓
buttons — or Enter and Shift+Enter — step through them, wrapping at either end.
The counter switches to "2 of 4" once you're navigating, and the current
highlight gets a ring so it's distinguishable from the rest. When a conversation
has no highlights at all the finder is hidden entirely.

Jumps are instant rather than smoothly scrolled. Smooth scrolling is
animation-frame driven, so it silently does nothing in a throttled or
backgrounded tab — and a find bar should land immediately anyway.

## Redacting flagged text

Select a highlight — click it, or arrow to it — and the finder's **Redact**
button deletes that text from the conversation. The transcript re-renders
without it, the highlight count drops, and the conversation's row in the table
picks up a lime tint, a left accent bar, and a count chip so modified
conversations are obvious at a glance. Redactions ride along into whichever file
you build afterwards, in both Export and Remove mode.

Three things worth knowing about how it behaves:

**Only flagged text can be deleted.** `/api/redact` re-derives the message's
segments server-side and refuses anything that isn't currently a flagged run.
That keeps it a redaction tool rather than a general find-and-delete endpoint
that happens to be reachable over HTTP.

**Every copy inside that message goes**, across both `text` and the `content`
blocks. Those two fields duplicate each other in Claude exports, so removing the
string from only one would leave the secret in the downloaded file. By the same
logic, if a key appears twice in one message, both copies go — leaving the
second behind because it was a separate match would defeat the point.

**It writes to the working copy, not your file.** Redaction rewrites the
session's working copy in the temp directory, which is what makes it show up in
the export; the file you uploaded from is never touched. A build made before a
redaction is discarded rather than served with the old text. A redaction can be
reversed with the modal's undo button (see [Undo / redo](#undo--redo)), and
re-uploading resets everything.

The row's other metadata is recomputed from the server's response, so the word
count drops and the sensitivity tag can fall — redact every flagged run in a Red
conversation and it turns Green.

## Advanced settings · deleting messages

The **Advanced settings** checkbox next to the search box unlocks message
editing. It's a mode indicator as much as a control: lowkey blue while off, red
once on, since it enables destructive actions. It exposes two ways to remove
content from a transcript:

- **Delete whole messages.** Every message grows a selection checkbox — click a
  message header to pick it (its bubble outlines red), pick as many as you like,
  then hit **Delete (N)** in the modal footer to remove them all at once.
- **Delete an arbitrary text selection.** Drag-select any run of text inside a
  message — the selection turns red — and a floating **✕ Delete** button appears
  just above it. One click removes exactly that text. When the selected string
  appears more than once in the message, only the copy you highlighted goes: the
  client sends the selection's *occurrence index* (an encoding-agnostic count of
  identical runs before it) rather than a raw offset, sidestepping the UTF-16 vs
  code-point mismatch, and the server removes that specific occurrence from both
  `text` and the mirroring content blocks so nothing leaks into the export. This
  is tallied as a redaction.

Both share the redaction machinery — they rewrite the session's working copy
(never the uploaded file), re-render the transcript, invalidate any file built
beforehand, and recompute the row (so message count and the sensitivity tag
move, and deleting every message leaves the conversation present but empty).

A modified row shows a lime count pill totalling **all** its edits, and directly
beneath the conversation name a line spelling out the breakdown — e.g. pill `2`
with "1 redaction · 1 message deleted", or "2 messages deleted" on its own. The
breakdown line stays narrower than the longest conversation name, so it never
widens the table.

### Undo / redo

The modal footer's **↶ / ↷** buttons (or Cmd/Ctrl+Z and Cmd/Ctrl+Shift+Z) undo
and redo edits to the open conversation — redactions and deletions alike. Since
every edit is persisted to the working copy so it reaches the export, the history
lives server-side: each conversation keeps a stack of message-list snapshots and
a cursor, every edit pushes a snapshot (carrying the cumulative edit tallies),
and undo/redo just move the cursor and rewrite the working file to match. A fresh
edit after an undo drops the redo branch, exactly like a text editor. Undo back
to the start and the row un-modifies itself. History is per conversation and
per session; re-uploading still resets everything.

The server sends *pre-sliced segments* rather than character offsets. Python
indexes strings by code point and JavaScript by UTF-16 code unit, so a single
emoji earlier in a message would shift every offset after it and misplace the
highlight; slicing server-side means the client only concatenates.

Regex alone produces too many false positives, so four validators back it up:

- **Luhn checksum** on candidate card numbers — `4111 1111 1111 1112` stays Green.
- **Octet ranges** on dotted quads — `192.168.1.50` is an IP, `1.2.3.4` is a version string.
- **Placeholder detection** on credential assignments — `api_key = "your-api-key-here"` stays Green, `api_key = "sk-proj-9f8Ab2Kd0LmQxZ71"` goes Red.
- **Capitalisation** for place names — "days in Lisbon" is a destination, "days in bed" and "days in December" are not.

Plus targeted negative lookbehinds where one word carries two senses: "at
startup" and "on server startup" are program lifecycle, not a business venture,
while "my startup" and "startup idea" still fire.

Each rule also carries *gates*: cheap lowercase literals that must be present
before its regex runs. Gates are a necessary condition of the pattern, never
merely a likely one — `test_gates_never_hide_a_match` re-runs the whole corpus
with gating disabled and fails if any tag changes. They make classification
about 4× faster (~8 MB of conversation text per second), because an ordinary
conversation skips nearly every rule.

**What it will and won't catch.** Being lexical, it keys on phrasing. It won't
recognise a sensitive topic described in words no rule anticipates, and it can't
identify a place name that isn't preceded by a travel phrase. Treat the tag as a
triage aid for finding conversations worth reviewing, not as a guarantee that
Green conversations are free of sensitive content.

## How the output stays structurally identical

The filtered file is rebuilt from the *original* parsed JSON objects, matched by
`uuid` — not regenerated from the pandas DataFrame. Every field on every kept
conversation (`account`, `chat_messages`, `attachments`, `files`, and anything
else Anthropic adds later) survives untouched. The only difference between input
and output is which conversations are in the list.

## Colour

The app uses a **dark theme**: a deep dark-blue page with the brand navy as the
panel surface and white text. The palette lives at the top of
[`static/style.css`](static/style.css) as brand tokens (`--navy`, `--lime`,
`--periwinkle`, `--pill-blue`) plus a block of dark surface/text/border tokens,
with every role token derived from them. No component hardcodes a colour, so
re-theming — including flipping back to a light theme — means editing that one
block.

How the colours are used:

| Token | Where |
| --- | --- |
| `--bg` (`#0E1A38`) | Deep dark-blue page background |
| `--surface` (brand `--navy`) | Cards, table, modal |
| `--surface-2` | Elevated: sticky table header, your own message bubbles |
| `--ink` (white) | Headings and body text, at full / `.66` / `.42` alpha |
| `--lime` | Primary CTAs (Continue, Download), and the redaction markers on a modified row |
| `--periwinkle` | Focus rings, drag-over state, spinner, active step dot, selected-row tint, Claude's bubbles |
| `--pill-blue` | Small light badges (file icon, Export icon) that pop against the dark |

Three things the dark background forced, all flagged in the CSS:

- The **sensitivity tags and transcript highlights** keep green/amber/red —
  they're a traffic light — but as *translucent fills with a bright text tone*
  rather than the light pastels a light theme could use. They live in a separate
  "semantic" block so the distinction stays explicit.
- `--danger` splits in two: a **bright tone** for text/icons on dark, and
  `--danger-solid` for saturated button fills (Remove, Redact) that carry white
  text.
- **Redacted rows use the lime family** (wash + edge + count badge) rather than
  blue, so "modified" stays distinct from "selected", which uses the periwinkle
  wash.

`color-scheme: dark` is set so native controls — checkboxes, scrollbars, the
search field's clear button — render dark to match.

## Files

| File | What it does |
| --- | --- |
| `app.py` | Flask routes: upload, transcript, redact, delete_text, delete_messages, undo, redo, build, download, reset |
| `claude_parser.py` | Claude parsing (pandas) plus UI-shaping helpers |
| `openai_parser.py` | ChatGPT parsing — mapping-graph traversal and edits |
| `sensitivity.py` | Green/Yellow/Red rules, gates, and validators |
| `templates/index.html` | All four pages and the transcript modal |
| `static/app.js` | Page transitions, table, selection, transcript, build/download |
| `static/style.css` | Styling |
| `sample_data/conversations.json` | Claude fixture covering the awkward cases |
| `sample_data/openai_conversations.json` | ChatGPT fixture (graph, tool nodes, a branch) |
| `test_app.py` | End-to-end checks over the sample fixture |
| `test_sensitivity.py` | Per-category classifier cases and the gate-safety check |
| `test_download_integrity.py` | Combinations of edits verified through to the downloaded file |
| `test_openai.py` | ChatGPT parsing, edits, and edits-through-to-download |

## Two behaviours worth knowing

**Untitled conversations are never listed.** The supplied parsing code filters
with `df[df["name"] != ""]`, so conversations with a blank name don't appear in
the table and can't be selected — *Export* leaves them out, *Remove* keeps them
in. Nothing in the UI mentions them or counts them.

**`conversation_id` in the supplied code numbers messages, not conversations.**
`pd.json_normalize` returns a fresh `RangeIndex`, so `messages_df.index` is
`0..n-1` over all messages rather than the parent conversation's index. The
supplied line is kept as-is; `claude_parser.py` adds a `conversation_index`
column alongside it that carries the real index across, and the app groups on
that.

## Privacy

Nothing leaves the machine. The upload is written to a per-session temp
directory, read by the local Flask process, and removed when you hit
`/api/reset` or the temp directory is cleared.
