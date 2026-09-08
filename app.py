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

import analysis
import batch
import corrections
import intake
import platform_support
import preflight
import probe
import profiles
import report
import video

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


def _file_identity(path):
    """The parts of a file that make a cached measurement safe to reuse."""
    try:
        stat = os.stat(path)
    except OSError:
        return None
    if not os.path.isfile(path):
        return None
    return (os.path.abspath(path), stat.st_size, stat.st_mtime_ns,
            stat.st_ctime_ns, stat.st_dev, stat.st_ino)


# One sorted selection, kept between the intake call and the estimate that
# follows it. The lean envelope the page holds has had the probe output
# dropped — it is kilobytes a file and nothing on the page draws it — but the
# estimate needs the width, height and frame rate that were in it. Keeping it
# here beats probing the whole folder twice.
_intakes = {}
_intakes_lock = threading.Lock()
_INTAKES_KEPT = 4


def _remember_intake(envelope):
    token = secrets.token_urlsafe(12)
    with _intakes_lock:
        _intakes[token] = envelope
        while len(_intakes) > _INTAKES_KEPT:
            _intakes.pop(next(iter(_intakes)))
    return token


def _recall_intake(token):
    with _intakes_lock:
        return _intakes.get(token)


def intake_job(paths, recursive=False):
    """Sort a selection. Cheap per file, and not cheap over four hundred."""
    def work(update):
        update(stage="looking at what is here", phase="container",
               progress=0.02)

        def progress(done, total):
            update(progress=0.02 + 0.96 * (done / max(total, 1)),
                   stage="identifying %d of %d" % (done, total))

        envelope = intake.classify(paths, recursive=recursive,
                                   progress=progress)
        lean = intake.without_facts(envelope)
        lean["intake"] = _remember_intake(envelope)
        lean["profiles"] = {
            kind: [{"id": p["id"], "label": p["label"],
                    "summary": p.get("summary", ""),
                    "confidence": p.get("confidence", "informal")}
                   for p in profiles.for_kind(kind)]
            for kind in ("audio", "video", "captions")
        }
        return lean
    return start_job(work)


def estimate_run(token, assignments):
    """What the chosen work will cost, before any of it starts.

    Weighted by predicted cost rather than by file count, because a two-second
    caption check and a ninety-minute picture pass are not the same tick of a
    progress bar.
    """
    envelope = _recall_intake(token)
    if not envelope:
        raise ValueError("That selection has been forgotten. Choose the "
                         "files again.")
    facts_by_path = {i["path"]: i.get("facts") for i in envelope["items"]}

    rows, total = [], 0.0
    for choice in assignments or []:
        path = choice.get("path")
        if choice.get("action") == "skip":
            continue
        facts = facts_by_path.get(path) or {}
        profile = profiles.get(choice.get("target", "web"))
        seconds = _estimate_one(facts, profile, choice.get("depth",
                                                           "selective"))
        total += seconds
        rows.append({"path": path, "seconds": round(seconds, 1)})

    return {"files": len(rows), "seconds": round(total, 1),
            "low": round(total * 0.7), "high": round(total * 1.4),
            "per_file": rows}


def _estimate_one(facts, profile, depth):
    container = facts.get("container") or {}
    duration = container.get("duration_s") or 0.0
    stream = facts.get("video") or {}

    seconds = analysis.estimate_seconds(duration) or 0.0
    if stream:
        filters = preflight.picture_filters(profile, depth)
        seconds += video.estimate_seconds(
            filters, duration, stream.get("width"), stream.get("height"),
            stream.get("avg_frame_rate")) or 0.0
    return seconds


def _key(path, target, depth="selective"):
    identity = _file_identity(path)
    return (identity, target, depth) if identity else None


def _remember(path, target, payload, depth="selective"):
    key = _key(path, target, depth)
    if key:
        with _cache_lock:
            _cache[key] = payload
            # Nothing here is expensive to recompute and a long session should
            # not grow without limit.
            if len(_cache) > 24:
                _cache.pop(next(iter(_cache)))


def _recall(path, target, depth="selective"):
    key = _key(path, target, depth)
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


def _depth(body):
    """'full' only when asked for in those words. Anything else is selective."""
    return "full" if (body or {}).get("depth") == "full" else "selective"


def picture_plan(path, target):
    """What each depth would read, and roughly how long it would take."""
    try:
        return preflight.picture_plan(path, target)
    except Exception:
        # A file the picture pass cannot plan for is a file the check itself
        # will report on properly. Refusing to open the window over it would
        # be the wrong end to fail at.
        return None


def _refuse_mismatch(path, target, ffprobe=None):
    """A profile may not be run against a file it was never a target for.

    The page only offers compatible profiles, but a dropdown is a convenience
    and not a guarantee about what arrives: a stale page, a second window, or
    anything that is not the page at all can still ask. Refusing here means
    the answer to "can this be checked against that" is the same wherever it
    is asked.
    """
    if preflight.is_caption_file(path):
        kind = "captions"
    else:
        if ffprobe is None:
            _, ffprobe = platform_support.require_tools()
        try:
            kind = probe.kind_of(probe.inspect(path, ffprobe))
        except probe.ProbeError:
            return          # the check itself will report this properly
    profile = profiles.get(target) if not isinstance(target, dict) else target
    if kind and not profiles.accepts(profile, kind):
        WORD = {"audio": "a sound file", "video": "a video file",
                "captions": "a caption file"}
        for_kinds = " or ".join(profiles.applies_to(profile))
        raise ValueError(
            "%s is %s, and %s is a target for %s."
            % (os.path.basename(path), WORD.get(kind, "a " + kind + " file"),
               profile.get("label", target), for_kinds))


def check_job(path, target, depth="selective"):
    def work(update):
        _refuse_mismatch(path, target)
        cached = _recall(path, target, depth)
        if cached:
            update(stage="already measured", phase="target", progress=1.0)
            return cached["envelope"]

        profile = profiles.get(target)
        update(stage="reading the container", phase="container", progress=0.02)

        def progress(fraction):
            update(progress=0.05 + fraction * 0.9)

        facts, measurements, result, profile = preflight.run(
            path, profile, progress=progress, stage=_stage_reporter(update),
            depth=depth)
        update(stage="comparing against the target", phase="target",
               progress=0.97)
        envelope = report.envelope(facts, measurements, result, profile)
        envelope["chart"] = report.chart_svg(envelope, theme="auto")
        _remember(path, target, {"facts": facts, "measurements": measurements,
                                 "result": result, "profile": profile,
                                 "envelope": envelope}, depth)
        return envelope
    return start_job(work)


def batch_job(paths, target, recursive=False, depth="selective"):
    """Check a whole delivery, remembering each file so a click is instant."""
    def work(update):
        def on_file(index, total, name):
            update(phase="audio", progress=index / max(1, total),
                   stage=f"{name} — {index + 1} of {total}")

        result = batch.run(paths, target, recursive=recursive,
                           on_file=on_file, depth=depth)
        _remember_delivery([entry["path"] for entry in result["files"]],
                           target, result, depth)
        update(phase="target", stage="comparing the delivery", progress=0.98)
        envelope = report.set_envelope(result)
        envelope["kind"] = "delivery"
        envelope["chart"] = report.set_chart_svg(result, theme="auto")
        for entry in result["files"]:
            _remember(entry["path"], target, {
                "facts": entry["facts"], "measurements": entry["measurements"],
                "result": entry["result"], "profile": result["profile"],
                "envelope": entry["envelope"]}, depth)
        return envelope
    return start_job(work)


# The last delivery this session measured, kept so that planning and then
# correcting it does not measure every file a second time.
_delivery = {}
_delivery_lock = threading.Lock()


def _remember_delivery(paths, target, result, depth="selective"):
    identities = tuple(sorted(filter(None, (_file_identity(p) for p in paths))))
    if len(identities) != len(paths):
        return
    with _delivery_lock:
        _delivery.clear()
        _delivery["key"] = (identities, target, depth)
        _delivery["result"] = result


def _recall_delivery(paths, target, depth="selective"):
    identities = tuple(sorted(filter(None, (_file_identity(p) for p in paths))))
    if len(identities) != len(paths):
        return None
    with _delivery_lock:
        if _delivery.get("key") == (identities, target, depth):
            return _delivery.get("result")
    return None


def delivery_plan(paths, target, depth="selective"):
    """What it would take to make this delivery pass, in sentences."""
    result = _recall_delivery(paths, target, depth)
    if result is None:
        raise preflight.PreflightError(
            "Check the delivery before correcting it.")
    planned = batch.plan(result)
    return {
        "consensus": planned["consensus"],
        "files": [{"name": entry["name"],
                   "because_of_the_set": entry["because_of_the_set"],
                   "steps": [{"description": step["description"],
                              "caveat": step.get("caveat")}
                             for step in entry["steps"]]}
                  for entry in planned["files"]],
        "untouched": planned["untouched"],
        "unaddressed": [{"label": f["label"], "detail": f["detail"]}
                        for f in planned["unaddressed"]],
    }


def delivery_fix_job(paths, target, overwrite=False, depth="selective"):
    """Correct a delivery, then measure the delivery that came out.

    The work itself is batch.correct, which the command line calls too — the
    rebuilding it does when a file lands off target is part of the promise,
    and a window that skipped it would be quietly making a weaker one.
    """
    def work(update):
        result = _recall_delivery(paths, target, depth)
        if result is None:
            raise preflight.PreflightError(
                "Check the delivery before correcting it.")

        def on_file(index, total, name):
            update(phase="audio", progress=0.1 + 0.6 * index / max(1, total),
                   stage=f"{name} — {index + 1} of {total}")

        def say(message):
            update(phase="target", stage=message, progress=0.75)

        done = batch.correct(result, target, overwrite=overwrite,
                             on_file=on_file, on_stage=say)
        if not done["after"]:
            raise preflight.PreflightError(
                "No failing check here has a safe automatic fix."
                if not done["planned"]["files"]
                else "No corrected copy could be written.")

        after = report.set_envelope(done["after"])
        after["kind"] = "delivery"
        after["chart"] = report.set_chart_svg(done["after"], theme="auto")
        _remember_delivery(done["outputs"], target, done["after"], depth)
        for entry in done["after"]["files"]:
            _remember(entry["path"], target, {
                "facts": entry["facts"], "measurements": entry["measurements"],
                "result": entry["result"],
                "profile": done["after"]["profile"],
                "envelope": entry["envelope"]}, depth)
        return {"after": after,
                "written": [entry["output"]
                            for entry in done["written"]["written"]],
                "failed": done["written"]["failed"]}
    return start_job(work)


def plan_for(path, target, depth="selective"):
    """The correction plan, from the cached measurement or a fresh one.

    The depth has to match the check that was just run or this misses the
    cache and decodes the picture a second time — which on a feature is the
    difference between a plan appearing at once and a plan appearing after
    eighty minutes.
    """
    cached = _recall(path, target, depth)
    if not cached:
        profile = profiles.get(target)
        facts, measurements, result, profile = preflight.run(
            path, profile, depth=depth)
        cached = {"facts": facts, "measurements": measurements,
                  "result": result, "profile": profile,
                  "envelope": report.envelope(facts, measurements, result,
                                              profile)}
        _remember(path, target, cached, depth)

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


def fix_job(path, target, overwrite=False, depth="selective"):
    def work(update):
        cached = _recall(path, target, depth)
        if not cached:
            update(stage="measuring the audio", phase="audio",
                   progress=0.05)
            profile = profiles.get(target)
            facts, measurements, result, profile = preflight.run(
                path, profile, depth=depth)
            cached = {"facts": facts, "measurements": measurements,
                      "result": result, "profile": profile,
                      "envelope": report.envelope(facts, measurements, result,
                                                  profile)}
            _remember(path, target, cached, depth)

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
        after = preflight.run(written, cached["profile"], depth=depth)
        after_envelope = report.envelope(
            after[0], after[1], after[2], cached["profile"],
            corrections=[{"description": s["description"]} for s in prepared])
        after_envelope["chart"] = report.chart_svg(
            after_envelope, theme="auto", baseline=cached["envelope"])

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
            after = preflight.run(written, cached["profile"], depth=depth)
            after_envelope = report.envelope(
                after[0], after[1], after[2], cached["profile"],
                corrections=[{"description": s["description"]}
                             for s in prepared])
            after_envelope["chart"] = report.chart_svg(
                after_envelope, theme="auto", baseline=cached["envelope"])

        _remember(written, target, {
            "facts": after[0], "measurements": after[1], "result": after[2],
            "profile": cached["profile"], "envelope": after_envelope}, depth)
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

            if url.path == "/api/pick_folder":
                return self._json({"path": platform_support.pick_folder(
                    "Choose a folder of files to check")})

            if url.path == "/api/batch":
                paths = body.get("paths") or [body.get("path", "")]
                paths = [os.path.expanduser(p.strip()) for p in paths
                         if p and p.strip()]
                if not paths:
                    raise ValueError("No folder was given.")
                for path in paths:
                    if not os.path.exists(path):
                        raise ValueError(f"No such file or folder: {path}")
                return self._json({"job": batch_job(
                    paths, body.get("target", "web"),
                    bool(body.get("recursive")), _depth(body))})

            if url.path == "/api/intake":
                paths = [os.path.expanduser(p.strip())
                         for p in (body.get("paths") or []) if p and p.strip()]
                if not paths:
                    raise ValueError("Nothing was chosen.")
                for path in paths:
                    if not os.path.exists(path):
                        raise ValueError(f"No such file or folder: {path}")
                return self._json({"job": intake_job(
                    paths, bool(body.get("recursive")))})

            if url.path == "/api/estimate":
                return self._json(estimate_run(body.get("intake"),
                                               body.get("assignments")))

            if url.path == "/api/check":
                path = self._require_file(body)
                return self._json({"job": check_job(
                    path, body.get("target", "web"),
                    _depth(body))})

            if url.path == "/api/picture_plan":
                # Asked before anything is decoded, so the choice between
                # depths can be offered with a number attached rather than
                # after the wait it was meant to warn about.
                path = self._require_file(body)
                return self._json(picture_plan(
                    path, body.get("target", "web")) or {})

            if url.path == "/api/plan":
                path = self._require_file(body)
                return self._json(plan_for(path, body.get("target", "web"),
                                           _depth(body)))

            if url.path == "/api/fix":
                path = self._require_file(body)
                return self._json({"job": fix_job(
                    path, body.get("target", "web"),
                    bool(body.get("overwrite")), _depth(body))})

            if url.path == "/api/delivery_plan":
                paths = [os.path.expanduser(p) for p in
                         (body.get("paths") or [])]
                return self._json(delivery_plan(paths,
                                                body.get("target", "web"),
                                                _depth(body)))

            if url.path == "/api/delivery_fix":
                paths = [os.path.expanduser(p) for p in
                         (body.get("paths") or [])]
                return self._json({"job": delivery_fix_job(
                    paths, body.get("target", "web"),
                    bool(body.get("overwrite")), _depth(body))})

            if url.path == "/api/export":
                return self._json(self._export(body))

            if url.path == "/api/quit":
                # A double-clicked app has no terminal to press Ctrl-C in, so
                # the page it opened is the only place a person can stop it.
                threading.Timer(0.2, self.server.shutdown).start()
                return self._json({"stopping": True})

            if url.path == "/api/reveal":
                return self._json({"shown": platform_support.reveal(
                    body.get("path", ""))})
        except (preflight.PreflightError, probe.ProbeError,
                corrections.CorrectionError, platform_support.ToolsMissing,
                platform_support.PickerUnavailable, batch.BatchError,
                ValueError) as error:
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
        cached = _recall(path, target, _depth(body))
        if not cached:
            raise ValueError("Run the check before exporting its report.")
        stem = os.path.splitext(path)[0]
        # A report about a corrected copy should not be called
        # "…preflight.preflight.md".
        if stem.endswith(".preflight"):
            stem = stem[:-len(".preflight")]
        kind = body.get("kind", "markdown")
        written = []
        if kind == "json":
            out, text = stem + ".preflight.json", report.data(cached["envelope"])
        else:
            out = stem + ".preflight.md"
            drawing = report.chart_svg(cached["envelope"])
            name = None
            if drawing:
                name = os.path.basename(stem) + ".preflight.loudness.svg"
                with open(os.path.join(os.path.dirname(out), name), "w",
                          encoding="utf-8") as handle:
                    handle.write(drawing)
                written.append(name)
            text = report.markdown(cached["envelope"], name)
        with open(out, "w", encoding="utf-8") as handle:
            handle.write(text)
        return {"written": out, "alongside": written}

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
