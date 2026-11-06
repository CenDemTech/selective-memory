/* Connects the original front end (app.js) to the Python backend running in
   worker.js. app.js still calls fetch('/api/...'); those calls are answered
   inside the browser instead of by a local server. Everything else passes
   through to the real fetch. */
(function () {
  const worker = new Worker('worker.js');
  const pending = new Map();
  let nextId = 1;

  /* Small status pill shown while Python loads. */
  const pill = document.createElement('div');
  pill.id = 'engine-status';
  pill.setAttribute('role', 'status');
  pill.innerHTML =
    '<span class="engine-dot" aria-hidden="true"></span>' +
    '<span class="engine-text">Getting things ready… this takes a few seconds the first time.</span>';
  const css = document.createElement('style');
  css.textContent = `
    #engine-status{position:fixed;left:50%;bottom:24px;transform:translateX(-50%);z-index:9999;
      display:flex;align-items:center;gap:10px;padding:10px 16px;border-radius:999px;
      background:var(--surface-2,#151E33);color:var(--ink,#E8EEF9);border:1px solid var(--border-strong,rgba(255,255,255,.16));
      font:14px/1.3 Inter,system-ui,sans-serif;box-shadow:0 8px 24px rgba(0,0,0,.25);max-width:calc(100vw - 32px)}
    #engine-status .engine-dot{width:10px;height:10px;border-radius:50%;flex:none;
      background:var(--blue,#2E6BF6);animation:enginePulse 1s ease-in-out infinite alternate}
    #engine-status.is-ready .engine-dot{background:var(--ok,#4ADE9B);animation:none}
    #engine-status.is-failed .engine-dot{background:var(--danger,#FF8A80);animation:none}
    @keyframes enginePulse{from{opacity:.35}to{opacity:1}}
    @media (prefers-reduced-motion: reduce){#engine-status .engine-dot{animation:none}}`;
  document.head.appendChild(css);
  const mount = () => document.body.appendChild(pill);
  if (document.body) mount(); else document.addEventListener('DOMContentLoaded', mount);
  const setStatus = (text, cls) => {
    pill.querySelector('.engine-text').textContent = text;
    if (cls) pill.classList.add(cls);
  };

  worker.onmessage = (event) => {
    const msg = event.data;
    if (msg.type === 'progress') {
      setStatus(msg.text + ' This takes a few seconds the first time.');
    } else if (msg.type === 'ready') {
      setStatus('Ready. Everything runs on your computer.', 'is-ready');
      setTimeout(() => pill.remove(), 2500);
    } else if (msg.type === 'failed') {
      setStatus('Something went wrong while loading. Please refresh the page. (' + msg.text + ')', 'is-failed');
    } else if (msg.type === 'response') {
      const entry = pending.get(msg.id);
      if (!entry) return;
      pending.delete(msg.id);
      if (msg.error) {
        entry.resolve(new Response(JSON.stringify({ error: 'Internal error: ' + msg.error }), {
          status: 500, headers: { 'Content-Type': 'application/json' },
        }));
      } else {
        entry.resolve(new Response(msg.body, {
          status: msg.status, headers: { 'Content-Type': msg.contentType || 'application/octet-stream' },
        }));
      }
    }
  };

  const realFetch = window.fetch.bind(window);
  window.fetch = async function (input, init) {
    const raw = typeof input === 'string' ? input : '';
    if (!raw.startsWith('/api/')) return realFetch(input, init);

    const req = new Request('http://local.invalid' + raw, init);
    const hasBody = init && init.body != null;
    const body = hasBody ? new Uint8Array(await req.arrayBuffer()) : null;
    const ctype = hasBody ? req.headers.get('Content-Type') : null;
    const id = nextId++;
    return new Promise((resolve) => {
      pending.set(id, { resolve });
      worker.postMessage({ id, method: req.method, path: raw, ctype, body }, body ? [body.buffer] : []);
    });
  };
})();
