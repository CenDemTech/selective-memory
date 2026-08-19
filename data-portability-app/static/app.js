/* Conversation Filter — front-end controller.
   Three pages, one in-memory table, and a build/download step at the end. */

const state = {
  token: null,
  filename: null,
  totalInFile: 0,
  conversations: [],      // every conversation, as returned by the backend
  visible: [],            // after the search filter
  selected: new Set(),    // conversation uuids
  tierFilter: new Set(),  // sensitivity tiers to show; empty = show all
  sort: { key: 'created_at', dir: 'desc' },
  mode: null,             // 'export' | 'remove', chosen right after upload
  lastBuild: null,
  advanced: false,          // per-message deletion inside a conversation
  msgSelection: new Set(),  // message uuids picked for the next mass-delete
};

const $ = (id) => document.getElementById(id);

/* Force a style recalculation so a class added right after unhiding an element
   still animates. requestAnimationFrame would do it too, but it never fires
   while the tab is backgrounded, which leaves the UI wedged mid-transition. */
const reflow = (el) => void el.offsetWidth;

/* ── Page transitions ─────────────────────────────────────────────── */

let currentPage = 1;

function goTo(step) {
  if (step === currentPage) return;
  const from = $(`page-${currentPage}`);
  const to = $(`page-${step}`);

  from.classList.add('is-leaving');
  from.classList.remove('is-active');
  setTimeout(() => from.classList.remove('is-leaving'), 400);

  to.scrollTop = 0;
  to.classList.add('is-active');  // CSS delays the fade-in until `from` has cleared

  currentPage = step;
  updateActionBar();  // the bar is fixed-position, so it only belongs on the table page
  document.querySelectorAll('#steps li').forEach((li) => {
    const n = Number(li.dataset.step);
    li.classList.toggle('active', n === step);
    li.classList.toggle('done', n < step);
  });
}

document.querySelectorAll('[data-goto]').forEach((btn) => {
  btn.addEventListener('click', () => goTo(Number(btn.dataset.goto)));
});

/* ── Page 1 · platform ────────────────────────────────────────────── */

const PLATFORM_NAMES = { claude: 'Claude', openai: 'ChatGPT' };

// What to call the non-human speaker in transcripts, per platform.
function assistantLabel() {
  return PLATFORM_NAMES[state.platform] || 'Assistant';
}

document.querySelectorAll('.platform[data-platform]').forEach((btn) => {
  btn.addEventListener('click', () => {
    state.platform = btn.dataset.platform;
    const named = PLATFORM_NAMES[state.platform];
    if (named) $('upload-lede').textContent = `From your ${named} data export. Drop it below or browse for it.`;
    goTo(2);
  });
});

/* ── Page 2 · upload ──────────────────────────────────────────────── */

const dropzone = $('dropzone');
const fileInput = $('file-input');

fileInput.addEventListener('change', () => {
  if (fileInput.files.length) upload(fileInput.files[0]);
});

['dragenter', 'dragover'].forEach((ev) =>
  dropzone.addEventListener(ev, (e) => {
    e.preventDefault();
    dropzone.classList.add('dragging');
  })
);
['dragleave', 'drop'].forEach((ev) =>
  dropzone.addEventListener(ev, (e) => {
    e.preventDefault();
    dropzone.classList.remove('dragging');
  })
);
dropzone.addEventListener('drop', (e) => {
  const file = e.dataTransfer.files[0];
  if (file) upload(file);
});

async function upload(file) {
  // Drop the previous upload's temp files before replacing it.
  if (state.token) {
    const stale = state.token;
    state.token = null;
    fetch('/api/reset', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ token: stale }),
    }).catch(() => {});
  }

  $('upload-error').hidden = true;
  $('upload-status').hidden = false;
  $('upload-status-text').textContent = `Reading ${file.name}…`;

  const body = new FormData();
  body.append('file', file);
  body.append('platform', state.platform || 'claude');

  try {
    const res = await fetch('/api/upload', { method: 'POST', body });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || 'Upload failed.');

    state.token = data.token;
    state.filename = data.filename;
    state.totalInFile = data.total_in_file;
    state.conversations = data.conversations;
    state.selected.clear();
    state.mode = null;
    state.lastBuild = null;
    document.querySelectorAll('.choice').forEach((b) => b.classList.remove('chosen'));
    resetTierFilter();

    renderTable();
    goTo(3);   // the export/remove choice comes first now
  } catch (err) {
    $('upload-error').textContent = err.message;
    $('upload-error').hidden = false;
  } finally {
    $('upload-status').hidden = true;
    fileInput.value = '';
  }
}

/* ── Page 3 · table ───────────────────────────────────────────────── */

function formatDate(value) {
  if (!value) return '—';
  const d = new Date(value);
  if (isNaN(d)) return '—';
  return d.toLocaleString(undefined, {
    year: 'numeric', month: 'short', day: 'numeric',
    hour: 'numeric', minute: '2-digit',
  });
}

function sortRows(rows) {
  const { key, dir } = state.sort;
  const factor = dir === 'asc' ? 1 : -1;
  return [...rows].sort((a, b) => {
    let x = a[key];
    let y = b[key];
    if (typeof x === 'string' && typeof y === 'string') {
      if (key === 'created_at' || key === 'updated_at') {
        x = Date.parse(x) || 0;
        y = Date.parse(y) || 0;
      } else {
        return x.localeCompare(y) * factor;
      }
    }
    return ((x ?? 0) - (y ?? 0)) * factor;
  });
}

function renderTable() {
  const query = $('search').value.trim().toLowerCase();
  const tiers = state.tierFilter;
  const filtered = state.conversations.filter((c) => {
    if (query && !(c.name || '').toLowerCase().includes(query)) return false;
    if (tiers.size && !tiers.has(c.sensitivity)) return false;
    return true;
  });

  state.visible = sortRows(filtered);

  const tbody = $('tbody');
  tbody.innerHTML = '';
  const frag = document.createDocumentFragment();

  for (const c of state.visible) {
    const tr = document.createElement('tr');
    tr.dataset.uuid = c.uuid;
    if (state.selected.has(c.uuid)) tr.classList.add('selected');

    const edits = (c.redactions || 0) + (c.deletions || 0);
    if (edits) tr.classList.add('modified');

    let chip = '', summaryLine = '';
    if (edits) {
      const bits = [];
      if (c.redactions) bits.push(`${c.redactions} redaction${c.redactions === 1 ? '' : 's'}`);
      if (c.deletions) bits.push(`${c.deletions} message${c.deletions === 1 ? '' : 's'} deleted`);
      const summary = bits.join(' · ');
      chip = `<span class="edit-chip" title="${escapeAttr(summary)}">${edits}</span>`;
      summaryLine = `<div class="edit-summary" title="${escapeAttr(summary)}">${escapeHtml(summary)}</div>`;
    }

    tr.innerHTML = `
      <td class="col-check"><input type="checkbox" ${state.selected.has(c.uuid) ? 'checked' : ''}
          aria-label="Select ${escapeAttr(c.name)}"></td>
      <td><div class="name-cell">
          <div class="name-row"><span class="conv-name"
              title="${escapeAttr(c.name)}">${escapeHtml(c.name)}</span>${chip}</div>
          ${summaryLine}</div></td>
      <td class="muted">${formatDate(c.created_at)}</td>
      <td class="muted">${formatDate(c.updated_at)}</td>
      <td class="num">${c.message_count}</td>
      <td class="num">${c.word_count.toLocaleString()}</td>
      <td class="num muted">${c.duration}</td>
      <td>${sensitivityTag(c)}</td>
      <td><button class="link-btn" data-index="${c.index}">See conversation text</button></td>
    `;
    frag.appendChild(tr);
  }
  tbody.appendChild(frag);

  $('empty-state').hidden = state.visible.length > 0;

  const shown = state.visible.length;
  const total = state.conversations.length;
  $('table-summary').textContent =
    `${state.filename} · ${shown === total ? total : `${shown} of ${total}`} conversation${total === 1 ? '' : 's'}`;

  syncHeaderCheckbox();
  updateActionBar();
  document.querySelectorAll('th.sortable').forEach((th) => {
    th.classList.toggle('asc', th.dataset.sort === state.sort.key && state.sort.dir === 'asc');
    th.classList.toggle('desc', th.dataset.sort === state.sort.key && state.sort.dir === 'desc');
  });
}

/* A colour-coded tag whose tooltip names the categories that fired. */
function sensitivityTag(c) {
  const tone = (c.sensitivity || 'Green').toLowerCase();
  const why = c.sensitivity_reasons && c.sensitivity_reasons.length
    ? `Matched: ${c.sensitivity_reasons.join(', ')}`
    : 'Nothing flagged';
  return `<span class="tag ${tone}" title="${escapeAttr(why)}">${escapeHtml(c.sensitivity)}</span>`;
}

function escapeHtml(s) {
  return String(s ?? '').replace(/[&<>"']/g, (ch) =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[ch]));
}
const escapeAttr = escapeHtml;

$('search').addEventListener('input', renderTable);

// Sensitivity filter: each pill toggles a tier; with none active, all show.
document.querySelectorAll('.tier-pill').forEach((pill) => {
  pill.addEventListener('click', () => {
    const tier = pill.dataset.tier;
    if (state.tierFilter.has(tier)) state.tierFilter.delete(tier);
    else state.tierFilter.add(tier);
    pill.setAttribute('aria-pressed', state.tierFilter.has(tier) ? 'true' : 'false');
    renderTable();
  });
});

function resetTierFilter() {
  state.tierFilter.clear();
  document.querySelectorAll('.tier-pill').forEach((p) => p.setAttribute('aria-pressed', 'false'));
}

document.querySelectorAll('th.sortable').forEach((th) => {
  th.addEventListener('click', () => {
    const key = th.dataset.sort;
    if (state.sort.key === key) {
      state.sort.dir = state.sort.dir === 'asc' ? 'desc' : 'asc';
    } else {
      state.sort = { key, dir: key === 'name' ? 'asc' : 'desc' };
    }
    renderTable();
  });
});

/* Row interactions: checkbox toggling and the transcript button. */
$('tbody').addEventListener('click', (e) => {
  const viewBtn = e.target.closest('.link-btn');
  if (viewBtn) {
    openTranscript(Number(viewBtn.dataset.index));
    return;
  }

  const tr = e.target.closest('tr');
  if (!tr) return;
  const box = tr.querySelector('input[type="checkbox"]');
  if (e.target !== box) box.checked = !box.checked;

  const uuid = tr.dataset.uuid;
  if (box.checked) state.selected.add(uuid);
  else state.selected.delete(uuid);
  tr.classList.toggle('selected', box.checked);

  syncHeaderCheckbox();
  updateActionBar();
});

$('check-all').addEventListener('change', (e) => {
  const on = e.target.checked;
  for (const c of state.visible) {
    if (on) state.selected.add(c.uuid);
    else state.selected.delete(c.uuid);
  }
  document.querySelectorAll('#tbody tr').forEach((tr) => {
    tr.querySelector('input[type="checkbox"]').checked = on;
    tr.classList.toggle('selected', on);
  });
  updateActionBar();
});

function syncHeaderCheckbox() {
  const box = $('check-all');
  const selectedHere = state.visible.filter((c) => state.selected.has(c.uuid)).length;
  box.checked = state.visible.length > 0 && selectedHere === state.visible.length;
  box.indeterminate = selectedHere > 0 && selectedHere < state.visible.length;
}

function updateActionBar() {
  const n = state.selected.size;
  const bar = $('actionbar');
  $('selected-count').textContent = `${n} conversation${n === 1 ? '' : 's'} selected`;
  $('btn-build').textContent = `${state.mode === 'remove' ? 'Remove' : 'Export'} (${n})`;

  if (n > 0 && currentPage === 4) {
    bar.hidden = false;
    reflow(bar);
    bar.classList.add('visible');
  } else {
    bar.classList.remove('visible');
    setTimeout(() => {
      if (state.selected.size === 0 || currentPage !== 4) bar.hidden = true;
    }, 400);
  }
}

$('clear-selection').addEventListener('click', () => {
  state.selected.clear();
  renderTable();
});

/* ── Highlight finder ─────────────────────────────────────────────── */

let highlights = [];
let hlIndex = -1;   // -1 means "nothing parked on yet"

function resetHighlightNav() {
  highlights = [...$('modal-body').querySelectorAll('mark.hl')];
  hlIndex = -1;
  $('hl-nav').hidden = highlights.length === 0;
  updateHighlightCount();
  updateRedactButton();
}

function updateHighlightCount() {
  const n = highlights.length;
  $('hl-count').textContent = hlIndex < 0
    ? `${n} highlight${n === 1 ? '' : 's'}`
    : `${hlIndex + 1} of ${n}`;
}

function goToHighlight(step) {
  if (!highlights.length) return;
  selectHighlight(hlIndex < 0
    ? (step > 0 ? 0 : highlights.length - 1)
    : (hlIndex + step + highlights.length) % highlights.length);
}

function selectHighlight(index) {
  if (!highlights.length) return;

  if (hlIndex >= 0 && highlights[hlIndex]) highlights[hlIndex].classList.remove('current');
  hlIndex = index;

  const mark = highlights[hlIndex];
  mark.classList.add('current');

  // Centre it inside the modal body. Measuring rects rather than using
  // scrollIntoView keeps the scroll contained to the modal — scrollIntoView
  // also walks up and moves the page behind it. The jump is instant on
  // purpose: a find bar should land immediately, and smooth scrolling is
  // animation-frame driven, so it silently does nothing in a throttled tab.
  const body = $('modal-body');
  const bodyRect = body.getBoundingClientRect();
  const markRect = mark.getBoundingClientRect();
  const delta = markRect.top - bodyRect.top - (body.clientHeight - markRect.height) / 2;
  body.scrollTop += delta;

  updateHighlightCount();
  updateRedactButton();
}

$('hl-next').addEventListener('click', () => goToHighlight(1));
$('hl-prev').addEventListener('click', () => goToHighlight(-1));

document.addEventListener('keydown', (e) => {
  if (e.key !== 'Enter' || $('transcript-modal').hidden) return;
  // Don't steal Enter from a focused control — that's how it gets activated.
  const target = e.target;
  if (target instanceof Element && target.closest('button, a, input, textarea, select')) return;
  e.preventDefault();
  goToHighlight(e.shiftKey ? -1 : 1);
});

/* Rebuild a message from its server-sliced segments, wrapping flagged runs. */
function renderMessage(message) {
  const segments = message.segments;
  if (!segments || !segments.length) return escapeHtml(message.text);

  return segments.map((seg) => {
    const body = escapeHtml(seg.text);
    if (!seg.tier) return body;
    // data-msg lets a redaction name the message the run belongs to.
    return `<mark class="hl ${seg.tier.toLowerCase()}" data-msg="${escapeAttr(message.uuid || '')}"
      title="${escapeAttr(seg.label)} — click to select, then Redact">${body}</mark>`;
  }).join('');
}

/* The conversation the modal is currently showing. */
let openConv = null;

function openTranscript(index) {
  openConv = state.conversations.find((c) => c.index === index) || null;
  state.msgSelection.clear();
  $('modal-title').textContent = openConv ? openConv.name : 'Conversation';
  $('modal-sub').textContent = '';
  $('modal-body').innerHTML = '<div class="spinner"></div>';
  $('hl-nav').hidden = true;
  showOverlay('transcript-modal');
  return loadTranscript();
}

function describeOpenConv() {
  const conv = openConv;
  if (!conv) return '';

  const parts = [
    `${conv.message_count} message${conv.message_count === 1 ? '' : 's'}`,
    formatDate(conv.created_at),
  ];
  // The tag now scans every message, so a highlight always implies a non-Green
  // tag — there's no "Green but Claude said something" case left to explain.
  if (conv.sensitivity !== 'Green') {
    parts.push(`${conv.sensitivity}: ${conv.sensitivity_reasons.join(', ')} — highlighted below`);
  }
  if (conv.redactions) {
    parts.push(`${conv.redactions} redaction${conv.redactions === 1 ? '' : 's'} applied`);
  }
  return parts.join(' · ');
}

async function loadTranscript() {
  if (!openConv) return;
  hideTextFab();  // the DOM is about to be replaced

  try {
    const res = await fetch(`/api/transcript?token=${state.token}&index=${openConv.index}`);
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || 'Could not load that conversation.');

    if (!data.messages.length) {
      $('modal-body').innerHTML = '<p class="empty">This conversation has no messages.</p>';
      $('modal-sub').textContent = describeOpenConv();
      resetHighlightNav();
      updateDeleteSelectedButton();
      setUndoRedo(data);   // still lets you undo emptying it
      return;
    }

    $('modal-body').innerHTML = data.messages.map((m) => {
      const role = m.sender === 'human' ? 'human' : 'assistant';
      const label = m.sender === 'human' ? 'You' : assistantLabel();
      const selected = m.uuid && state.msgSelection.has(m.uuid);
      // The checkbox is always rendered (when the message has a uuid); CSS shows
      // it only while the body carries the `advanced` class.
      const box = m.uuid
        ? `<input type="checkbox" class="msg-check" ${selected ? 'checked' : ''}
             aria-label="Select this message" tabindex="-1">`
        : '';
      return `
        <div class="msg ${role} ${selected ? 'msg-selected' : ''}" data-msg="${escapeAttr(m.uuid || '')}">
          <div class="msg-head">
            ${box}
            <span class="who">${label}</span>
            <span class="when">${formatDate(m.created_at)}</span>
          </div>
          <div class="bubble">${renderMessage(m) || '<em>(no text)</em>'}</div>
        </div>`;
    }).join('');
    $('modal-body').classList.toggle('advanced', state.advanced);
    $('modal-body').scrollTop = 0;
    resetHighlightNav();
    updateDeleteSelectedButton();
    setUndoRedo(data);
    $('modal-sub').textContent = describeOpenConv();
  } catch (err) {
    $('modal-body').innerHTML = `<p class="error">${escapeHtml(err.message)}</p>`;
    resetHighlightNav();
  }
}

/* ── Redaction ────────────────────────────────────────────────────── */

// Clicking a flagged run selects it, so Redact has an unambiguous target.
$('modal-body').addEventListener('click', (e) => {
  // A drag that ends on a highlight is a text selection, not a click.
  if (!window.getSelection().isCollapsed) return;
  const mark = e.target.closest('mark.hl');
  if (!mark) return;
  const at = highlights.indexOf(mark);
  if (at >= 0) selectHighlight(at);
});

$('hl-redact').addEventListener('click', async () => {
  if (hlIndex < 0 || !openConv) return;
  const mark = highlights[hlIndex];
  const button = $('hl-redact');

  button.disabled = true;
  try {
    const res = await fetch('/api/redact', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        token: state.token,
        conversation_uuid: openConv.uuid,
        message_uuid: mark.dataset.msg,
        text: mark.textContent,
      }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || 'Could not redact that text.');

    applyEditedRow(data);
    setUndoRedo(data);
    await loadTranscript();
  } catch (err) {
    alert(err.message);
  } finally {
    updateRedactButton();
  }
});

/* Fold an edit response (redaction or deletion) back into the table + modal. */
function applyEditedRow(data) {
  // Word count and tag can both move, so take the server's version, and carry
  // the edit tallies it reports for the modified-row indicator.
  const fresh = { ...data.row, redactions: data.redactions, deletions: data.deletions };
  const at = state.conversations.findIndex((c) => c.uuid === fresh.uuid);
  if (at >= 0) state.conversations[at] = fresh;
  openConv = fresh;
  renderTable();
}

/* ── Advanced settings · multi-select message deletion ────────────── */

$('adv-check').addEventListener('change', (e) => {
  state.advanced = e.target.checked;
  // Leaving advanced mode drops any pending selection.
  if (!state.advanced) {
    state.msgSelection.clear();
    document.querySelectorAll('#modal-body .msg-selected').forEach((el) => {
      el.classList.remove('msg-selected');
      const box = el.querySelector('.msg-check');
      if (box) box.checked = false;
    });
    hideTextFab();
  }
  $('modal-body').classList.toggle('advanced', state.advanced);
  updateDeleteSelectedButton();
});

// In advanced mode, clicking a message header toggles it for deletion. Clicks
// on a highlight are left to the redaction handler above.
$('modal-body').addEventListener('click', (e) => {
  if (!state.advanced || e.target.closest('mark.hl')) return;
  if (!window.getSelection().isCollapsed) return;  // finishing a text drag
  const head = e.target.closest('.msg-head');
  if (!head) return;
  const msg = head.closest('.msg');
  const uuid = msg && msg.dataset.msg;
  if (!uuid) return;

  if (state.msgSelection.has(uuid)) state.msgSelection.delete(uuid);
  else state.msgSelection.add(uuid);
  const on = state.msgSelection.has(uuid);
  msg.classList.toggle('msg-selected', on);
  const box = msg.querySelector('.msg-check');
  if (box) box.checked = on;
  updateDeleteSelectedButton();
});

function updateDeleteSelectedButton() {
  const btn = $('delete-selected');
  const n = state.msgSelection.size;
  btn.hidden = !state.advanced;
  btn.disabled = n === 0;
  btn.textContent = `Delete (${n})`;
}

$('delete-selected').addEventListener('click', async () => {
  if (!openConv || state.msgSelection.size === 0) return;
  const uuids = [...state.msgSelection];
  const btn = $('delete-selected');

  btn.disabled = true;
  try {
    const res = await fetch('/api/delete_messages', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        token: state.token,
        conversation_uuid: openConv.uuid,
        message_uuids: uuids,
      }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || 'Could not delete those messages.');

    state.msgSelection.clear();
    applyEditedRow(data);
    setUndoRedo(data);
    await loadTranscript();
  } catch (err) {
    alert(err.message);
  } finally {
    updateDeleteSelectedButton();
  }
});

/* ── Undo / redo ──────────────────────────────────────────────────── */

function setUndoRedo(data) {
  $('undo-btn').disabled = !(data && data.can_undo);
  $('redo-btn').disabled = !(data && data.can_redo);
}

async function runHistory(which) {
  if (!openConv || $(`${which}-btn`).disabled) return;
  try {
    const res = await fetch(`/api/${which}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ token: state.token, conversation_uuid: openConv.uuid }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || `Could not ${which}.`);

    state.msgSelection.clear();  // the message list just changed under us
    applyEditedRow(data);
    setUndoRedo(data);
    await loadTranscript();
  } catch (err) {
    alert(err.message);
  }
}

$('undo-btn').addEventListener('click', () => runHistory('undo'));
$('redo-btn').addEventListener('click', () => runHistory('redo'));

document.addEventListener('keydown', (e) => {
  if ($('transcript-modal').hidden || !(e.metaKey || e.ctrlKey)) return;
  const key = e.key.toLowerCase();
  if (key === 'z') { e.preventDefault(); runHistory(e.shiftKey ? 'redo' : 'undo'); }
  else if (key === 'y') { e.preventDefault(); runHistory('redo'); }
});

/* ── Advanced settings · delete an arbitrary text selection ───────── */

let pendingTextDelete = null;

function bubbleOf(node) {
  const el = node.nodeType === Node.ELEMENT_NODE ? node : node.parentElement;
  return el ? el.closest('.bubble') : null;
}

function hideTextFab() {
  $('text-delete-fab').hidden = true;
  pendingTextDelete = null;
}

// Show a floating Delete above the current selection when it sits inside a
// single message bubble and advanced mode is on.
function updateTextFab() {
  if ($('transcript-modal').hidden || !state.advanced) return hideTextFab();
  const sel = window.getSelection();
  if (!sel || sel.isCollapsed || !sel.rangeCount) return hideTextFab();

  const range = sel.getRangeAt(0);
  const bubble = bubbleOf(range.startContainer);
  if (!bubble || bubble !== bubbleOf(range.endContainer)) return hideTextFab();
  const msg = bubble.closest('.msg');
  const uuid = msg && msg.dataset.msg;
  const selected = sel.toString();
  if (!uuid || !selected.trim()) return hideTextFab();

  // Which copy of the selected string this is — count identical runs before it,
  // so the server removes the highlighted instance, not every match.
  const pre = document.createRange();
  pre.selectNodeContents(bubble);
  pre.setEnd(range.startContainer, range.startOffset);
  const occurrence = pre.toString().split(selected).length - 1;
  pendingTextDelete = { message_uuid: uuid, text: selected, occurrence };

  const rect = range.getBoundingClientRect();
  const fab = $('text-delete-fab');
  fab.hidden = false;
  fab.style.left = `${rect.left + rect.width / 2}px`;
  fab.style.top = `${rect.top - 8}px`;
}

document.addEventListener('selectionchange', updateTextFab);
$('modal-body').addEventListener('scroll', () => {
  if (!$('text-delete-fab').hidden) updateTextFab();
});

// Pressing the button must not collapse the selection before the click lands.
$('text-delete-fab').addEventListener('mousedown', (e) => e.preventDefault());

$('text-delete-fab').addEventListener('click', async () => {
  if (!openConv || !pendingTextDelete) return;
  const body = { token: state.token, conversation_uuid: openConv.uuid, ...pendingTextDelete };
  try {
    const res = await fetch('/api/delete_text', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || 'Could not delete that text.');

    window.getSelection().removeAllRanges();
    hideTextFab();
    applyEditedRow(data);
    setUndoRedo(data);
    await loadTranscript();
  } catch (err) {
    alert(err.message);
  }
});

function updateRedactButton() {
  const button = $('hl-redact');
  const ready = hlIndex >= 0;
  button.disabled = !ready;
  button.title = ready
    ? `Delete “${highlights[hlIndex].textContent}” from this conversation`
    : 'Select a highlight first';
}

/* ── Overlay helpers ──────────────────────────────────────────────── */

function showOverlay(id) {
  const el = $(id);
  el.hidden = false;
  reflow(el);
  el.classList.add('visible');
}

function hideOverlay(id) {
  const el = $(id);
  el.classList.remove('visible');
  setTimeout(() => { el.hidden = true; }, 250);
}

['modal-close', 'modal-close-2'].forEach((id) =>
  $(id).addEventListener('click', () => { hideTextFab(); hideOverlay('transcript-modal'); })
);

document.querySelectorAll('.overlay').forEach((overlay) => {
  overlay.addEventListener('click', (e) => {
    if (e.target === overlay) hideOverlay(overlay.id);
  });
});

document.addEventListener('keydown', (e) => {
  if (e.key !== 'Escape') return;
  hideTextFab();
  document.querySelectorAll('.overlay:not([hidden])').forEach((o) => hideOverlay(o.id));
});

/* ── Page 3 · export or remove, then Page 4 · select ──────────────── */

// The choice now comes first — pick a mode, then go pick conversations.
document.querySelectorAll('.choice').forEach((btn) => {
  btn.addEventListener('click', () => {
    state.mode = btn.dataset.mode;
    document.querySelectorAll('.choice').forEach((b) => b.classList.toggle('chosen', b === btn));
    $('select-heading').textContent =
      state.mode === 'remove' ? 'Select conversations to remove' : 'Select conversations to export';
    goTo(4);
  });
});

// The action bar's primary button runs the chosen action on the selection.
$('btn-build').addEventListener('click', () => {
  if (state.selected.size > 0 && state.mode) build();
});

async function build() {
  const mode = state.mode;
  const button = $('btn-build');
  button.disabled = true;
  try {
    const res = await fetch('/api/build', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        token: state.token,
        mode,
        uuids: [...state.selected],
      }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || 'Could not build the file.');

    state.lastBuild = data;
    $('dl-sub').textContent =
      mode === 'export'
        ? `Exported ${data.selected_count} conversation${data.selected_count === 1 ? '' : 's'}.`
        : `Removed ${data.selected_count} conversation${data.selected_count === 1 ? '' : 's'} from ${data.original_count}.`;
    $('result-filename').textContent = data.filename;
    $('result-meta').textContent =
      `${data.conversations_in_file.toLocaleString()} conversation${data.conversations_in_file === 1 ? '' : 's'} · ${formatBytes(data.size_bytes)}`;
    $('download-link').href = `/api/download?token=${state.token}`;
    $('download-link').setAttribute('download', data.filename);
    showOverlay('download-modal');
  } catch (err) {
    alert(err.message);
  } finally {
    button.disabled = false;
  }
}

$('dl-close').addEventListener('click', () => hideOverlay('download-modal'));

function formatBytes(bytes) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 ** 2) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 ** 2).toFixed(1)} MB`;
}
