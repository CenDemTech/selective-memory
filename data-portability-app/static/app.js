/* Conversation Filter — front-end controller.
   Three pages — Platform selection, Edit, Export — over one in-memory table.
   Uploading is a modal rather than a page of its own, so picking a platform and
   handing over a file read as one step. Ticking a row marks it for the export;
   the review modal edits the conversation in place. The Export page builds and
   downloads the cleaned file. */

const state = {
  token: null,
  filename: null,
  platform: null,
  totalInFile: 0,
  conversations: [],      // every conversation, as returned by the backend
  visible: [],            // the Edit table after search + sensitivity filters
  selected: new Set(),    // conversation uuids headed for the export
  tierFilter: new Set(),  // sensitivity tiers to show; empty = show all
  sort: { key: 'created_at', dir: 'desc' },
  lastBuild: null,
  msgSelection: new Set(),  // message uuids picked for the next mass-delete
};

const STEPS = ['Platform selection', 'Upload', 'Edit', 'Export'];
// Four pages, one per step, so the page number is the active chip directly.
const PAGE_TO_CHIP = { 1: 1, 2: 2, 3: 3, 4: 4 };

const $ = (id) => document.getElementById(id);

/* Force a style recalculation so a class added right after unhiding an element
   still animates. requestAnimationFrame would do it too, but it never fires
   while the tab is backgrounded, which leaves the UI wedged mid-transition. */
const reflow = (el) => void el.offsetWidth;

/* ── Step chips ───────────────────────────────────────────────────── */

/* Every page carries its own chip list; only one is on screen at a time, so
   they're all rebuilt together and stay in sync for free. */
function renderSteps() {
  const active = PAGE_TO_CHIP[currentPage];
  document.querySelectorAll('.stepchips').forEach((ol) => {
    ol.innerHTML = STEPS.map((label, i) => {
      const n = i + 1;
      const cls = n < active ? 'done' : n === active ? 'active' : '';
      const glyph = n < active ? '✓' : n;
      return `<li class="${cls}"><span class="chip-dot">${glyph}</span>${label}</li>`;
    }).join('');
  });
}

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
  renderSteps();
  if (step === 2) resetUploadPage();
  if (step === 4) renderExportTable();
}

document.querySelectorAll('[data-goto]').forEach((btn) => {
  btn.addEventListener('click', () => goTo(Number(btn.dataset.goto)));
});

/* ── Page 1 · platform ────────────────────────────────────────────── */

const PLATFORM_NAMES = { claude: 'Claude', openai: 'ChatGPT' };
const PLATFORM_VENDORS = { claude: 'Anthropic', openai: 'Open AI' };

// What to call the non-human speaker in transcripts, per platform.
function assistantLabel() {
  return PLATFORM_NAMES[state.platform] || 'Assistant';
}

document.querySelectorAll('.platform[data-platform]').forEach((btn) => {
  btn.addEventListener('click', () => {
    state.platform = btn.dataset.platform;
    const named = PLATFORM_NAMES[state.platform];
    const vendor = PLATFORM_VENDORS[state.platform];
    if (named) {
      $('upload-lede').innerHTML =
        `Drop your <code>conversations.json</code> from ${escapeHtml(named)} below or browse for it.`;
      $('upload-platform-name').textContent = vendor;
      $('upload-platform-note').textContent = `${named} export (.json)`;
    }
    resetUploadPage();
    goTo(2);
  });
});

/* ── Page 2 · upload ──────────────────────────────────────────────── */

/* Arriving always starts clean: a stale error from a rejected file shouldn't
   greet the next attempt. */
function resetUploadPage() {
  $('upload-error').hidden = true;
  $('upload-status').hidden = true;
}

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
  // The previous session is kept until the new file has parsed. The modal sits
  // over a live table, so a rejected file has to leave that table working —
  // dropping the old token up front would strand it on a deleted session.
  const previous = state.token;

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

    // Parsed cleanly, so the old working directory can go now.
    if (previous) {
      fetch('/api/reset', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ token: previous }),
      }).catch(() => {});
    }

    state.token = data.token;
    state.filename = data.filename;
    state.totalInFile = data.total_in_file;
    state.conversations = data.conversations;
    state.selected.clear();
    state.lastBuild = null;
    resetTierFilter();
    setDeleteHistory(null);   // fresh session — no deletions to undo yet

    // Names the file without carrying a count: deleting conversations would
    // leave a count here stale, and the footer already reports the total.
    $('edit-lede').innerHTML =
      'Redact sensitive information, delete specific turns or the whole conversation from your ' +
      `<code>${escapeHtml(data.filename)}</code>.`;

    renderTable();
    goTo(3);
  } catch (err) {
    $('upload-error').textContent = err.message;
    $('upload-error').hidden = false;
  } finally {
    $('upload-status').hidden = true;
    fileInput.value = '';
  }
}

/* ── Pages 3 & 4 · the table ──────────────────────────────────────── */

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

function matchesTier(c) {
  return !state.tierFilter.size || state.tierFilter.has(c.sensitivity);
}

/* A deleted conversation stays in place as a greyed tombstone — struck-through
   name, no checkbox, no actions — so it's plainly gone but still recoverable
   with the table's undo-delete button. */
function tombstoneRowHtml(c) {
  return `
    <td class="col-check"></td>
    <td><div class="name-cell"><div class="name-row"><span class="conv-name deleted-name"
        title="${escapeAttr(c.name)}">${escapeHtml(c.name)}</span></div></div></td>
    <td class="muted">${formatDate(c.created_at)}</td>
    <td class="muted">${formatDate(c.updated_at)}</td>
    <td class="num">${c.message_count}</td>
    <td class="num">${c.word_count.toLocaleString()}</td>
    <td class="num muted">${c.duration}</td>
    <td>${sensitivityTag(c)}</td>
    <td class="col-action"><span class="deleted-tag">Deleted</span></td>
  `;
}

/* One row builder for both tables — the Export step reuses the Edit step's
   markup so a conversation reads identically wherever it appears. */
function rowHtml(c) {
  if (c.deleted) return tombstoneRowHtml(c);
  const edits = (c.redactions || 0) + (c.deletions || 0);

  let chip = '', summaryLine = '';
  if (edits) {
    const bits = [];
    if (c.redactions) bits.push(`${c.redactions} redaction${c.redactions === 1 ? '' : 's'}`);
    if (c.deletions) bits.push(`${c.deletions} message${c.deletions === 1 ? '' : 's'} deleted`);
    const summary = bits.join(' · ');
    chip = `<span class="edit-chip" title="${escapeAttr(summary)}">${edits}</span>`;
    summaryLine = `<div class="edit-summary" title="${escapeAttr(summary)}">${escapeHtml(summary)}</div>`;
  }

  return `
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
    <td class="col-action">
      <button class="review-btn" data-index="${c.index}">Review</button>
      <button class="row-delete" data-uuid="${escapeAttr(c.uuid)}"
              title="Delete this conversation" aria-label="Delete ${escapeAttr(c.name)}">
        <svg viewBox="0 0 16 16" fill="none" aria-hidden="true"><path d="M2.8 4.3h10.4M6.5 4.3V3.1a.8.8 0 0 1 .8-.8h1.4a.8.8 0 0 1 .8.8v1.2M4.2 4.3l.6 8.3a.9.9 0 0 0 .9.8h4.6a.9.9 0 0 0 .9-.8l.6-8.3M6.7 6.8v4M9.3 6.8v4" stroke="currentColor" stroke-width="1.2" stroke-linecap="round" stroke-linejoin="round"/></svg>
      </button>
    </td>
  `;
}

/* The Export table is read-only — no checkbox, no row actions — and instead
   surfaces the edit tallies (redactions, messages deleted) as their own columns
   so the final shape of each conversation is visible before committing. */
function exportRowHtml(c) {
  return `
    <td><div class="name-cell"><div class="name-row"><span class="conv-name"
        title="${escapeAttr(c.name)}">${escapeHtml(c.name)}</span></div></div></td>
    <td class="muted">${formatDate(c.created_at)}</td>
    <td class="muted">${formatDate(c.updated_at)}</td>
    <td class="num">${c.message_count}</td>
    <td class="num">${c.word_count.toLocaleString()}</td>
    <td class="num muted">${c.duration}</td>
    <td>${sensitivityTag(c)}</td>
    <td class="num"><span class="count-pill ${c.redactions ? 'on' : ''}">${c.redactions || 0}</span></td>
    <td class="num"><span class="count-pill ${c.deletions ? 'on' : ''}">${c.deletions || 0}</span></td>
  `;
}

function fillTbody(tbody, rows, builder = rowHtml) {
  tbody.innerHTML = '';
  const frag = document.createDocumentFragment();
  for (const c of rows) {
    const tr = document.createElement('tr');
    tr.dataset.uuid = c.uuid;
    if (c.deleted) tr.classList.add('deleted');
    else {
      if (state.selected.has(c.uuid)) tr.classList.add('selected');
      if ((c.redactions || 0) + (c.deletions || 0)) tr.classList.add('modified');
    }
    tr.innerHTML = builder(c);
    frag.appendChild(tr);
  }
  tbody.appendChild(frag);
}

function renderTable() {
  const query = $('search').value.trim().toLowerCase();
  const filtered = state.conversations.filter((c) => {
    if (query && !(c.name || '').toLowerCase().includes(query)) return false;
    return matchesTier(c);
  });

  state.visible = sortRows(filtered);
  fillTbody($('tbody'), state.visible);
  $('empty-state').hidden = state.visible.length > 0;
  updateCounts();
  document.querySelectorAll('#page-3 th.sortable').forEach((th) => {
    th.classList.toggle('asc', th.dataset.sort === state.sort.key && state.sort.dir === 'asc');
    th.classList.toggle('desc', th.dataset.sort === state.sort.key && state.sort.dir === 'desc');
  });

  if (currentPage === 4) renderExportTable();
}

/* The summary line, header checkbox, and CTA — everything that reflects the
   selection without touching the table's own rows. A drag updates these on
   every step, while the rows themselves are painted in place. */
function updateCounts() {
  const shown = state.visible.length;
  const total = state.conversations.length;
  const deleted = state.conversations.filter((c) => c.deleted).length;
  $('table-summary').textContent =
    `Showing ${shown} of ${total} conversation${total === 1 ? '' : 's'}` +
    (state.selected.size ? ` · ${state.selected.size} selected` : '') +
    (deleted ? ` · ${deleted} deleted` : '');
  syncHeaderCheckbox();
  updateExportCta();
  updateDeleteButton();
}

/* The Export page lists only what's headed into the file. */
function renderExportTable() {
  const picked = state.conversations.filter((c) => state.selected.has(c.uuid) && !c.deleted);
  const rows = sortRows(picked.filter(matchesTier));

  fillTbody($('export-tbody'), rows, exportRowHtml);
  $('export-empty').hidden = rows.length > 0;

  $('export-summary').textContent = picked.length
    ? `Showing ${rows.length} selected conversation${rows.length === 1 ? '' : 's'}`
    : 'Nothing selected for export.';
  $('btn-build').disabled = picked.length === 0;
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

/* Sensitivity filter: each tier pill toggles its tier, "All" clears them. Both
   pages carry a copy of the control, so they're driven together. */
document.querySelectorAll('[data-tier-filter] .tier-pill').forEach((pill) => {
  pill.addEventListener('click', () => {
    const tier = pill.dataset.tier;
    if (!tier) state.tierFilter.clear();
    else if (state.tierFilter.has(tier)) state.tierFilter.delete(tier);
    else state.tierFilter.add(tier);
    syncTierPills();
    renderTable();
  });
});

function syncTierPills() {
  document.querySelectorAll('[data-tier-filter] .tier-pill').forEach((p) => {
    const on = p.dataset.tier ? state.tierFilter.has(p.dataset.tier) : state.tierFilter.size === 0;
    p.setAttribute('aria-pressed', on ? 'true' : 'false');
  });
}

function resetTierFilter() {
  state.tierFilter.clear();
  syncTierPills();
}

document.querySelectorAll('#page-3 th.sortable').forEach((th) => {
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

/* ── Row selection ───────────────────────────────────────────────────
   Three ways to pick conversations, sharing one selection set: click a row,
   shift-click to extend from the last one, or press and drag down the table.
   Dragging past an edge scrolls the list, so a run longer than the viewport
   can be taken in a single gesture. Both tables get all three. */

let anchorUuid = null;    // last plainly-clicked row, the shift-extend origin
let drag = null;          // { tbody, from, at, value, before, moved }
let autoScrollTimer = null;
const pointer = { x: 0, y: 0 };

const rowsOf = (tbody) => [...tbody.querySelectorAll('tr')];
// A soft-deleted tombstone row can't be selected.
const isDeletedRow = (tr) => !!tr && tr.classList.contains('deleted');

/* Sync rows in place instead of re-rendering: rebuilding mid-drag would swap
   the element out from under the cursor and strand the gesture. */
function paintSelection(tbody) {
  for (const tr of rowsOf(tbody)) {
    const on = state.selected.has(tr.dataset.uuid);
    tr.classList.toggle('selected', on);
    const box = tr.querySelector('.col-check input');
    if (box) box.checked = on;
  }
}

/* Recomputed from the pre-gesture snapshot every step, so dragging back up
   releases the rows it passed rather than leaving them stuck on. */
function applyRange(tbody, from, to, value, base) {
  state.selected = new Set(base);
  const rows = rowsOf(tbody);
  const [lo, hi] = from <= to ? [from, to] : [to, from];
  for (let i = lo; i <= hi; i++) {
    const uuid = rows[i] && rows[i].dataset.uuid;
    if (!uuid || isDeletedRow(rows[i])) continue;   // tombstones aren't selectable
    if (value) state.selected.add(uuid);
    else state.selected.delete(uuid);
  }
  paintSelection(tbody);
  updateCounts();
}

/* Swallow exactly one click — the one the browser fires after a range gesture
   ends, which belongs to the gesture rather than to a row. A flag that simply
   stayed armed would eat an unrelated click later, because a drag released
   outside the table produces no click at all; this expires on its own. */
function swallowNextClick(tbody) {
  const swallow = (ev) => {
    ev.stopPropagation();
    ev.preventDefault();
    clearTimeout(timer);
    // A gesture begun on a checkbox still gets the browser's native toggle on
    // this click, so repaint the rows from state rather than trusting them.
    paintSelection(tbody);
  };
  document.addEventListener('click', swallow, { capture: true, once: true });
  const timer = setTimeout(() => document.removeEventListener('click', swallow, true), 300);
}

function extendDragTo(tr) {
  if (!drag || !tr || !drag.tbody.contains(tr)) return;
  const index = rowsOf(drag.tbody).indexOf(tr);
  if (index < 0 || index === drag.at) return;
  drag.at = index;
  if (index !== drag.from) drag.moved = true;
  applyRange(drag.tbody, drag.from, index, drag.value, drag.before);
}

function stopAutoScroll() {
  if (autoScrollTimer) clearInterval(autoScrollTimer);
  autoScrollTimer = null;
}

/* Holding the pointer near the top or bottom edge scrolls the table under it.
   The row beneath the cursor is re-derived each tick because scrolling on its
   own doesn't reliably fire mouseover. */
function startAutoScroll(container) {
  stopAutoScroll();
  autoScrollTimer = setInterval(() => {
    if (!drag) return stopAutoScroll();
    const r = container.getBoundingClientRect();
    const EDGE = 44;
    // Speed rises with how far past the edge the pointer is pushed, so easing
    // into the margin creeps and shoving well past it moves quickly.
    const over = pointer.y < r.top + EDGE ? pointer.y - (r.top + EDGE)
               : pointer.y > r.bottom - EDGE ? pointer.y - (r.bottom - EDGE)
               : 0;
    if (!over) return;
    const by = Math.sign(over) * Math.min(30, 5 + Math.abs(over) * 0.45);

    const was = container.scrollTop;
    container.scrollTop += by;
    if (container.scrollTop === was) return;   // already at the end

    // Probe inside the content area for the row now under the cursor: clear of
    // the container's border, and below the sticky header, which would
    // otherwise answer with its own <tr> when scrolling up.
    const head = container.querySelector('thead');
    const headH = head ? head.getBoundingClientRect().height : 0;
    const y = Math.min(Math.max(pointer.y, r.top + headH + 2), r.bottom - 3);
    const el = document.elementFromPoint(pointer.x, y);
    extendDragTo(el && el.closest('tr'));
  }, 30);
}

function endDrag() {
  if (!drag) return;
  const { tbody, moved } = drag;
  drag = null;
  stopAutoScroll();
  document.body.classList.remove('is-dragging');
  if (!moved) return;
  swallowNextClick(tbody);
  renderTable();   // resync the summaries and the Export table's row set
}

// Only the Edit table is interactive; the Export table is a read-only review.
['tbody'].forEach((id) => {
  const tbody = $(id);
  const container = tbody.closest('.table-scroll');

  tbody.addEventListener('mousedown', (e) => {
    if (e.button !== 0 || e.target.closest('.review-btn, .row-delete')) return;
    const tr = e.target.closest('tr');
    if (!tr || isDeletedRow(tr)) return;   // can't start a selection on a tombstone
    const rows = rowsOf(tbody);
    const index = rows.indexOf(tr);

    // Shift extends from the last plain click, the way a file list does.
    if (e.shiftKey && anchorUuid) {
      const from = rows.findIndex((r) => r.dataset.uuid === anchorUuid);
      if (from >= 0) {
        e.preventDefault();   // otherwise the browser text-selects across the range
        applyRange(tbody, from, index, true, state.selected);
        swallowNextClick(tbody);
        return;
      }
    }

    drag = {
      tbody,
      from: index,
      at: index,
      moved: false,
      value: !state.selected.has(tr.dataset.uuid),
      before: new Set(state.selected),
    };
    document.body.classList.add('is-dragging');
    if (container) startAutoScroll(container);
  });

  tbody.addEventListener('mouseover', (e) => {
    if (drag && drag.tbody === tbody) extendDragTo(e.target.closest('tr'));
  });

  tbody.addEventListener('click', (e) => {
    const trash = e.target.closest('.row-delete');
    if (trash) {
      deleteConversations([trash.dataset.uuid]);
      return;
    }

    const reviewBtn = e.target.closest('.review-btn');
    if (reviewBtn) {
      openTranscript(Number(reviewBtn.dataset.index));
      return;
    }

    const tr = e.target.closest('tr');
    if (!tr || isDeletedRow(tr)) return;   // tombstones aren't selectable
    const box = tr.querySelector('.col-check input');
    if (box && e.target !== box) box.checked = !box.checked;

    anchorUuid = tr.dataset.uuid;
    setSelected(tr.dataset.uuid, box ? box.checked : !state.selected.has(tr.dataset.uuid));
  });
});

document.addEventListener('mousemove', (e) => { pointer.x = e.clientX; pointer.y = e.clientY; });
document.addEventListener('mouseup', endDrag);

/* One place to move a conversation in or out of the export set, so both tables
   and every count stay consistent however it was toggled. */
function setSelected(uuid, on) {
  if (on) state.selected.add(uuid);
  else state.selected.delete(uuid);
  renderTable();
}

$('check-all').addEventListener('change', (e) => {
  const on = e.target.checked;
  for (const c of state.visible) {
    if (c.deleted) continue;   // tombstones aren't selectable
    if (on) state.selected.add(c.uuid);
    else state.selected.delete(c.uuid);
  }
  renderTable();
});

function syncHeaderCheckbox() {
  const box = $('check-all');
  const selectable = state.visible.filter((c) => !c.deleted);
  const selectedHere = selectable.filter((c) => state.selected.has(c.uuid)).length;
  box.checked = selectable.length > 0 && selectedHere === selectable.length;
  box.indeterminate = selectedHere > 0 && selectedHere < selectable.length;
}

function updateDeleteButton() {
  const n = state.selected.size;
  const btn = $('btn-delete-selected');
  btn.disabled = n === 0;
  btn.lastChild.textContent = n ? ` Delete (${n})` : ' Delete';
}

function updateExportCta() {
  const n = state.selected.size;
  const btn = $('btn-to-export');
  btn.disabled = n === 0;
  btn.firstChild.textContent = n
    ? `Review ${n} selected .json conversation${n === 1 ? '' : 's'} to be exported `
    : 'Review .json file to be exported ';
}

/* Deleting whole conversations is now a soft delete: the server keeps them in
   the working file but marks them deleted, so each becomes a greyed tombstone in
   place and can be brought back with the table's undo button. Indices don't
   move, so the affected rows are just flagged and repainted — no re-render of
   the whole table, and no confirm (undo is the safety net). */
async function deleteConversations(uuids) {
  if (!uuids.length || !state.token) return false;

  try {
    const res = await fetch('/api/delete_conversations', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ token: state.token, uuids }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || 'Could not delete those conversations.');
    applyDeletion(data.deleted, true, data);
    return true;
  } catch (err) {
    alert(err.message);
    return false;
  }
}

/* Flag a set of conversations deleted (or restored) in the local state, drop
   them from the selection, and repaint. Shared by delete, undo, and redo. */
function applyDeletion(uuids, deleted, flags) {
  const set = new Set(uuids || []);
  for (const c of state.conversations) {
    if (set.has(c.uuid)) c.deleted = deleted;
  }
  if (deleted) for (const uuid of set) state.selected.delete(uuid);
  state.lastBuild = null;
  anchorUuid = null;
  setDeleteHistory(flags);
  renderTable();
  // If a delete emptied the export selection, there's nothing to be on that page for.
  if (currentPage === 4 && state.selected.size === 0) goTo(3);
}

function setDeleteHistory(flags) {
  $('del-undo').disabled = !(flags && flags.can_undo_delete);
  $('del-redo').disabled = !(flags && flags.can_redo_delete);
}

async function undoDelete() {
  if (!state.token || $('del-undo').disabled) return;
  try {
    const res = await fetch('/api/undo_delete', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ token: state.token }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || 'Could not undo.');
    applyDeletion(data.restored, false, data);
  } catch (err) {
    alert(err.message);
  }
}

async function redoDelete() {
  if (!state.token || $('del-redo').disabled) return;
  try {
    const res = await fetch('/api/redo_delete', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ token: state.token }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || 'Could not redo.');
    applyDeletion(data.deleted, true, data);
  } catch (err) {
    alert(err.message);
  }
}

$('del-undo').addEventListener('click', undoDelete);
$('del-redo').addEventListener('click', redoDelete);

$('btn-delete-selected').addEventListener('click', () => {
  deleteConversations([...state.selected]);
});

$('btn-to-export').addEventListener('click', () => {
  if (state.selected.size > 0) goTo(4);
});

/* ── Highlight finder ─────────────────────────────────────────────── */
/* A dropdown lists every flagged run; picking one parks on it so Redact has an
   unambiguous target. "Highlight & Redact" beside it removes every run at once.
   Enter / Shift+Enter still step through, keeping the dropdown in sync. */

let highlights = [];
let hlIndex = -1;   // -1 means "nothing parked on yet"

function resetHighlightNav() {
  highlights = [...$('modal-body').querySelectorAll('mark.hl')];
  hlIndex = -1;
  const empty = highlights.length === 0;
  // The finder is always present in edit mode so the header reads the same on
  // every conversation; a green one just shows it greyed and stateless. In
  // review mode there's nothing to navigate when empty, so it stays hidden.
  $('hl-nav').hidden = false;
  $('transcript-modal').classList.toggle('no-highlights', empty);
  $('hl-select').disabled = empty;
  $('hl-redact-all').disabled = empty;
  rebuildHighlightSelect();
}

/* The leading option is a count-only placeholder ("4 highlights"); the rest are
   one per run. It reads like the mockup's "N Highlight ▾" pill until you pick. */
function rebuildHighlightSelect() {
  const sel = $('hl-select');
  const n = highlights.length;
  const opts = [`<option value="-1">${n} Highlight${n === 1 ? '' : 's'} Detected</option>`];
  for (let i = 0; i < n; i++) {
    const tier = highlights[i].classList.contains('red') ? 'Red'
               : highlights[i].classList.contains('yellow') ? 'Yellow' : '';
    const label = tier ? `Highlight ${i + 1} · ${tier}` : `Highlight ${i + 1}`;
    opts.push(`<option value="${i}">${escapeHtml(label)}</option>`);
  }
  sel.innerHTML = opts.join('');
  sel.value = String(hlIndex);
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
  $('hl-select').value = String(hlIndex);

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

  // In edit mode, parking on a highlight offers a Delete button over it.
  updateTextFab();
}

/* Park nothing: drop the ring, reset the dropdown, and clear the Delete button. */
function deselectHighlight() {
  if (hlIndex >= 0 && highlights[hlIndex]) highlights[hlIndex].classList.remove('current');
  hlIndex = -1;
  $('hl-select').value = '-1';
  updateTextFab();
}

$('hl-select').addEventListener('change', (e) => {
  const i = Number(e.target.value);
  if (i >= 0) selectHighlight(i);
  else deselectHighlight();
});

document.addEventListener('keydown', (e) => {
  if (e.key !== 'Enter' || $('transcript-modal').hidden) return;
  // Don't steal Enter from a focused control — that's how it gets activated.
  const target = e.target;
  if (target instanceof Element && target.closest('button, a, input, textarea, select')) return;
  e.preventDefault();
  goToHighlight(e.shiftKey ? -1 : 1);
});

/* Rebuild a message from its server-sliced segments: flagged runs become
   highlights, redacted runs become greyed boxes. Both come straight from the
   server, so they persist across reloads, undo/redo, and reopening. */
function renderMessage(message) {
  const segments = message.segments;
  if (!segments || !segments.length) return escapeHtml(message.text);

  return segments.map((seg) => {
    const body = escapeHtml(seg.text);
    if (seg.redacted) {
      return `<span class="redaction" title="Redacted — removed from the exported file">${body}</span>`;
    }
    if (!seg.tier) return body;
    // data-msg lets a redaction name the message the run belongs to.
    return `<mark class="hl ${seg.tier.toLowerCase()}" data-msg="${escapeAttr(message.uuid || '')}"
      title="Flagged: ${escapeAttr(seg.label)} — click to redact just this run (edit mode)">${body}</mark>`;
  }).join('');
}

/* ── Review modal ─────────────────────────────────────────────────── */

const TRASH_SVG = `<svg viewBox="0 0 16 16" fill="none" aria-hidden="true"><path d="M2.8 4.3h10.4M6.5 4.3V3.1a.8.8 0 0 1 .8-.8h1.4a.8.8 0 0 1 .8.8v1.2M4.2 4.3l.6 8.3a.9.9 0 0 0 .9.8h4.6a.9.9 0 0 0 .9-.8l.6-8.3M6.7 6.8v4M9.3 6.8v4" stroke="currentColor" stroke-width="1.2" stroke-linecap="round" stroke-linejoin="round"/></svg>`;

/* A turn is a human message and the assistant reply (or replies) that follow it.
   A new turn starts at each human message; anything before the first one — an
   opening assistant note — forms turn 1 on its own. */
function groupIntoTurns(messages) {
  const turns = [];
  let cur = null;
  for (const m of messages) {
    if (m.sender === 'human' || !cur) { cur = []; turns.push(cur); }
    cur.push(m);
  }
  return turns;
}

/* Per-message and per-turn controls (checkbox + trash) are always shown — the
   modal is the place editing happens, so there's no mode to arm first. */
function controlsHtml(kind) {
  const noun = kind === 'turn' ? 'turn' : 'message';
  const checkTip = `Select this ${noun} — then use the delete button to remove several at once`;
  const trashTip = `Delete this ${noun}`;
  return `<span class="edit-controls ${kind}-controls">
      <input type="checkbox" class="${kind}-check" aria-label="Select ${noun} for deletion" title="${checkTip}">
      <button class="msg-trash ${kind}-trash" title="${trashTip}" aria-label="${trashTip}">${TRASH_SVG}</button>
    </span>`;
}

function renderMsg(m) {
  const role = m.sender === 'human' ? 'human' : 'assistant';
  const label = m.sender === 'human' ? 'You' : assistantLabel();
  const selected = m.uuid && state.msgSelection.has(m.uuid);
  return `
    <div class="msg ${role} ${selected ? 'msg-selected' : ''}" data-msg="${escapeAttr(m.uuid || '')}">
      <div class="msg-head">
        <span class="who">${label}</span>
        <span class="when">${formatDate(m.created_at)}</span>
        ${m.uuid ? controlsHtml('msg') : ''}
      </div>
      <div class="bubble">${renderMessage(m) || '<em>(no text)</em>'}</div>
    </div>`;
}

function renderTurn(msgs, i) {
  const uuids = msgs.filter((m) => m.uuid).map((m) => m.uuid);
  return `
    <section class="turn" data-turn-msgs="${escapeAttr(uuids.join(','))}">
      <div class="turn-head">
        <span class="turn-label">Turn ${i + 1}</span>
        <span class="turn-rule"></span>
        ${uuids.length ? controlsHtml('turn') : ''}
      </div>
      ${msgs.map(renderMsg).join('')}
    </section>`;
}

/* The conversation the modal is currently showing. */
let openConv = null;

/* The Review modal opens read-only; the Edit button reveals the editing tools
   (redact buttons, per-turn/message checkboxes and trash, delete, save, and
   drag-to-delete). This is independent of a conversation's sensitivity, so a
   green conversation can be edited the same way. */
let editing = false;
// The edit tally at the moment edit mode was entered, so an exit can tell
// whether unsaved changes were made in this session.
let editBaselineRevisions = 0;

function currentRevisions() {
  return openConv ? (openConv.redactions || 0) + (openConv.deletions || 0) : 0;
}

function setEditing(on) {
  editing = on;
  $('transcript-modal').classList.toggle('editing', on);
  const btn = $('edit-toggle');
  btn.setAttribute('aria-pressed', on ? 'true' : 'false');
  btn.querySelector('.edit-toggle-label').textContent = on ? 'Exit Edit' : 'Edit';
  btn.title = on ? 'Exit editing' : 'Show editing tools';
  if (on) {
    editBaselineRevisions = currentRevisions();
  } else {
    // Leaving edit mode drops any pending message selection and highlight mode.
    state.msgSelection.clear();
    document.querySelectorAll('#modal-body .msg-selected').forEach((el) => {
      el.classList.remove('msg-selected');
      const box = el.querySelector('.msg-check');
      if (box) box.checked = false;
    });
    setHighlightMode(false);
    deselectHighlight();
    refreshTurnControls();
    updateModalDeleteButton();
  }
}

/* Exit edit mode back to the read-only view (staying in the modal). If edits
   were made this session and not saved, confirm first and roll them back to the
   state you entered on — that's what "exit without saving" means here, since
   every edit is otherwise written straight to the working copy. */
async function exitEditMode() {
  if (currentRevisions() !== editBaselineRevisions) {
    const ok = await confirmDialog(
      'You made changes to the conversation without saving. Are you sure you want to exit without saving?',
      { title: 'Exit without saving?', okLabel: 'Exit without saving', okClass: 'danger' }
    );
    if (!ok) return;
    let guard = 0;
    while (openConv && currentRevisions() > editBaselineRevisions &&
           !$('undo-btn').disabled && guard++ < 200) {
      await runHistory('undo');
    }
  }
  setEditing(false);
}

$('edit-toggle').addEventListener('click', () => {
  if (editing) exitEditMode();
  else setEditing(true);
});

/* ── Highlight mode ───────────────────────────────────────────────────
   In edit mode, turning this on lets you drag-select any run of text inside a
   message — the selection turns red — and a floating Delete button appears just
   above it. One click redacts exactly that run (boxed like any redaction). With
   the toggle off, selecting text does nothing special. */
let highlightMode = false;
let pendingTextDelete = null;

function setHighlightMode(on) {
  highlightMode = on;
  $('highlight-mode').checked = on;
  // NB: a distinct class name — `.highlight-mode` is the toggle *label*, and
  // reusing it here would drop that label's `user-select:none` onto the whole
  // modal and make the transcript unselectable. `hl-mode` also paints the
  // selection red.
  $('transcript-modal').classList.toggle('hl-mode', on);
  if (!on) hideTextFab();
}

$('highlight-mode').addEventListener('change', (e) => setHighlightMode(e.target.checked));

/* Count which copy of ``text`` sits at the start of ``range`` within ``bubble``,
   so the server redacts that instance rather than every match. */
function occurrenceBefore(bubble, range, text) {
  const pre = document.createRange();
  pre.selectNodeContents(bubble);
  pre.setStart(bubble, 0);
  pre.setEnd(range.startContainer, range.startOffset);
  return pre.toString().split(text).length - 1;
}

function positionFab(rect) {
  const fab = $('text-delete-fab');
  fab.hidden = false;
  fab.style.left = `${rect.left + rect.width / 2}px`;
  fab.style.top = `${rect.top - 8}px`;
}

/* The floating Delete button, above whatever is currently targeted for
   redaction (edit mode only). Two things can target it: a live text drag inside
   a bubble while Highlight mode is on, or a sensitivity highlight the finder is
   parked on. Either sets `pendingTextDelete` and shows the button over it. */
function updateTextFab() {
  if ($('transcript-modal').hidden || !editing) return hideTextFab();
  const body = $('modal-body');
  const sel = window.getSelection();

  // 1) Highlight mode: a live drag-selection inside one bubble. The bubble must
  // still be in the live transcript — a selection left over old, replaced DOM
  // isn't a real target.
  if (highlightMode && sel && !sel.isCollapsed && sel.rangeCount) {
    const range = sel.getRangeAt(0);
    const bubble = bubbleOf(range.startContainer);
    const msg = bubble && bubble.closest('.msg');
    const selected = range.toString();
    if (bubble && body.contains(bubble) && bubble === bubbleOf(range.endContainer) &&
        msg && msg.dataset.msg && selected.trim()) {
      pendingTextDelete = {
        message_uuid: msg.dataset.msg,
        text: selected,
        occurrence: occurrenceBefore(bubble, range, selected),
      };
      positionFab(range.getBoundingClientRect());
      return;
    }
  }

  // 2) A sensitivity highlight the finder is parked on — offer to redact it.
  if (hlIndex >= 0 && highlights[hlIndex] && body.contains(highlights[hlIndex])) {
    const mark = highlights[hlIndex];
    const bubble = mark.closest('.bubble');
    const msg = mark.closest('.msg');
    if (bubble && msg && msg.dataset.msg) {
      const markRange = document.createRange();
      markRange.selectNode(mark);
      pendingTextDelete = {
        message_uuid: msg.dataset.msg,
        text: mark.textContent,
        occurrence: occurrenceBefore(bubble, markRange, mark.textContent),
      };
      positionFab(mark.getBoundingClientRect());
      return;
    }
  }

  hideTextFab();
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
    await loadTranscript();  // server hands the boxed run back as a segment
    hideTextFab();           // final word, after any mid-reload selectionchange
  } catch (err) {
    alert(err.message);
  }
});

function openTranscript(index) {
  openConv = state.conversations.find((c) => c.index === index) || null;
  state.msgSelection.clear();
  setEditing(false);   // always open read-only
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
    `Started ${formatDate(conv.created_at)}`,
    `ID: ${conv.uuid}`,
  ];
  // The tag scans every message, so a highlight always implies a non-Green tag.
  if (conv.sensitivity !== 'Green') {
    parts.push(`${conv.sensitivity}: ${conv.sensitivity_reasons.join(', ')} — highlighted below`);
  }
  return parts.join(' · ');
}

/* The footer's running tally of edits to this conversation, as a revision count
   (redactions + message deletions), with the breakdown on hover. */
function updateEditStatus() {
  const conv = openConv;
  const el = $('edit-status');
  if (!conv) return void (el.textContent = '', el.title = '');
  const revisions = (conv.redactions || 0) + (conv.deletions || 0);
  el.textContent = revisions ? `${revisions} revision${revisions === 1 ? '' : 's'}` : '';
  const bits = [];
  if (conv.redactions) bits.push(`${conv.redactions} redaction${conv.redactions === 1 ? '' : 's'}`);
  if (conv.deletions) bits.push(`${conv.deletions} message${conv.deletions === 1 ? '' : 's'} deleted`);
  el.title = bits.join(' · ');
}

async function loadTranscript() {
  if (!openConv) return;
  // Clear the fab and the parked highlight *before* the async fetch. Otherwise a
  // `selectionchange` queued by the delete (removeAllRanges) can fire mid-reload
  // while `hlIndex` still points at the old, not-yet-replaced mark, and reopen
  // the Delete button over text that's about to disappear.
  hlIndex = -1;
  highlights = [];
  hideTextFab();  // the DOM is about to be replaced

  try {
    const res = await fetch(`/api/transcript?token=${state.token}&index=${openConv.index}`);
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || 'Could not load that conversation.');

    if (!data.messages.length) {
      $('modal-body').innerHTML = '<p class="empty">This conversation has no messages.</p>';
      $('modal-sub').textContent = describeOpenConv();
      resetHighlightNav();
      updateModalDeleteButton();
      updateEditStatus();
      setUndoRedo(data);   // still lets you undo emptying it
      return;
    }

    $('modal-body').innerHTML = groupIntoTurns(data.messages).map(renderTurn).join('');
    $('modal-body').scrollTop = 0;
    resetHighlightNav();
    refreshTurnControls();
    updateModalDeleteButton();
    updateEditStatus();
    setUndoRedo(data);
    $('modal-sub').textContent = describeOpenConv();
  } catch (err) {
    $('modal-body').innerHTML = `<p class="error">${escapeHtml(err.message)}</p>`;
    resetHighlightNav();
  }
}

/* Edits are written to the working copy as they're made, so Save Changes is a
   "keep these and step out of edit mode" — it leaves the review window open,
   just no longer editing, and marks this session's edits as saved (so exiting
   afterwards won't warn). */
$('save-changes').addEventListener('click', () => {
  hideTextFab();
  setEditing(false);
});

/* ── Redaction ────────────────────────────────────────────────────── */

// Selecting a flagged run is an edit-mode action: clicking parks the finder on
// it (and shows a Delete button over it); clicking the selected one unclicks it.
$('modal-body').addEventListener('click', (e) => {
  if (!editing) return;
  // A drag that ends on a highlight is a text selection, not a click.
  if (!window.getSelection().isCollapsed) return;
  const mark = e.target.closest('mark.hl');
  if (!mark) return;
  const at = highlights.indexOf(mark);
  if (at < 0) return;
  if (at === hlIndex) deselectHighlight();
  else selectHighlight(at);
});

/* A redaction leaves a grey box with the removed text greyed out inside, so it's
   clear what was taken out. The run is stripped from the export, but the server
   keeps it (wrapped) in the working copy and hands it back as a `redacted`
   segment, so the box is rendered from the reloaded transcript and persists
   across undo/redo and reopening. */

/* "Redact all highlighted": redact every highlight in the conversation at once —
   the flagged runs the classifier detected plus any you added by hand in
   Highlight mode. Each highlight is boxed via /api/delete_text, which wraps the
   leftmost still-visible copy of its text; taking the marks in document order
   and always passing occurrence 0 boxes them left-to-right without shifting the
   ones still to come (a boxed run stops counting as visible). One reload at the
   end renders the persistent boxes. */
$('hl-redact-all').addEventListener('click', async () => {
  if (!openConv) return;
  const button = $('hl-redact-all');
  const targets = [...$('modal-body').querySelectorAll('mark.hl')]
    .map((m) => ({ message_uuid: m.dataset.msg, text: m.textContent }))
    .filter((t) => t.message_uuid && t.text);
  if (!targets.length) return;

  button.disabled = true;
  try {
    let last = null;
    for (const t of targets) {
      try {
        const res = await fetch('/api/delete_text', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            token: state.token,
            conversation_uuid: openConv.uuid,
            message_uuid: t.message_uuid,
            text: t.text,
            occurrence: 0,
          }),
        });
        const data = await res.json();
        if (res.ok) last = data;
      } catch (_) { /* skip a run that can't be matched and keep going */ }
    }
    if (last) { applyEditedRow(last); setUndoRedo(last); }
    await loadTranscript();
  } catch (err) {
    alert(err.message);
  } finally {
    // Stay disabled if there's nothing left to redact.
    button.disabled = $('modal-body').querySelectorAll('mark.hl').length === 0;
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
  updateEditStatus();
}

/* ── Message & turn deletion ──────────────────────────────────────── */
/* Checkboxes build a selection; the trash icons delete now. A deleted message
   leaves a tombstone in place so the removal reads clearly, and undo brings it
   back on the next full reload. */

const liveMsgUuids = (scope) =>
  [...scope.querySelectorAll('.msg:not(.deleted)')].map((m) => m.dataset.msg).filter(Boolean);

const isTombstoned = (uuid) => {
  const el = $('modal-body').querySelector(`.msg[data-msg="${cssEscape(uuid)}"]`);
  return !!el && el.classList.contains('deleted');
};

function cssEscape(s) {
  return (window.CSS && CSS.escape) ? CSS.escape(s) : String(s).replace(/["\\]/g, '\\$&');
}

/* Reflect a message's selected state in both the DOM and the selection set. */
function setMsgSelected(uuid, on) {
  if (on) state.msgSelection.add(uuid);
  else state.msgSelection.delete(uuid);
  const el = $('modal-body').querySelector(`.msg[data-msg="${cssEscape(uuid)}"]`);
  if (!el) return;
  el.classList.toggle('msg-selected', on);
  const box = el.querySelector('.msg-check');
  if (box) box.checked = on;
}

/* Sync every turn's checkbox/trash to the messages it still holds: a turn reads
   checked when all its live messages are, disabled when it has none left. */
function refreshTurnControls() {
  document.querySelectorAll('#modal-body .turn').forEach((turn) => {
    const live = liveMsgUuids(turn);
    const chosen = live.filter((u) => state.msgSelection.has(u)).length;
    const box = turn.querySelector('.turn-check');
    const trash = turn.querySelector('.turn-trash');
    if (box) {
      box.checked = live.length > 0 && chosen === live.length;
      box.indeterminate = chosen > 0 && chosen < live.length;
      box.disabled = live.length === 0;
    }
    if (trash) trash.disabled = live.length === 0;
  });
}

$('modal-body').addEventListener('change', (e) => {
  const msgCheck = e.target.closest('.msg-check');
  if (msgCheck) {
    const msg = msgCheck.closest('.msg');
    if (msg && msg.dataset.msg) setMsgSelected(msg.dataset.msg, msgCheck.checked);
    refreshTurnControls();
    updateModalDeleteButton();
    return;
  }
  const turnCheck = e.target.closest('.turn-check');
  if (turnCheck) {
    const turn = turnCheck.closest('.turn');
    for (const uuid of liveMsgUuids(turn)) setMsgSelected(uuid, turnCheck.checked);
    refreshTurnControls();
    updateModalDeleteButton();
  }
});

$('modal-body').addEventListener('click', (e) => {
  const turnTrash = e.target.closest('.turn-trash');
  if (turnTrash) {
    deleteMessagesInModal(liveMsgUuids(turnTrash.closest('.turn')));
    return;
  }
  const msgTrash = e.target.closest('.msg-trash');
  if (msgTrash) {
    const msg = msgTrash.closest('.msg');
    if (msg) deleteMessagesInModal([msg.dataset.msg]);
  }
});

async function deleteMessagesInModal(uuids) {
  uuids = (uuids || []).filter((u) => u && !isTombstoned(u));
  if (!openConv || !uuids.length) return;
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

    for (const uuid of uuids) {
      state.msgSelection.delete(uuid);
      markTombstone(uuid);
    }
    applyEditedRow(data);
    setUndoRedo(data);
    resetHighlightNav();     // any flagged runs in those messages are gone now
    refreshTurnControls();
    updateModalDeleteButton();
  } catch (err) {
    alert(err.message);
  }
}

function markTombstone(uuid) {
  const el = $('modal-body').querySelector(`.msg[data-msg="${cssEscape(uuid)}"]`);
  if (!el) return;
  el.classList.add('deleted');
  el.classList.remove('msg-selected');
  const bubble = el.querySelector('.bubble');
  if (bubble) bubble.innerHTML = '<em>This message has been deleted.</em>';
  const controls = el.querySelector('.edit-controls');
  if (controls) controls.remove();
}

/* The footer's destructive button does double duty: it removes the whole
   conversation by default, and switches to removing the checked messages while
   any are selected — so the two never compete for the same slot. */
function updateModalDeleteButton() {
  const btn = $('modal-delete');
  const n = state.msgSelection.size;
  if (n > 0) {
    btn.dataset.mode = 'messages';
    btn.textContent = `Delete ${n} message${n === 1 ? '' : 's'}`;
  } else {
    btn.dataset.mode = 'conversation';
    btn.textContent = 'Delete Entire Conversation';
  }
}

$('modal-delete').addEventListener('click', async () => {
  if ($('modal-delete').dataset.mode === 'messages') {
    deleteMessagesInModal([...state.msgSelection]);
    return;
  }
  if (!openConv) return;
  const ok = await deleteConversations([openConv.uuid]);
  if (ok) {
    hideTextFab();
    hideOverlay('transcript-modal');
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

/* Locate the message bubble a DOM node sits in (used by Highlight mode). */
function bubbleOf(node) {
  const el = node.nodeType === Node.ELEMENT_NODE ? node : node.parentElement;
  return el ? el.closest('.bubble') : null;
}

function hideTextFab() {
  $('text-delete-fab').hidden = true;
  pendingTextDelete = null;
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

/* An in-app confirm dialog. Returns a promise resolving true/false. Used instead
   of the browser's native confirm(), which is silently suppressed inside some
   embedded/preview browsers — so the warning would never appear. */
function confirmDialog(message, opts = {}) {
  return new Promise((resolve) => {
    $('confirm-title').textContent = opts.title || 'Are you sure?';
    $('confirm-message').textContent = message;
    const ok = $('confirm-ok');
    const cancel = $('confirm-cancel');
    ok.textContent = opts.okLabel || 'Confirm';
    ok.className = `btn ${opts.okClass || 'danger'}`;
    showOverlay('confirm-modal');
    setTimeout(() => ok.focus(), 0);

    const finish = (result) => {
      hideOverlay('confirm-modal');
      ok.removeEventListener('click', onOk);
      cancel.removeEventListener('click', onCancel);
      $('confirm-modal').removeEventListener('click', onBackdrop);
      document.removeEventListener('keydown', onKey, true);
      resolve(result);
    };
    const onOk = () => finish(true);
    const onCancel = () => finish(false);
    const onBackdrop = (e) => { if (e.target === $('confirm-modal')) finish(false); };
    const onKey = (e) => {
      if (e.key === 'Escape') { e.stopPropagation(); e.preventDefault(); finish(false); }
      else if (e.key === 'Enter') { e.stopPropagation(); e.preventDefault(); finish(true); }
    };
    ok.addEventListener('click', onOk);
    cancel.addEventListener('click', onCancel);
    $('confirm-modal').addEventListener('click', onBackdrop);
    document.addEventListener('keydown', onKey, true);
  });
}

// The close ✕ is hidden while editing (see CSS); in review it closes the modal.
$('modal-close').addEventListener('click', () => {
  hideTextFab();
  hideOverlay('transcript-modal');
});

document.querySelectorAll('.overlay').forEach((overlay) => {
  overlay.addEventListener('click', (e) => {
    if (e.target !== overlay) return;
    if (overlay.id === 'confirm-modal') return;  // confirmDialog owns its backdrop
    // You must leave edit mode before the conversation can be closed, so a
    // backdrop click in edit mode exits edit mode instead of closing.
    if (overlay.id === 'transcript-modal' && editing) { exitEditMode(); return; }
    hideOverlay(overlay.id);
  });
});

document.addEventListener('keydown', (e) => {
  if (e.key !== 'Escape') return;
  hideTextFab();
  // Same rule for Escape: in edit mode it exits editing rather than closing.
  if (!$('transcript-modal').hidden && editing) { exitEditMode(); return; }
  document.querySelectorAll('.overlay:not([hidden])').forEach((o) => hideOverlay(o.id));
});

/* ── Page 3 · build and download ──────────────────────────────────── */

$('btn-build').addEventListener('click', () => {
  if (state.selected.size > 0) build();
});

async function build() {
  const button = $('btn-build');
  button.disabled = true;
  try {
    const res = await fetch('/api/build', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        token: state.token,
        mode: 'export',
        uuids: [...state.selected],
      }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || 'Could not build the file.');

    state.lastBuild = data;
    $('dl-sub').textContent =
      `Cleaned file built from ${data.selected_count} of ${data.original_count} conversation${data.original_count === 1 ? '' : 's'}.`;
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

/* ── Boot ─────────────────────────────────────────────────────────── */

renderSteps();
syncTierPills();
