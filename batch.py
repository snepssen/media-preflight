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
import corrections
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


# Collecting for the intake stage rather than for a check: everything found
# is kept, including files this tool cannot read. A selection that silently
# loses four files is worse than one that names them and says why.
ANY_EXTENSION = object()


def collect(paths, recursive=False, extensions=None):
    """Turn files and folders into an ordered list of files to check."""
    if extensions is not ANY_EXTENSION:
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
        if extensions is ANY_EXTENSION:
            raise BatchError("Nothing there to look at.")
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
            if extensions is ANY_EXTENSION:
                out.append(os.path.join(root, name))
            elif name.rsplit(".", 1)[-1].lower() in extensions:
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
        progress=None, on_file=None, depth="selective", width_divide=None):
    """Check every file against one target, then check the set.

    ``depth`` is passed straight through to every file. A delivery measured
    two ways is not one delivery, so it is decided once here rather than per
    file.
    """
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
                progress=_slice(progress, index, len(files)),
                depth=depth, width_divide=width_divide)
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
        "depth": depth,
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

    order = numbering([entry["name"] for entry in files])
    out = {
        "numbering": order,
        "missing_files": order["missing"],
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


# --------------------------------------------------------------- correcting

# How far a file may sit from the delivery's common level before it is worth
# moving. Below this the correction is inaudible and the re-encode is not.
LEVEL_TOLERANCE_DB = 0.3


def consensus(result, profile):
    """What the files of this delivery should agree on, and at what level.

    The majority decides the format questions, because a title is almost never
    wrong in the majority — one chapter exported with the wrong preset is the
    shape this fault actually takes. The level is decided by the target's own
    band rather than by the majority: bringing four quiet chapters up to meet
    a fifth would satisfy the set rule by making every file wrong.
    """
    measurements = result["set_measurements"]
    groups = measurements.get("groups") or {}
    out = {"channels": None, "sample_rate": None, "loudness_target": None,
           "loudness_metric": None, "reasons": []}

    for name in ("channels", "sample_rate"):
        grouped = groups.get(name) or {}
        if len(grouped) < 2:
            continue
        largest = max(grouped.values(), key=len)
        sizes = sorted((len(files) for files in grouped.values()), reverse=True)
        if sizes[0] == sizes[1]:
            out["reasons"].append(
                f"The delivery is evenly split on {name.replace('_', ' ')}, so "
                f"there is no majority to follow and nothing is changed. "
                f"Choose one and run again with a profile that states it.")
            continue
        value = next(key for key, files in grouped.items() if files is largest)
        out[name] = int(value)
        out["reasons"].append(
            f"{len(largest)} of {measurements['file_count']} files are "
            f"{value} {name.replace('_', ' ')}; the rest are brought to match.")

    loudness = measurements.get("loudness") or {}
    metric = loudness.get("metric")
    rule = _loudness_rule(profile, metric)
    if metric and rule is not None:
        target = corrections._target_band(rule)
        if target is not None:
            out["loudness_metric"] = metric
            out["loudness_target"] = target
            unit = loudness.get("unit", "dB")
            out["reasons"].append(
                f"Every file is brought to {target:g} {unit}, the middle of "
                f"what this target asks for — not to the average of the files, "
                f"which would satisfy the set and fail the target.")
    return out


def _loudness_rule(profile, metric):
    for rule in profile.get("rules", []):
        if rule["metric"] == metric:
            return rule
    return None


def plan(result, profile=None):
    """What it would take to make this delivery pass, file by file."""
    profile = profile or result["profile"]
    agreed = consensus(result, profile)
    set_findings = {f["id"]: f for f in result["set_result"]["findings"]}

    files, unfixable = [], []
    for entry in result["files"]:
        extra = _set_derived_findings(entry, agreed, profile, set_findings)
        findings = list(entry["result"]["findings"]) + extra
        overrides = _overrides_for(entry, agreed, extra)
        planned = corrections.plan(entry["facts"], entry["measurements"],
                                   {"findings": findings}, profile, overrides)
        files.append({
            "path": entry["path"],
            "name": entry["name"],
            "steps": planned["steps"],
            "because_of_the_set": [f["id"] for f in extra],
            "unfixable": planned["unfixable"],
        })
        unfixable.extend(planned["unfixable"])

    return {
        "consensus": agreed,
        "files": [f for f in files if f["steps"]],
        "untouched": [f["name"] for f in files if not f["steps"]],
        "unfixable": unfixable,
        "unaddressed": [f for f in result["set_result"]["findings"]
                        if f["status"] == "fail"
                        and f["metric"] not in _ADDRESSABLE],
    }


# The cross-file faults a correction can actually do something about.
_ADDRESSABLE = {"set_channels_distinct", "set_sample_rate_distinct",
                "set_loudness_spread_db"}


def _set_derived_findings(entry, agreed, profile, set_findings):
    """Faults this file does not have, which the delivery does.

    These are synthesised in the shape of ordinary findings so that the
    existing planner handles them — guards, ordering, caveats and all. A file
    four decibels above its neighbours has broken no per-file rule; it is the
    set that is wrong, and this is how the set says so about one member.
    """
    out = []
    audio = entry["facts"].get("audio") or {}

    if agreed.get("channels") and audio.get("channels") != agreed["channels"]:
        out.append({
            "id": "channels", "label": "Channel count across the delivery",
            "metric": "channels", "status": "fail", "fix": "encode",
            "note": "", "intervals": [], "timestamps": [],
            "actual": str(audio.get("channels")),
            "required": str(agreed["channels"]),
        })

    if agreed.get("sample_rate") and \
            audio.get("sample_rate") != agreed["sample_rate"]:
        out.append({
            "id": "sample_rate", "label": "Sample rate across the delivery",
            "metric": "sample_rate", "status": "fail", "fix": "encode",
            "note": "", "intervals": [], "timestamps": [],
            "actual": str(audio.get("sample_rate")),
            "required": str(agreed["sample_rate"]),
        })

    spread = set_findings.get("loudness") or set_findings.get("set_loudness")
    if agreed.get("loudness_target") is not None and spread and \
            spread["status"] in ("fail", "warn"):
        metric = agreed["loudness_metric"]
        measured = entry["measurements"].get(metric)
        if isinstance(measured, (int, float)) and \
                abs(measured - agreed["loudness_target"]) > LEVEL_TOLERANCE_DB:
            rule = _loudness_rule(profile, metric)
            out.append({
                "id": rule["id"] if rule else "integrated",
                "label": "Level across the delivery", "metric": metric,
                "status": "fail", "fix": "gain", "note": "",
                "intervals": [], "timestamps": [],
                "actual": f"{measured:.2f}",
                "required": f"{agreed['loudness_target']:g}",
            })
    return out


def _overrides_for(entry, agreed, extra):
    ids = {f["id"] for f in extra}
    overrides = {}
    if "channels" in ids:
        overrides["channels"] = agreed["channels"]
    if "sample_rate" in ids:
        overrides["sample_rate"] = agreed["sample_rate"]
    if agreed.get("loudness_target") is not None:
        overrides["loudness_target"] = agreed["loudness_target"]
        overrides["loudness_metric"] = agreed["loudness_metric"]
    if ids & {"channels", "sample_rate"}:
        overrides["force_encode"] = True
    return overrides


def correct(result, target, ffmpeg=None, overwrite=False, directory=None,
            on_file=None, on_stage=None, rounds=2):
    """Plan, write, measure, and where necessary rebuild — the whole promise.

    This is one function rather than two so that the window and the command
    line cannot drift apart on it. The rebuilding is not an optimisation: a
    stereo chapter downmixed to mono comes back at a level the plan could not
    have predicted, because how much a downmix costs depends on how alike the
    two channels were. So the delivery is measured and the files that landed
    off target are built again **from their sources**, never from the copies,
    which keeps the number of lossy encodes at one however many rounds it
    takes.
    """
    if ffmpeg is None:
        ffmpeg, _ = platform_support.require_tools()
    say = on_stage or (lambda message: None)

    planned = plan(result)
    if not planned["files"]:
        return {"planned": planned, "written": None, "after": None,
                "outputs": []}

    facts = {entry["path"]: entry["facts"] for entry in result["files"]}
    written = apply(planned, facts, ffmpeg=ffmpeg, overwrite=overwrite,
                    directory=directory, on_file=on_file)
    if not written["written"]:
        return {"planned": planned, "written": written, "after": None,
                "outputs": []}

    untouched = [entry["path"] for entry in result["files"]
                 if entry["name"] in planned["untouched"]]
    outputs = [entry["output"] for entry in written["written"]]

    say("measuring the corrected delivery")
    depth = result.get("depth", "selective")
    after = run(outputs + untouched, target, ffmpeg, on_file=on_file,
                depth=depth)
    for _ in range(rounds):
        if after["verdict"] != "fail":
            break
        if not refine(result, written, after, ffmpeg, say):
            break
        say("measuring the rebuilt delivery")
        after = run(outputs + untouched, target, ffmpeg, on_file=on_file,
                    depth=depth)

    return {"planned": planned, "written": written, "after": after,
            "outputs": outputs + untouched}


def refine(result, written, after, ffmpeg, say=None):
    """Rebuild, from their sources, the files that landed off target."""
    say = say or (lambda message: None)
    profile = result["profile"]
    after_by_path = {entry["path"]: entry for entry in after["files"]}
    facts = {entry["path"]: entry["facts"] for entry in result["files"]}
    rebuilt = 0
    for entry in written["written"]:
        measured = after_by_path.get(entry["output"])
        if not measured or measured["result"]["verdict"] != "fail":
            continue
        adjusted, why = corrections.refine(
            entry["steps"], measured["measurements"], measured["result"],
            profile)
        if not adjusted:
            continue
        say(f"rebuilding {entry['name']} from the source: {why}")
        _, command, steps = corrections.apply(
            entry["source"], adjusted, facts[entry["source"]],
            destination=entry["output"], ffmpeg=ffmpeg, overwrite=True)
        entry["command"], entry["steps"] = command, steps
        rebuilt += 1
    return rebuilt


def apply(planned, facts_by_path, ffmpeg=None, overwrite=False,
          directory=None, on_file=None):
    """Write a corrected copy of every file that needs one."""
    if ffmpeg is None:
        ffmpeg, _ = platform_support.require_tools()
    written, failed = [], []
    for index, entry in enumerate(planned["files"]):
        if on_file:
            on_file(index, len(planned["files"]), entry["name"])
        try:
            path, command, steps = corrections.apply(
                entry["path"], entry["steps"], facts_by_path[entry["path"]],
                ffmpeg=ffmpeg, overwrite=overwrite, directory=directory)
        except corrections.CorrectionError as error:
            failed.append({"name": entry["name"], "error": str(error)})
            continue
        written.append({"name": entry["name"], "source": entry["path"],
                        "output": path, "command": command, "steps": steps})
    return {"written": written, "failed": failed}


# ------------------------------------------------------------- the numbering

# `chapter-01, chapter-02, chapter-04` is a missing chapter, and it is visible
# from the filenames alone. Nothing about any file is wrong; the delivery is
# simply short one, and that is the kind of thing found at submission rather
# than at export.

_SEQUENCE = re.compile(r"^(.*?)(\d+)(\D*)$")
_MIN_SEQUENCE = 3


def numbering(names):
    """Missing numbers in what looks like a numbered sequence.

    Only what looks like one: three or more files sharing a prefix, a suffix
    and a digit width. A folder of unrelated names has no sequence to be
    missing from, and inventing one would produce a finding about nothing.
    """
    groups = {}
    for name in names:
        stem = os.path.splitext(name)[0]
        found = _SEQUENCE.match(stem)
        if not found:
            continue
        prefix, digits, suffix = found.groups()
        # The digit width is part of the key: `part2` and `part02` are two
        # naming schemes, and a delivery that mixes them has a different
        # problem from a missing file.
        key = (prefix, suffix, len(digits))
        groups.setdefault(key, []).append(int(digits))

    best_key, best = None, []
    for key, numbers in groups.items():
        if len(numbers) > len(best):
            best_key, best = key, numbers
    if len(best) < _MIN_SEQUENCE:
        return {"missing": [], "expected": len(names), "sequence": None}

    numbers = sorted(set(best))
    missing = [n for n in range(numbers[0], numbers[-1] + 1)
               if n not in set(numbers)]
    prefix, suffix, width = best_key
    extension = os.path.splitext(names[0])[1]
    return {
        "missing": [f"{prefix}{n:0{width}d}{suffix}{extension}"
                    for n in missing],
        "first": numbers[0],
        "last": numbers[-1],
        "present": len(numbers),
        "expected": numbers[-1] - numbers[0] + 1,
        "sequence": f"{prefix}…{suffix}",
    }
