#!/usr/bin/env python3
"""Media Preflight — the window.

A local HTTP server and a page in your browser, which is the cheapest
cross-platform window there is and the only one that needs nothing installed.
It listens on the loopback address only, and every request carries a token
minted at startup, so nothing else on the machine or the network can drive it.

    python3 app.py            opens http://127.0.0.1:8770/?t=…

Files are never uploaded. The browser cannot tell a local program where a
dropped file lives — it offers the bytes and the basename and nothing else —
so the page asks this server to open a native file dialog instead, and the
path never leaves the machine. That is not a workaround; it is the point.
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import sys
import threading
import traceback
import uuid
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import corrections
import platform_support
import preflight
import probe
import profiles
import report

HERE = os.path.dirname(os.path.abspath(__file__))
PORT = 8770
TOKEN = secrets.token_urlsafe(24)

# Finished analyses, so that planning a correction does not measure the file a
# second time. Keyed by (path, target); a file that changed on disk is measured
# again because its size and modification time are part of the key.
_cache = {}
_cache_lock = threading.Lock()

_jobs = {}
_jobs_lock = threading.Lock()


def _key(path, target):
    try:
        stat = os.stat(path)
    except OSError:
        return None
    return (os.path.abspath(path), target, stat.st_size, int(stat.st_mtime))


def _remember(path, target, payload):
    key = _key(path, target)
    if key:
        with _cache_lock:
            _cache[key] = payload
            # Nothing here is expensive to recompute and a long session should
            # not grow without limit.
            if len(_cache) > 24:
                _cache.pop(next(iter(_cache)))


def _recall(path, target):
    key = _key(path, target)
    with _cache_lock:
        return _cache.get(key) if key else None


# --------------------------------------------------------------------- jobs

def start_job(work):
    job_id = uuid.uuid4().hex
    with _jobs_lock:
        _jobs[job_id] = {"state": "running", "progress": 0.0,
                         "phase": "container",
                         "stage": "reading the container"}

    def update(**fields):
        with _jobs_lock:
            _jobs[job_id].update(fields)

    def run():
        try:
            result = work(update)
            update(state="done", progress=1.0, stage="done", result=result)
        except Exception as error:          # noqa: BLE001 - reported, not swallowed
            update(state="error", error=str(error) or error.__class__.__name__,
                   trace=traceback.format_exc())

    threading.Thread(target=run, daemon=True).start()
    return job_id


def job_state(job_id):
    with _jobs_lock:
        state = _jobs.get(job_id)
        return dict(state) if state else None


# ------------------------------------------------------------------- the work

# What each phase of a run is called in the window, and which node of the
# signal path lights up while it runs.
STAGE_WORDS = {
    "container": "reading the container",
    "audio": "measuring the audio",
    "video": "measuring the picture",
    "captions": "reading the captions",
    "target": "comparing against the target",
}


def _stage_reporter(update):
    def announce(name):
        update(phase=name, stage=STAGE_WORDS.get(name, name))
    return announce


def check_job(path, target):
    def work(update):
        cached = _recall(path, target)
        if cached:
            update(stage="already measured", phase="target", progress=1.0)
            return cached["envelope"]

        profile = profiles.get(target)
        update(stage="reading the container", phase="container", progress=0.02)

        def progress(fraction):
            update(progress=0.05 + fraction * 0.9)

        facts, measurements, result, profile = preflight.run(
            path, profile, progress=progress, stage=_stage_reporter(update))
        update(stage="comparing against the target", phase="target",
               progress=0.97)
        envelope = report.envelope(facts, measurements, result, profile)
        _remember(path, target, {"facts": facts, "measurements": measurements,
                                 "result": result, "profile": profile,
                                 "envelope": envelope})
        return envelope
    return start_job(work)


def plan_for(path, target):
    """The correction plan, from the cached measurement or a fresh one."""
    cached = _recall(path, target)
    if not cached:
        profile = profiles.get(target)
        facts, measurements, result, profile = preflight.run(path, profile)
        cached = {"facts": facts, "measurements": measurements,
                  "result": result, "profile": profile,
                  "envelope": report.envelope(facts, measurements, result,
                                              profile)}
        _remember(path, target, cached)

    planned = corrections.plan(cached["facts"], cached["measurements"],
                               cached["result"], cached["profile"])
    destination = (corrections.output_path(path, planned["steps"])
                   if planned["steps"] else None)
    ffmpeg, _ = platform_support.require_tools()
    command = None
    if planned["steps"]:
        prepared = corrections.prepare(path, planned["steps"], ffmpeg)
        command = corrections.build_command(path, destination, prepared,
                                            cached["facts"], ffmpeg)
    return {
        "steps": [{"id": s["id"], "description": s["description"],
                   "caveat": s.get("caveat")} for s in planned["steps"]],
        "unfixable": planned["unfixable"],
        "output": destination,
        "command": command,
    }


def fix_job(path, target, overwrite=False):
    def work(update):
        cached = _recall(path, target)
        if not cached:
            update(stage="measuring the audio", phase="audio",
                   progress=0.05)
            profile = profiles.get(target)
            facts, measurements, result, profile = preflight.run(path, profile)
            cached = {"facts": facts, "measurements": measurements,
                      "result": result, "profile": profile,
                      "envelope": report.envelope(facts, measurements, result,
                                                  profile)}
            _remember(path, target, cached)

        planned = corrections.plan(cached["facts"], cached["measurements"],
                                   cached["result"], cached["profile"])
        if not planned["steps"]:
            raise preflight.PreflightError(
                "No failing check here has a safe automatic fix.")

        ffmpeg, _ = platform_support.require_tools()
        update(stage="writing the corrected copy", phase="audio",
               progress=0.3)
        written, command, prepared = corrections.apply(
            path, planned["steps"], cached["facts"], ffmpeg=ffmpeg,
            overwrite=overwrite)

        update(stage="measuring the corrected copy", phase="audio",
               progress=0.6)
        after = preflight.run(written, cached["profile"])
        after_envelope = report.envelope(
            after[0], after[1], after[2], cached["profile"],
            corrections=[{"description": s["description"]} for s in prepared])

        for _ in range(2):
            if after[2]["verdict"] != "fail":
                break
            adjusted, why = corrections.refine(prepared, after[1], after[2],
                                               cached["profile"])
            if not adjusted:
                break
            update(stage=f"rebuilding from the source — {why}",
                   phase="audio", progress=0.75)
            written, command, prepared = corrections.apply(
                path, adjusted, cached["facts"], destination=written,
                ffmpeg=ffmpeg, overwrite=True)
            after = preflight.run(written, cached["profile"])
            after_envelope = report.envelope(
                after[0], after[1], after[2], cached["profile"],
                corrections=[{"description": s["description"]}
                             for s in prepared])

        _remember(written, target, {
            "facts": after[0], "measurements": after[1], "result": after[2],
            "profile": cached["profile"], "envelope": after_envelope})
        return {
            "output": written,
            "before": cached["envelope"],
            "after": after_envelope,
            "recipe": corrections.recipe(path, written, prepared, command,
                                         after_envelope),
        }
    return start_job(work)


# ------------------------------------------------------------------- server

class Handler(BaseHTTPRequestHandler):
    server_version = "MediaPreflight/" + report.VERSION

    def log_message(self, fmt, *args):
        """The terminal is for the tool's own output, not an access log."""

    # -- plumbing

    def _send(self, code, body, content_type="application/json"):
        payload = body if isinstance(body, bytes) else body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(payload)

    def _json(self, payload, code=200):
        self._send(code, json.dumps(report.jsonable(payload)))

    def _error(self, message, code=400):
        self._json({"error": message}, code)

    def _authorised(self, query):
        return secrets.compare_digest(query.get("t", [""])[0], TOKEN)

    def _body(self):
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except ValueError:
            return {}

    # -- routes

    def do_GET(self):
        url = urlparse(self.path)
        query = parse_qs(url.query)

        if url.path == "/":
            if not self._authorised(query):
                return self._send(403, "Open the address this tool printed at "
                                       "startup — it carries a one-time token.",
                                  "text/plain; charset=utf-8")
            with open(os.path.join(HERE, "index.html"), "rb") as handle:
                page = handle.read()
            return self._send(200, page, "text/html; charset=utf-8")

        if not self._authorised(query):
            return self._error("Not authorised.", 403)

        if url.path == "/api/state":
            return self._json(self._state())

        if url.path == "/api/job":
            state = job_state(query.get("id", [""])[0])
            if state is None:
                return self._error("No such job.", 404)
            state.pop("trace", None)
            return self._json(state)

        return self._error("No such route.", 404)

    def do_POST(self):
        url = urlparse(self.path)
        query = parse_qs(url.query)
        if not self._authorised(query):
            return self._error("Not authorised.", 403)
        body = self._body()

        try:
            if url.path == "/api/pick":
                return self._json({"path": platform_support.pick_file()})

            if url.path == "/api/check":
                path = self._require_file(body)
                return self._json({"job": check_job(path, body.get("target",
                                                                   "web"))})

            if url.path == "/api/plan":
                path = self._require_file(body)
                return self._json(plan_for(path, body.get("target", "web")))

            if url.path == "/api/fix":
                path = self._require_file(body)
                return self._json({"job": fix_job(
                    path, body.get("target", "web"),
                    bool(body.get("overwrite")))})

            if url.path == "/api/export":
                return self._json(self._export(body))

            if url.path == "/api/reveal":
                return self._json({"shown": platform_support.reveal(
                    body.get("path", ""))})
        except (preflight.PreflightError, probe.ProbeError,
                corrections.CorrectionError, platform_support.ToolsMissing,
                platform_support.PickerUnavailable, ValueError) as error:
            return self._error(str(error))

        return self._error("No such route.", 404)

    def _require_file(self, body):
        path = os.path.expanduser((body.get("path") or "").strip())
        if not path:
            raise ValueError("No file was given.")
        if not os.path.isfile(path):
            raise ValueError(f"No such file: {path}")
        return os.path.abspath(path)

    def _export(self, body):
        """Write the report beside the file it describes."""
        path = self._require_file(body)
        target = body.get("target", "web")
        cached = _recall(path, target)
        if not cached:
            raise ValueError("Run the check before exporting its report.")
        stem = os.path.splitext(path)[0]
        # A report about a corrected copy should not be called
        # "…preflight.preflight.md".
        if stem.endswith(".preflight"):
            stem = stem[:-len(".preflight")]
        kind = body.get("kind", "markdown")
        if kind == "json":
            out, text = stem + ".preflight.json", report.data(cached["envelope"])
        else:
            out, text = stem + ".preflight.md", report.markdown(cached["envelope"])
        with open(out, "w", encoding="utf-8") as handle:
            handle.write(text)
        return {"written": out}

    def _state(self):
        ffmpeg = platform_support.find_ffmpeg()
        return {
            "tool": {"name": report.NAME, "version": report.VERSION},
            "ffmpeg": ffmpeg,
            "ffmpeg_hint": platform_support.install_hint(),
            "targets": [{"id": p["id"], "label": p["label"],
                         "summary": p["summary"],
                         "confidence": p.get("confidence"),
                         "source": p.get("source")}
                        for p in profiles.all_profiles()],
        }


def serve(port=PORT, open_browser=True):
    address = f"http://127.0.0.1:{port}/?t={TOKEN}"
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"{report.NAME} {report.VERSION}")
    print(f"  {address}")
    if not platform_support.find_ffmpeg():
        print(f"  ffmpeg was not found — install it with: "
              f"{platform_support.install_hint()}")
    print("  Ctrl-C to stop.")
    if open_browser:
        threading.Timer(0.4, webbrowser.open, args=(address,)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        server.server_close()


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--port", type=int, default=PORT)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    serve(args.port, not args.no_browser)


if __name__ == "__main__":
    main()
