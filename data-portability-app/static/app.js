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
// Upload is a modal, not a page of its own, so the chip row has four steps but
// the app has three pages. This maps the page you're on to the chip that reads
// active; everything before it is done, everything after is upcoming.
const PAGE_TO_CHIP = { 1: 1, 2: 3, 3: 4 };

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
  if (step === 3) renderExportTable();
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
    openUploadModal();
  });
});

/* ── Upload · a modal, not a page ─────────────────────────────────── */

/* Opening always starts clean: a stale error from a rejected file shouldn't
   greet the next attempt. */
function openUploadModal() {
  $('upload-error').hidden = true;
  $('upload-status').hidden = true;
  showOverlay('upload-modal');
}

// Reached from the Edit page — swapping the file keeps you where you are if you
// change your mind, and lands you on a fresh table if you don't.
$('back-to-upload').addEventListener('click', openUploadModal);

$('upload-close').addEventListener('click', () => hideOverlay('upload-modal'));

// The only route back to the platform cards, so page 1 is never a dead end:
// picking a platform there reopens this modal.
$('change-platform').addEventListener('click', () => {
  hideOverlay('upload-modal');
  goTo(1);
});

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

    // Names the file without carrying a count: deleting conversations would
    // leave a count here stale, and the footer already reports the total.
    $('edit-lede').innerHTML =
      'Redact sensitive information, delete specific turns or the whole conversation from your ' +
      `<code>${escapeHtml(data.filename)}</code>.`;

    renderTable();
    hideOverlay('upload-modal');
    goTo(2);
  } catch (err) {
    $('upload-error').textContent = err.message;
    $('upload-error').hidden = false;
  } finally {
    $('upload-status').hidden = true;
    fileInput.value = '';
  }
}

/* ── Pages 2 & 3 · the table ──────────────────────────────────────── */

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

/* One row builder for both tables — the Export step reuses the Edit step's
   markup so a conversation reads identically wherever it appears. */
function rowHtml(c) {
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
    if (state.selected.has(c.uuid)) tr.classList.add('selected');
    if ((c.redactions || 0) + (c.deletions || 0)) tr.classList.add('modified');
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
  document.querySelectorAll('#page-2 th.sortable').forEach((th) => {
    th.classList.toggle('asc', th.dataset.sort === state.sort.key && state.sort.dir === 'asc');
    th.classList.toggle('desc', th.dataset.sort === state.sort.key && state.sort.dir === 'desc');
  });

  if (currentPage === 3) renderExportTable();
}

/* The summary line, header checkbox, and CTA — everything that reflects the
   selection without touching the table's own rows. A drag updates these on
   every step, while the rows themselves are painted in place. */
function updateCounts() {
  const shown = state.visible.length;
  const total = state.conversations.length;
  $('table-summary').textContent =
    `Showing ${shown} of ${total} conversation${total === 1 ? '' : 's'}` +
    (state.selected.size ? ` · ${state.selected.size} selected` : '');
  syncHeaderCheckbox();
  updateExportCta();
  updateDeleteButton();
}

/* The Export page lists only what's headed into the file. */
function renderExportTable() {
  const picked = state.conversations.filter((c) => state.selected.has(c.uuid));
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

document.querySelectorAll('#page-2 th.sortable').forEach((th) => {
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
    if (!uuid) continue;
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
    if (!tr) return;
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
    if (!tr) return;
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
    if (on) state.selected.add(c.uuid);
    else state.selected.delete(c.uuid);
  }
  renderTable();
});

function syncHeaderCheckbox() {
  const box = $('check-all');
  const selectedHere = state.visible.filter((c) => state.selected.has(c.uuid)).length;
  box.checked = state.visible.length > 0 && selectedHere === state.visible.length;
  box.indeterminate = selectedHere > 0 && selectedHere < state.visible.length;
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

/* Removing whole conversations rewrites the working copy and can't be undone,
   so it asks first. The server renumbers what's left and hands back fresh
   metadata, which replaces the table wholesale — patching the old rows would
   leave every index after the gap pointing at the wrong conversation. */
async function deleteConversations(uuids) {
  if (!uuids.length || !state.token) return false;

  const names = uuids
    .map((u) => (state.conversations.find((c) => c.uuid === u) || {}).name)
    .filter(Boolean);
  const what = uuids.length === 1
    ? `“${names[0] || 'this conversation'}”`
    : `${uuids.length} conversations`;
  if (!confirm(`Delete ${what} from your working copy? This can't be undone.`)) return false;

  const btn = $('btn-delete-selected');
  btn.disabled = true;
  try {
    const res = await fetch('/api/delete_conversations', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ token: state.token, uuids }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || 'Could not delete those conversations.');

    state.conversations = data.conversations;
    state.totalInFile = data.total_in_file;
    state.lastBuild = null;
    for (const uuid of uuids) state.selected.delete(uuid);
    anchorUuid = null;   // the row it pointed at may be gone

    renderTable();
    // Nothing left to export means nothing to be on the Export page for.
    if (currentPage === 3 && state.selected.size === 0) goTo(2);
    return true;
  } catch (err) {
    alert(err.message);
    return false;
  } finally {
    updateDeleteButton();
  }
}

$('btn-delete-selected').addEventListener('click', () => {
  deleteConversations([...state.selected]);
});

$('btn-to-export').addEventListener('click', () => {
  if (state.selected.size > 0) goTo(3);
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
  $('hl-nav').hidden = highlights.length === 0;
  rebuildHighlightSelect();
  updateRedactButton();
}

/* The leading option is a count-only placeholder ("4 highlights"); the rest are
   one per run. It reads like the mockup's "N Highlight ▾" pill until you pick. */
function rebuildHighlightSelect() {
  const sel = $('hl-select');
  const n = highlights.length;
  const opts = [`<option value="-1">${n} highlight${n === 1 ? '' : 's'}</option>`];
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

  updateRedactButton();
}

$('hl-select').addEventListener('change', (e) => {
  const i = Number(e.target.value);
  if (i >= 0) selectHighlight(i);
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
      title="${escapeAttr(seg.label)} — click to select, then Redact">${body}</mark>`;
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
  return `<span class="edit-controls ${kind}-controls">
      <input type="checkbox" class="${kind}-check" aria-label="Select for deletion">
      <button class="msg-trash ${kind}-trash" title="Delete" aria-label="Delete">${TRASH_SVG}</button>
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

/* Edits are written to the working copy as they're made, so this is a plain
   "done" — it closes the modal with everything already persisted. */
$('save-changes').addEventListener('click', () => {
  hideTextFab();
  hideOverlay('transcript-modal');
});

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

/* A redaction leaves a grey box with the removed text greyed out inside, so it's
   clear what was taken out. The run is stripped from the export, but the server
   keeps it (wrapped) in the working copy and hands it back as a `redacted`
   segment, so the box is rendered from the reloaded transcript and persists
   across undo/redo and reopening. */

// "Redact" (amber): remove the run currently parked on in the dropdown.
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

/* "Highlight & Redact" (blue): remove every flagged run in the conversation.
   Redacting always removes a still-flagged run, so re-scan and take the first
   one each pass until none remain — the guard just bounds a pathological loop. */
$('hl-redact-all').addEventListener('click', async () => {
  if (!openConv) return;
  const button = $('hl-redact-all');
  button.disabled = true;
  try {
    let guard = 0;
    while (guard++ < 500) {
      const mark = $('modal-body').querySelector('mark.hl');
      if (!mark) break;
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
    }
  } catch (err) {
    alert(err.message);
  } finally {
    button.disabled = false;
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
    btn.textContent = 'Delete Conversation';
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
// single message bubble.
function updateTextFab() {
  if ($('transcript-modal').hidden) return hideTextFab();
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
  // so the server redacts the highlighted instance, not every match.
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
    await loadTranscript();  // server hands the boxed run back as a segment
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

$('modal-close').addEventListener('click', () => {
  hideTextFab();
  hideOverlay('transcript-modal');
});

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
