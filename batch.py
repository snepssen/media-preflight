"""Checking a delivery, rather than a file.

An audiobook is thirty files and ACX accepts or rejects the title. A podcast
season is a folder. Almost nothing anybody delivers is one file, and some of
the requirements are properties of the *set* rather than of any member of it:
ACX asks that every file in a title share a channel count and a sample rate,
and a rule written against one file cannot say that. A title where chapter
twelve is stereo and everything else is mono passes thirty individual checks
and is rejected on submission.

So this module runs the per-file check over many files and then measures the
set itself: how many distinct sample rates are present, how far apart the
loudest and quietest files are, which file is the odd one out. Those become
findings of their own, with the offending *files* named the way a per-file
finding names timestamps.
"""

from __future__ import annotations

import os
import re

import checks
import platform_support
import preflight
import profiles
import report

# The tool's own output, which must never be swept back in as input. A folder
# checked twice would otherwise start checking its own corrected copies, and a
# corrected copy of a corrected copy is nobody's idea of a delivery.
OUTPUT_MARKER = ".preflight."


class BatchError(RuntimeError):
    """Nothing to check, or nothing checkable, with the reason."""


def collect(paths, recursive=False, extensions=None):
    """Turn files and folders into an ordered list of files to check."""
    extensions = extensions or (platform_support.MEDIA_EXTS
                                + platform_support.CAPTION_EXTS)
    found = []
    for path in paths:
        path = os.path.expanduser(path)
        if os.path.isfile(path):
            found.append(os.path.abspath(path))
        elif os.path.isdir(path):
            found.extend(_scan(path, recursive, extensions))
        else:
            raise BatchError(f"No such file or folder: {path}")

    seen, ordered = set(), []
    for path in sorted(found, key=natural_key):
        if path in seen:
            continue
        seen.add(path)
        ordered.append(path)
    if not ordered:
        raise BatchError(
            "Nothing to check. Looked for: "
            + ", ".join("." + e for e in sorted(extensions)[:8]) + "…")
    return ordered


def _scan(folder, recursive, extensions):
    out = []
    for root, folders, files in os.walk(folder):
        folders[:] = sorted(f for f in folders if not f.startswith("."))
        for name in files:
            if name.startswith(".") or OUTPUT_MARKER in name:
                continue
            if name.rsplit(".", 1)[-1].lower() in extensions:
                out.append(os.path.join(root, name))
        if not recursive:
            break
    return out


_NUMBER = re.compile(r"(\d+)")


def natural_key(path):
    """Sort 'Chapter 2' before 'Chapter 10', which plain sorting does not.

    A delivery is ordered, and a report that lists chapter 10 second is a
    report somebody has to re-sort in their head before they can read it.
    """
    name = os.path.basename(path)
    return [int(part) if part.isdigit() else part.lower()
            for part in _NUMBER.split(name)]


# ------------------------------------------------------------------- running

def run(paths, target="web", ffmpeg=None, ffprobe=None, recursive=False,
        progress=None, on_file=None):
    """Check every file against one target, then check the set. """
    if ffmpeg is None or ffprobe is None:
        ffmpeg, ffprobe = platform_support.require_tools()
    profile = profiles.get(target) if not isinstance(target, dict) else target
    files = collect(paths, recursive)

    done, failed = [], []
    for index, path in enumerate(files):
        if on_file:
            on_file(index, len(files), os.path.basename(path))
        try:
            facts, measurements, result, _ = preflight.run(
                path, profile, ffmpeg, ffprobe,
                progress=_slice(progress, index, len(files)))
        except Exception as error:            # noqa: BLE001 — reported, not lost
            failed.append({"path": path, "name": os.path.basename(path),
                           "error": str(error) or error.__class__.__name__})
            continue
        done.append({
            "path": path,
            "name": os.path.basename(path),
            "facts": facts,
            "measurements": measurements,
            "result": result,
            "envelope": report.envelope(facts, measurements, result, profile),
        })

    if not done:
        raise BatchError(
            "None of these files could be measured:\n  "
            + "\n  ".join(f"{f['name']}: {f['error']}" for f in failed))

    set_measurements = measure_set(done, profile)
    set_result = checks.evaluate_set(set_measurements, profile)
    return {
        "profile": profile,
        "files": done,
        "unreadable": failed,
        "set_measurements": set_measurements,
        "set_result": set_result,
        "verdict": _verdict(done, set_result, failed),
    }


def _slice(progress, index, total):
    if progress is None:
        return None
    return lambda fraction: progress((index + fraction) / total)


def _verdict(done, set_result, failed):
    if failed or set_result["verdict"] == "fail" or \
            any(f["result"]["verdict"] == "fail" for f in done):
        return "fail"
    if set_result["verdict"] == "warn" or \
            any(f["result"]["verdict"] == "warn" for f in done):
        return "warn"
    return "pass"


# ----------------------------------------------------------------- measuring

# Properties every file in a delivery is normally expected to share. Each one
# becomes a grouping of files by value, so a rule can ask how many distinct
# values there are and the finding can name the files in the minority.
SHARED_PROPERTIES = {
    "channels": lambda f: (f["facts"].get("audio") or {}).get("channels"),
    "sample_rate": lambda f: (f["facts"].get("audio") or {}).get("sample_rate"),
    "codec": lambda f: (f["facts"].get("audio") or {}).get("codec"),
    "container": lambda f: f["facts"]["container"].get("format_name"),
    "bitrate_mode": lambda f: f["measurements"].get("bitrate_mode"),
    "bit_depth": lambda f: (f["facts"].get("audio") or {}).get(
        "bits_per_sample") or None,
}


def measure_set(files, profile):
    """The properties of the delivery as a whole."""
    groups = {}
    for name, read in SHARED_PROPERTIES.items():
        grouped = {}
        for entry in files:
            value = read(entry)
            if value in (None, "", 0):
                continue
            grouped.setdefault(str(value), []).append(entry["name"])
        if grouped:
            groups[name] = grouped

    durations = [f["measurements"].get("duration_s")
                 or f["facts"]["container"].get("duration_s") or 0.0
                 for f in files]
    loudness = _loudness_across(files, profile)
    peaks = _spread(files, "peak_dbfs")

    out = {
        "file_count": len(files),
        "failing_files": sum(1 for f in files
                             if f["result"]["verdict"] == "fail"),
        "warning_files": sum(1 for f in files
                             if f["result"]["verdict"] == "warn"),
        "total_duration_s": round(sum(durations), 3),
        "longest_file_s": round(max(durations), 3) if durations else 0.0,
        "groups": groups,
        "loudness": loudness,
        "peak": peaks,
    }
    for name, grouped in groups.items():
        out[f"{name}_distinct"] = len(grouped)
        out[f"{name}_odd"] = odd_ones_out(grouped)
    return out


def _loudness_across(files, profile):
    """Compare the files in whichever loudness the target actually states.

    A target written in LUFS is compared in LUFS and one written in RMS in RMS.
    Mixing them to get a single "loudness spread" would be the same invention
    the per-file report refuses when it declines to timestamp an RMS failure.
    """
    metrics = {rule["metric"] for rule in profile.get("rules", [])}
    if "integrated_lufs" in metrics:
        return _spread(files, "integrated_lufs", "LUFS")
    if "rms_dbfs" in metrics:
        return _spread(files, "rms_dbfs", "dBFS RMS")
    return {"absent": "This target states no loudness requirement, so there "
                      "is nothing to compare the files in."}


def _spread(files, key, unit="dB"):
    values = [(f["name"], f["measurements"].get(key)) for f in files]
    values = [(name, value) for name, value in values
              if isinstance(value, (int, float)) and value == value
              and abs(value) != float("inf")]
    if len(values) < 2:
        return {"absent": "Fewer than two files could be measured for this."}
    ordered = sorted(values, key=lambda pair: pair[1])
    spread = ordered[-1][1] - ordered[0][1]
    return {
        "unit": unit,
        "metric": key,
        "min": round(ordered[0][1], 2),
        "max": round(ordered[-1][1], 2),
        "spread": round(spread, 2),
        "quietest": ordered[0][0],
        "loudest": ordered[-1][0],
        "outliers": outliers(ordered, spread),
        "per_file": [{"name": name, "value": round(value, 2)}
                     for name, value in values],
    }


def outliers(ordered, spread):
    """The files somebody actually has to touch.

    Naming the loudest and the quietest is the obvious answer and usually the
    wrong one: when four chapters agree and a fifth is four decibels up, the
    quietest of the four is not at fault — it is the reference. So the files
    reported are the ones further from the middle of the set than half its
    spread, and when that describes nobody the two ends are named instead.
    """
    if spread <= 0 or len(ordered) < 3:
        return [name for name, _ in (ordered[:1] + ordered[-1:])]
    middle = ordered[len(ordered) // 2][1]
    far = [name for name, value in ordered
           if abs(value - middle) > spread / 2.0]
    return sorted(far) or [ordered[0][0], ordered[-1][0]]


def odd_ones_out(grouped):
    """The files not in the largest group — the ones somebody has to look at.

    When two groups are the same size there is no odd one out, only a
    disagreement, and naming half the delivery as the exception would be
    picking a side arbitrarily.
    """
    if len(grouped) < 2:
        return []
    sizes = sorted((len(names) for names in grouped.values()), reverse=True)
    if sizes[0] == sizes[1]:
        return sorted(name for names in grouped.values() for name in names)
    largest = max(grouped.values(), key=len)
    return sorted(name for names in grouped.values() if names is not largest
                  for name in names)
