# Browser version

This folder builds a version of Selective Memory that runs entirely in a web
browser. People open a link and use the same interface as the local app, with
no Python, terminal, or install. This makes the tool usable by participants in
research studies and by other non-technical users.

Uploaded files are processed inside the browser tab and never sent to a server.
The page talks to no server at all after it loads.

## How it works

The browser version runs the existing Python code in `data-portability-app/`
(the Flask app, parsers, sensitivity rules, and redaction) without changes,
using [Pyodide](https://pyodide.org) (Python compiled to WebAssembly).

- `worker.js` starts Pyodide in a Web Worker, loads pandas and Flask, and
  imports `app.py`. Requests are answered with Flask's built-in test client.
- `bridge.js` is loaded before `static/app.js`. It sends the front end's
  `fetch('/api/...')` calls to the worker and passes all other requests through
  unchanged. It also shows a short loading message while Python starts.
- `build.py` assembles the static site: it turns `templates/index.html` into a
  plain HTML file, copies `static/` and the Python modules, and packages Pyodide
  and the pure-Python Flask wheels.

The only change outside this folder is in `static/app.js`: the download link
now fetches the exported file and links to it as a blob URL. This works for
both the local server and the browser build.

CDT's test suite (68 tests) passes when run under the same Pyodide runtime.

## Build

```bash
python web/build.py                # writes web/dist/, Pyodide bundled (about 22 MB)
python web/build.py --pyodide cdn  # smaller site; Pyodide loads from cdn.jsdelivr.net
```

The bundled option downloads the Pyodide release from GitHub once (about
360 MB) and keeps only the files the app needs. With `--pyodide cdn`, the
browser downloads the Python runtime from jsDelivr. Users' data still never
leaves their browser either way.

To try it locally:

```bash
python -m http.server -d web/dist
# open http://localhost:8000
```

Opening `dist/index.html` directly by double-clicking doesn't work: browsers
block Web Workers on `file://` pages, so the site has to be served.

## Deploy

`.github/workflows/pages.yml` builds the site and publishes it to GitHub Pages
on every push to `main`. To turn it on, go to **Settings → Pages** and set
**Source** to **GitHub Actions**. The site will then be at
`https://<owner>.github.io/<repo>/`.

`dist/` is a folder of static files, so any static host also works.

## Performance

The first visit downloads about 22 MB, which the browser then caches. Startup
takes 5 to 10 seconds. In testing, a 19 MB Claude export with 4,000
conversations took about 20 seconds to parse. Desktop browsers work best for
large exports.
