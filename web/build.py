#!/usr/bin/env python3
"""Build the browser version of Selective Memory as a static site.

The site runs the unmodified Flask app from ../data-portability-app inside the
browser with Pyodide (Python compiled to WebAssembly). Participants open a link
and nothing has to be installed. Uploaded files are processed in the browser
tab and never sent to a server.

Usage:
    python web/build.py                # self-hosted Pyodide (default)
    python web/build.py --pyodide cdn  # load Pyodide from cdn.jsdelivr.net
    python web/build.py --out site     # choose the output folder

Output goes to web/dist/ by default. Serve it with any static host (GitHub
Pages, Netlify, a university web server). To try it locally:
    python -m http.server -d web/dist
"""

import argparse
import base64
import html as html_lib
import io
import json
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
from pathlib import Path

PYODIDE_VERSION = "0.29.0"
PYODIDE_CDN = f"https://cdn.jsdelivr.net/pyodide/v{PYODIDE_VERSION}/full/"
PYODIDE_TARBALL = (
    f"https://github.com/pyodide/pyodide/releases/download/"
    f"{PYODIDE_VERSION}/pyodide-{PYODIDE_VERSION}.tar.bz2"
)
PYODIDE_CORE = [
    "pyodide.js",
    "pyodide.asm.js",
    "pyodide.asm.wasm",
    "python_stdlib.zip",
    "pyodide-lock.json",
]
# Packages Pyodide ships prebuilt for WebAssembly (loaded with loadPackage).
PYODIDE_PACKAGES = ["pandas", "jinja2", "click", "markupsafe"]
# Pure-Python packages Flask needs that Pyodide doesn't ship. Pinned to the
# versions this build was tested with.
PYPI_WHEELS = [
    "flask==3.1.3",
    "werkzeug==3.1.9",
    "itsdangerous==2.2.0",
    "blinker==1.9.0",
]

HERE = Path(__file__).resolve().parent
APP = HERE.parent / "data-portability-app"

# Search and link-preview metadata, matching what jekyll-seo-tag would emit.
# The site isn't built with Jekyll (it is uploaded as-is), so build.py writes
# the tags itself.
SITE_NAME = "Selective Memory"
SITE_URL = "https://cendemtech.github.io/selective-memory/"
SITE_DESCRIPTION = (
    "Selective Memory offers AI data portability on your terms, not AI "
    "companies. You can edit your downloaded AI chatbot conversations before "
    "you upload them to a new service, picking and choosing which "
    "conversations you want to include, and redacting potentially sensitive "
    "data."
)
# Link-preview image (web/social-preview.png, copied to the site root).
SITE_IMAGE = "social-preview.png"
SITE_IMAGE_SIZE = (1280, 640)
SITE_IMAGE_ALT = "CDT AI Governance Lab"
PUBLISHER = "Center for Democracy & Technology"


def log(msg: str) -> None:
    """Print a build progress message."""
    print(f"[build] {msg}", flush=True)


def seo_tags(site_url: str) -> str:
    """Return the <head> tags for search engines and link previews.

    Mirrors jekyll-seo-tag's output: description, canonical URL, Open Graph,
    Twitter card, and schema.org JSON-LD.
    """
    url = site_url.rstrip("/") + "/"
    image = url + SITE_IMAGE
    attr = html_lib.escape
    ld = {
        "@context": "https://schema.org",
        "@type": "WebSite",
        "name": SITE_NAME,
        "headline": SITE_NAME,
        "description": SITE_DESCRIPTION,
        "url": url,
        "image": image,
        "publisher": {
            "@type": "Organization",
            "name": PUBLISHER,
            "logo": {"@type": "ImageObject", "url": image},
        },
    }
    # "</" can't appear inside a <script> block.
    ld_json = json.dumps(ld, ensure_ascii=False).replace("</", "<\\/")
    return (
        "  <!-- SEO tags written by web/build.py -->\n"
        f'  <meta name="description" content="{attr(SITE_DESCRIPTION)}">\n'
        f'  <link rel="canonical" href="{attr(url)}">\n'
        f'  <meta property="og:title" content="{attr(SITE_NAME)}">\n'
        f'  <meta property="og:site_name" content="{attr(SITE_NAME)}">\n'
        f'  <meta property="og:description" content="{attr(SITE_DESCRIPTION)}">\n'
        f'  <meta property="og:url" content="{attr(url)}">\n'
        f'  <meta property="og:image" content="{attr(image)}">\n'
        f'  <meta property="og:image:width" content="{SITE_IMAGE_SIZE[0]}">\n'
        f'  <meta property="og:image:height" content="{SITE_IMAGE_SIZE[1]}">\n'
        f'  <meta property="og:image:alt" content="{attr(SITE_IMAGE_ALT)}">\n'
        '  <meta property="og:type" content="website">\n'
        '  <meta property="og:locale" content="en_US">\n'
        '  <meta name="twitter:card" content="summary_large_image">\n'
        f'  <meta name="twitter:title" content="{attr(SITE_NAME)}">\n'
        f'  <meta name="twitter:description" content="{attr(SITE_DESCRIPTION)}">\n'
        f'  <meta name="twitter:image" content="{attr(image)}">\n'
        f'  <meta name="twitter:image:alt" content="{attr(SITE_IMAGE_ALT)}">\n'
        f'  <script type="application/ld+json">{ld_json}</script>\n'
    )


def build_page(out: Path, site_url: str) -> None:
    """Write index.html from the Flask template, with static paths."""
    html = (APP / "templates" / "index.html").read_text(encoding="utf-8")
    html, n = re.subn(
        r"\{\{\s*url_for\('static',\s*filename='([^']+)'\)\s*\}\}",
        r"static/\1",
        html,
    )
    if "{{" in html or "{%" in html:
        sys.exit("index.html has template tags build.py doesn't handle")
    html = html.replace(
        "local app and deleted immediately when you close the session.",
        "app running inside your browser, and is cleared when you close "
        "this tab.",
    )
    app_js = '<script src="static/app.js"></script>'
    if app_js not in html:
        sys.exit("could not find the app.js script tag in index.html")
    bridge = '<script src="bridge.js"></script>\n'
    html = html.replace(app_js, bridge + app_js)
    title = "</title>\n"
    if title not in html:
        sys.exit("could not find the </title> tag in index.html")
    html = html.replace(title, title + seo_tags(site_url), 1)
    (out / "index.html").write_text(html, encoding="utf-8")
    log(f"index.html ({n} static links rewritten)")


def copy_python(out: Path) -> list[str]:
    """Copy the app's Python modules (not tests) and return their names.

    They are saved as .txt so every static host serves them as plain files.
    """
    names = sorted(
        p.name
        for p in APP.glob("*.py")
        if not p.name.startswith(("test_", "conftest"))
    )
    (out / "py").mkdir()
    for name in names:
        shutil.copy(APP / name, out / "py" / f"{name}.txt")
    log(f"python modules: {', '.join(names)}")
    return names


def build_flask_wheels(out: Path) -> None:
    """Download Flask's pure-Python wheels and embed them in a JS file."""
    with tempfile.TemporaryDirectory() as tmp:
        subprocess.run(
            [sys.executable, "-m", "pip", "download", "-q", "--no-deps",
             "--only-binary=:all:", "-d", tmp, *PYPI_WHEELS],
            check=True,
        )
        wheels = {
            p.stem: base64.b64encode(p.read_bytes()).decode()
            for p in Path(tmp).glob("*.whl")
        }
    if len(wheels) != len(PYPI_WHEELS):
        sys.exit(f"expected {len(PYPI_WHEELS)} wheels, got {sorted(wheels)}")
    (out / "flask-wheels.js").write_text(
        "/* Flask and the pure-Python packages it needs "
        "(base64-encoded wheels from PyPI). */\n"
        f"self.FLASK_WHEELS = {json.dumps(wheels)};\n",
        encoding="utf-8",
    )
    log(f"flask-wheels.js ({', '.join(sorted(wheels))})")


def fetch_pyodide(out: Path) -> None:
    """Download Pyodide and keep only the core files and needed packages."""
    log(f"downloading Pyodide {PYODIDE_VERSION} (about 360 MB, one time)")
    target = out / "pyodide"
    target.mkdir()
    with urllib.request.urlopen(PYODIDE_TARBALL) as resp:
        data = resp.read()
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:bz2") as tar:
        lock_file = tar.extractfile("pyodide/pyodide-lock.json")
        lock = json.load(lock_file)["packages"]
        needed, todo = set(), list(PYODIDE_PACKAGES)
        while todo:
            name = todo.pop()
            if name not in needed:
                needed.add(name)
                todo.extend(lock[name]["depends"])
        wheels = [lock[name]["file_name"] for name in sorted(needed)]
        files = PYODIDE_CORE + wheels
        for name in files:
            member = tar.extractfile(f"pyodide/{name}")
            (target / name).write_bytes(member.read())
    packages = ", ".join(sorted(needed))
    log(f"pyodide/ ({len(files)} files: core + {packages})")


def main() -> None:
    """Parse arguments and build the site."""
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--out",
        default=str(HERE / "dist"),
        help="output folder (default: web/dist)",
    )
    parser.add_argument(
        "--pyodide",
        choices=["local", "cdn"],
        default="local",
        help="bundle Pyodide in the site (local) or load it from jsDelivr",
    )
    parser.add_argument(
        "--site-url",
        default=SITE_URL,
        help=f"public URL of the site, for SEO tags (default: {SITE_URL})",
    )
    args = parser.parse_args()

    out = Path(args.out).resolve()
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)

    shutil.copytree(APP / "static", out / "static")
    # Example exports for the "Try it with a sample file" button.
    (out / "sample_data").mkdir()
    for sample in (APP / "sample_data").glob("*.json"):
        shutil.copy(sample, out / "sample_data" / sample.name)
    for name in ["bridge.js", "worker.js", SITE_IMAGE]:
        shutil.copy(HERE / name, out / name)
    shutil.copy(HERE.parent / "LICENSE", out / "LICENSE.txt")
    (out / ".nojekyll").write_text("")  # GitHub Pages: serve files as-is

    build_page(out, args.site_url)
    py_files = copy_python(out)
    build_flask_wheels(out)

    if args.pyodide == "local":
        fetch_pyodide(out)
        index = "pyodide/"
    else:
        index = PYODIDE_CDN
    (out / "config.js").write_text(
        "/* Written by web/build.py. */\n"
        f"self.PYODIDE_INDEX = {json.dumps(index)};\n"
        f"self.PY_FILES = {json.dumps(py_files)};\n",
        encoding="utf-8",
    )
    log(f"done: {out}")


if __name__ == "__main__":
    main()
