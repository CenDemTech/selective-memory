/* Runs CDT's original Python backend (Flask app + parsers) inside the browser
   with Pyodide. Requests the page would have sent to the local server arrive
   here as messages and are answered by Flask's built-in test client, so the
   Python code runs unchanged and nothing is sent over the network. */

importScripts('config.js');
const PYODIDE_INDEX = new URL(self.PYODIDE_INDEX || 'pyodide/', self.location.href).href;
importScripts(PYODIDE_INDEX + 'pyodide.js');
importScripts('flask-wheels.js');

const PY_FILES = self.PY_FILES;

const BRIDGE_PY = `
import sys
sys.path.insert(0, '/home/pyodide/app')
from pyodide.ffi import to_js
from app import app as flask_app

_client = flask_app.test_client()

def handle(method, path_qs, ctype, body):
    path, _, qs = path_qs.partition('?')
    kwargs = {'method': method, 'query_string': qs}
    if body is not None:
        kwargs['data'] = bytes(body.to_py())
    if ctype:
        kwargs['content_type'] = ctype
    resp = _client.open(path, **kwargs)
    return to_js([resp.status_code, resp.headers.get('Content-Type', ''), resp.get_data()])
`;

const abs = (p) => new URL(p, self.location.href).href;
const progress = (text) => self.postMessage({ type: 'progress', text });

const ready = (async () => {
  progress('Getting things ready…');
  const pyodide = await loadPyodide({ indexURL: PYODIDE_INDEX });
  await pyodide.loadPackage(['pandas', 'jinja2', 'click', 'markupsafe'], { messageCallback: () => {} });
  for (const b64 of Object.values(self.FLASK_WHEELS)) {
    const bytes = Uint8Array.from(atob(b64), (c) => c.charCodeAt(0));
    pyodide.unpackArchive(bytes.buffer, 'wheel');
  }
  progress('Almost ready…');
  pyodide.FS.mkdirTree('/home/pyodide/app');
  for (const name of PY_FILES) {
    const res = await fetch(abs('py/' + name + '.txt'));
    if (!res.ok) throw new Error('Could not load ' + name);
    pyodide.FS.writeFile('/home/pyodide/app/' + name, await res.text());
  }
  pyodide.runPython(BRIDGE_PY);
  return pyodide.globals.get('handle');
})();

(async () => {
  try {
    await ready;
    self.postMessage({ type: 'ready' });
  } catch (err) {
    self.postMessage({ type: 'failed', text: String((err && err.message) || err) });
  }
})();

self.onmessage = async (event) => {
  const { id, method, path, ctype, body } = event.data;
  try {
    const handle = await ready;
    const [status, contentType, bytes] = handle(method, path, ctype || undefined, body || undefined);
    self.postMessage({ type: 'response', id, status, contentType, body: bytes }, [bytes.buffer]);
  } catch (err) {
    self.postMessage({ type: 'response', id, error: String(err && err.message || err) });
  }
};
