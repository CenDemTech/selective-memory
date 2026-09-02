# Conversation Filter

A local mini app for filtering AI chat exports. Bring a `conversations.json`
file, review every conversation in a table, select the ones you care about, and
download a new JSON file with **the same structure** as the one you uploaded.

Claude and ChatGPT (OpenAI) exports are supported today. A Gemini parser exists
in the tree but isn't offered on the platform screen yet.

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

## The steps

Four steps, four pages — **Platform selection · Upload · Edit · Export** — named
in a chip row under each heading; completed steps keep a green tick, and the chip
for the current page reads active. The page number is the chip number.

1. **Platform selection** — pick which service the export came from: **Anthropic**
   (Claude) or **Open AI** (ChatGPT). Choosing one advances to Upload.
2. **Upload** — a card naming the platform it will be read as, then a large
   dropzone: drop your `conversations.json` or **browse files**. **Back** returns
   to the platform cards. On a successful parse it advances to Edit.
3. **Edit** — a sortable, searchable table of every conversation with its name,
   start time, last update, message count, word count, duration, and a
   **Conversation sensitivity** tag (see below), filterable by tier. Each row has
   a **Review** button that opens the full transcript in a modal for redacting
   and deleting. Tick the rows you want to keep, then **Review .json file to be
   exported**. **Upload a different file** returns to the Upload page.
4. **Export** — a read-only review titled **Review your conversations to
   export**: the ticked conversations with their final sensitivity labels plus
   two tally columns, **Redactions** and **Msgs deleted**, so the shape of each
   conversation is visible before committing. **Export (.json) conversation
   file** builds it and reveals the filename, conversation count, size, and a
   **Download .json** button. **Back** returns to the table with the selection
   intact.

### The upload page

It restates the platform you picked above the dropzone, so a wrong choice is
obvious before a file is committed to it. Its **Back** button returns to the
platform cards. The page is also reached from the Edit page's **Upload a
different file**, to swap the file for a fresh one.

Swapping a file has to leave the current session working until the new one is
good: the previous session's token is held until the new file has parsed, and
only then is the old working directory reset. A rejected file renders its error
on the page and the existing conversations stay editable.

There is no separate export-vs-remove choice: ticking a row includes it. The
backend still supports a `remove` build mode over `/api/build` — the UI just
doesn't drive it.

### Selecting rows

Three ways, sharing one selection set: click a row, **shift-click** to extend a
range from the last plain click, or **press and drag** down the rows. Dragging
past the top or bottom edge scrolls the table under the cursor, so a run longer
than the viewport can be taken in one gesture — the scroll speed rises with how
far past the edge you push.

Two things that behavior forced. A drag repaints rows *in place* rather than
re-rendering, because rebuilding the table mid-gesture would swap the element out
from under the pointer; the range is recomputed from a snapshot taken at
mousedown, so dragging back up releases the rows it passed. And the click the
browser fires after a drag has to be swallowed, since it belongs to the gesture
rather than to a row — but a flag that simply stayed armed would eat an unrelated
click later, because *a drag released outside the table produces no click at
all*. The guard is one-shot and expires on its own.

### Deleting conversations

Each row carries a **trashcan**, and a **Delete** button above the table removes
every selected conversation at once. Both hit `/api/delete_conversations` — this
is the "or the whole conversation" half of the page's promise, not just a filter.

It's a **soft delete**, and undoable. Rather than rewriting the file (which
renumbered every following row and couldn't be reversed), the uuids are added to
the session's `deleted` set: the conversation stays in the working file, so its
row becomes a **greyed tombstone in place** — struck-through name, no checkbox, no
actions, a *Deleted* tag — and it's simply left out of the export. Because indices
never move, the client just flags the affected rows and repaints them rather than
re-rendering the whole table.

**Undo / redo, beside the Delete button.** A pair of hooked-arrow buttons (like
the review modal's) undo and redo whole-conversation deletions:
`/api/undo_delete` pulls the last delete action's uuids back out of the set (the
tombstones turn back into live rows), `/api/redo_delete` re-applies it, and a
fresh delete drops the redo stack. There's no confirm — the undo button is the
safety net. The footer's count carries a `· N deleted` tail while any tombstones
are present.

The Edit page's lede names the uploaded file but deliberately carries no count:
deleting would leave one stale, and the footer already reports the total.

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

Opening **Review** on a Yellow or Red conversation highlights the
exact runs that triggered it, tinted by tier, with the category on hover. The
modal subtitle repeats the tag and its categories. Both sides of the transcript
are highlighted, and both tiers are shown even in a Red conversation — the
yellow signals are still worth seeing.

A **highlight dropdown** sits next to the modal's close button. It opens showing
the total ("4 highlights") and lists every flagged run; picking one parks on it
(the current highlight gets a ring) and enables **Redact**. Enter and
Shift+Enter still step through them, keeping the dropdown in sync, and both wrap
at either end. When a conversation has no highlights at all the dropdown and its
buttons are hidden entirely.

Jumps are instant rather than smoothly scrolled. Smooth scrolling is
animation-frame driven, so it silently does nothing in a throttled or
backgrounded tab — and a find bar should land immediately anyway.

## Redacting flagged text

In Edit mode, **Redact all highlighted** (amber, beside the highlight dropdown)
redacts *every* detected highlight in the conversation at once. To redact just
one, **click it** — selecting a sensitivity highlight (edit mode only) rings it
and pops a floating **Delete** button above it; one click redacts that run.
Clicking the selected highlight again unclicks it. Either way each run turns into
a **greyed-out box** in place — the text stays visible so you can see what you
took out — the highlight list shrinks, and the conversation's row in the table
picks up a blue tint, a left accent bar, and a count chip so modified
conversations are obvious at a glance. Redactions ride into the file you build
afterwards, stripped out.

### A redaction is a persistent, visible record

Redacting doesn't just delete the text — it **keeps it in the working copy,
wrapped in a pair of Private-Use-Area sentinels** (`U+E000 … U+E001`), and the
transcript hands that run back as a `redacted` segment on every load. So the grey
box is permanent: it survives closing and reopening the conversation, and
undo/redo toggles it (undo turns the box back into an ordinary flagged run). All
of the marker handling lives in [`redaction.py`](redaction.py); the parsers call
into it so Claude and ChatGPT behave identically.

The sentinels never leak. They're characters no real export contains, they're
stripped from the plain `text` the transcript returns (so the client only ever
sees the run inside a box), and — the important one — **`export_conversation`
removes the markers and their content at build time**, so the downloaded file
carries nothing that was redacted. The classifier and word count run on that same
stripped text, which is why redacting every flagged run in a Red conversation
still turns it Green.

Three more things worth knowing about how it behaves:

**Only flagged text can be redacted.** `/api/redact` re-derives the message's
*visible* segments server-side and refuses anything that isn't currently a
flagged run — and an already-redacted run isn't visible, so it can't be redacted
twice. That keeps it a redaction tool rather than a general find-and-delete
endpoint that happens to be reachable over HTTP.

**Every copy inside that message is boxed**, across both `text` and the
`content` blocks. Those two fields duplicate each other in Claude exports, so
leaving the string in either one would keep the secret in the downloaded file. By
the same logic, if a key appears twice in one message, both copies are boxed.

**It writes to the working copy, not your file.** Redaction rewrites the
session's working copy in the temp directory; the file you uploaded from is never
touched. A build made before a redaction is discarded rather than served with the
old text. A redaction can be reversed with the modal's undo button (see [Undo /
redo](#undo--redo)), and re-uploading resets everything.

The row's other metadata is recomputed from the server's response, so the word
count drops and the sensitivity tag can fall — redact every flagged run in a Red
conversation and it turns Green.

## Review vs. Edit

The Review modal opens **read-only**: you see the transcript, the highlight
finder, undo/redo, and the revision count — nothing that changes the
conversation. An **Edit** button in the header (always present, on every
conversation, green ones included) swaps itself out for the editing tools: the
**Redact all highlighted** button, the **Highlight mode** toggle, the per-turn
checkboxes and trash icons (per-message ones appear on hover), and the footer's
**Delete Entire Conversation** / **Save Changes**. It's a single `editing` class
on the modal; opening a conversation always starts in Review. The highlights
themselves stay visible in Review — they show what's flagged — but nothing that
would alter the text is shown until you ask for it.

**Leaving edit mode.** While editing, the close **✕** is replaced by **✕ Exit
Edit**, so you have to step out of editing before the conversation can be closed
(Escape and a backdrop click exit edit mode too, rather than closing). Two ways
out, both keeping the review window open:

- **Save Changes** keeps this session's edits and drops back to Review.
- **✕ Exit Edit** also drops back to Review, but if you made edits without
  saving it first asks *"You made changes to the conversation without saving. Are
  you sure you want to exit without saving?"* — and on confirm it **rolls those
  edits back** to the state you entered edit mode on (undoing to the baseline),
  since every edit is otherwise written straight to the working copy. Exiting
  with nothing changed doesn't prompt.

**Highlight mode** (a toggle, edit-only) lets you redact text the classifier
didn't flag: with it on, drag-select any run inside a message — the selection
turns **red** — and a floating **Delete** button appears just above it. One click
redacts exactly that run (boxed like any redaction). With the toggle off,
selecting text does nothing. This is separate from **Redact all highlighted**,
which boxes the classifier's detected runs in one go.

## Deleting messages and turns

The transcript is grouped into **turns** — a prompt and the reply (or replies)
that follow it, under a "Turn N" divider. In Edit mode every turn *and* every
message carries a **checkbox** and a **trash icon**, sitting at the far end of the
header row.

There are three ways to remove content from a transcript:

- **Delete a message or a whole turn now.** The trash icon on a message removes
  it; the trash icon on a turn removes every message the turn still holds. A
  deleted message leaves a **tombstone** — *"This message has been deleted."* — in
  place, so the removal reads clearly; the message is gone from the working copy
  immediately, and **undo** (see below) brings it back on the next reload.
- **Select, then delete a batch.** Ticking message checkboxes builds a selection;
  a turn checkbox ticks all of that turn's live messages at once and reads
  checked only when they all are. The footer's destructive button does double
  duty — **Delete Entire Conversation** by default, switching to **Delete N
  messages** while any are selected — so the two never compete for the same slot.
- **Redact any text.** To remove a run the classifier didn't flag, turn on
  **Highlight mode** and drag-select it — the selection turns red, a floating
  **Delete** button appears above it, and one click redacts exactly that run.
  Under the hood this is `/api/delete_text`: when the selected string appears more
  than once, the run is matched by *occurrence* (an encoding-agnostic count of
  identical copies before it, sidestepping the UTF-16 vs code-point mismatch), and
  the box is stripped from both `text` and the mirroring content blocks at export
  so nothing leaks.

The footer's **Delete Entire Conversation** removes the whole open conversation
(the same not-undoable path as the table's trashcan, so it confirms first) and
closes
the modal.

All of these share the redaction machinery — they rewrite the session's working
copy (never the uploaded file), re-render the transcript, invalidate any file
built beforehand, and recompute the row (so message count and the sensitivity tag
move, and deleting every message leaves the conversation present but empty).

A modified row shows a blue count pill totalling **all** its edits, and directly
beneath the conversation name a line spelling out the breakdown — e.g. pill `2`
with "1 redaction · 1 message deleted", or "2 messages deleted" on its own. The
breakdown line stays narrower than the longest conversation name, so it never
widens the table. In the modal footer the same total reads as a **revision
count** ("4 revisions"), with the breakdown on hover.

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

The app uses a **dark theme**: a near-black navy page with slightly lifted navy
panels, white text, and a single blue accent carrying every primary action. The
palette lives at the top of [`static/style.css`](static/style.css) as brand
tokens (`--blue`, `--blue-bright`, `--blue-soft`) plus a block of dark
surface/text/border tokens, with every role token derived from them. No component
hardcodes a colour, so re-theming — including flipping to a light theme — means
editing that one block. Type is a system sans stack (`--font`), with a mono stack
(`--font-mono`) reserved for filenames and code.

How the colours are used:

| Token | Where |
| --- | --- |
| `--bg` (`#080D1B`) | Near-black navy page background |
| `--surface` | Cards, table, modal |
| `--surface-2` | Elevated: sticky table header, your own message bubbles |
| `--surface-3` | Highest: row actions, secondary buttons |
| `--ink` | Headings and body text, at full / `.64` / `.40` alpha |
| `--blue` | Primary CTAs (Review .json file to be exported, Export (.json) conversation file, the Edit button, Save Changes, Download), the active step chip, and the edit-count badge |
| `--blue-bright` | Links, inline `code`, the assistant's speaker label, the edited-row accent |
| `--ok` | The green tick on completed step chips and on the privacy footnote |

Three things the dark background forced, all flagged in the CSS:

- The **sensitivity tags and transcript highlights** keep green/amber/red —
  they're a traffic light — but as *translucent fills with a bright text tone*
  rather than light pastels. They live in a separate "semantic" block so the
  distinction stays explicit. The single-run **Redact** button borrows the amber
  (`--warn`) tone so it reads as acting on the yellow-flagged run beside it.
- `--danger` splits in two: a **bright tone** for text/icons on dark (the row
  trashcans, the message/turn trash icons, and the outlined **Delete** buttons),
  and `--danger-solid` for saturated fills and selection outlines that carry
  white text.
- **Edited rows use the blue family** (wash + left edge + count badge). Selection
  uses a blue wash too, so selection wins on fill while the left edge keeps
  saying "modified".

`color-scheme: dark` is set so native controls — checkboxes, scrollbars, the
search field's clear button — render dark to match.

## Files

| File | What it does |
| --- | --- |
| `app.py` | Flask routes: upload, transcript, redact, delete_text, delete_messages, delete_conversations, undo_delete, redo_delete, undo, redo, build, download, reset |
| `claude_parser.py` | Claude parsing (pandas) plus UI-shaping helpers |
| `openai_parser.py` | ChatGPT parsing — mapping-graph traversal and edits |
| `sensitivity.py` | Green/Yellow/Red rules, gates, and validators |
| `redaction.py` | Redaction markers: wrap, strip for export, split into display segments |
| `templates/index.html` | All four pages, plus the review and download modals |
| `static/app.js` | Page transitions, upload, table, selection, transcript, build/download |
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
