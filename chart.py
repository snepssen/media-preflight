"""Drawing the measurement, rather than only listing it.

A list of timestamps answers "where", and a picture answers "what shape is
this". They are different questions and a delivery report wants both: the list
tells you to look at 18:07, the picture tells you that 18:07 is one of nine
identical spikes and the real problem is a compressor doing something odd.

Three renderings share one reduction:

* ``svg`` for the window and for the report you send a client
* ``strip`` for the terminal, where a picture has to fit on one line
* the reduced timeline itself, in the JSON report, for whatever reads that

On reducing
-----------
A ten-hour audiobook is 36,000 seconds and no chart has 36,000 pixels, so the
timeline is bucketed. Each bucket keeps the **loudest and the quietest** value
in it, never the average: averaging is exactly the operation that hides a spike
next to a hole, and spikes next to holes are what this tool is for. The chart
draws the band between them, so a reduced chart is still honest about the range
it is hiding.
"""

from __future__ import annotations

import math

import checks

# Wide enough to read, small enough that a browser draws it without thinking.
DEFAULT_POINTS = 900
BLOCKS = "▁▂▃▄▅▆▇█"


def reduce(timeline, points=DEFAULT_POINTS):
    """Bucket a per-second timeline down to at most ``points`` columns."""
    rows = [row for row in (timeline or []) if row.get("short_term") is not None
            or row.get("momentary") is not None]
    if not rows:
        return []
    if len(rows) <= points:
        return [{"t": float(row["t"]),
                 "low": _lowest(row), "high": _highest(row),
                 "momentary": row.get("momentary")}
                for row in rows]

    span = len(rows) / float(points)
    out = []
    for index in range(points):
        chunk = rows[int(index * span):max(int((index + 1) * span),
                                           int(index * span) + 1)]
        if not chunk:
            continue
        lows = [_lowest(row) for row in chunk]
        highs = [_highest(row) for row in chunk]
        moments = [row["momentary"] for row in chunk
                   if row.get("momentary") is not None]
        lows = [v for v in lows if v is not None]
        highs = [v for v in highs if v is not None]
        if not highs:
            continue
        out.append({"t": float(chunk[0]["t"]),
                    "low": min(lows) if lows else min(highs),
                    "high": max(highs),
                    "momentary": max(moments) if moments else None})
    return out


def _lowest(row):
    values = [row.get("short_term"), row.get("momentary")]
    values = [v for v in values if v is not None]
    return min(values) if values else None


def _highest(row):
    values = [row.get("short_term"), row.get("momentary")]
    values = [v for v in values if v is not None]
    return max(values) if values else None


# ------------------------------------------------------------------ the band

# Only a rule measured in LUFS can be drawn on a chart of LUFS. An ACX profile
# states its requirement in RMS, and shading a loudness chart with an RMS band
# would be the same invention this tool refuses everywhere else — so on those
# targets the chart carries no band, and says why.
LOUDNESS_METRICS = ("integrated_lufs", "short_term_excursions")


def band_for(profile):
    """The shaded target band, or None with the reason there isn't one."""
    for rule in profile.get("rules", []):
        if rule["metric"] in LOUDNESS_METRICS and (
                rule.get("min") is not None or rule.get("max") is not None):
            return {"min": rule.get("min"), "max": rule.get("max"),
                    "label": rule["label"], "unit": rule.get("unit", "LUFS")}
    stated = [rule["label"] for rule in profile.get("rules", [])
              if rule["metric"] in ("rms_dbfs", "peak_dbfs")]
    if stated:
        # Two forms: the full sentence for the report, and something short
        # enough to sit under a chart without running off it.
        return {"absent": f"{profile.get('label', 'This target')} states its "
                          f"requirement as {stated[0].lower()}, which is not "
                          f"the same measurement as the loudness drawn here.",
                "short": f"No target band: this one is stated in "
                         f"{stated[0].lower()}, not LUFS."}
    return {"absent": "This target sets no loudness band.",
            "short": "No target band: this target sets none."}


# -------------------------------------------------------------------- events

# Regions worth marking under the chart, in the order they are drawn.
EVENT_KINDS = (
    ("silences", "silence", "quiet"),
    ("black", "black", "picture"),
    ("frozen", "frozen", "picture"),
    ("flashes", "flashing", "picture"),
    ("clipping_windows", "clipping", "damage"),
)


def events(measurements, findings=None):
    """Every interval the chart can mark, from measurements and findings alike."""
    out = []
    for key, label, group in EVENT_KINDS:
        for item in measurements.get(key) or []:
            start, end = item.get("start"), item.get("end")
            if start is None or end is None:
                continue
            out.append({"start": float(start), "end": float(end),
                        "label": label, "group": group,
                        "detail": item.get("detail", "")})
    for finding in findings or []:
        if finding["status"] not in ("fail", "warn"):
            continue
        for interval in finding.get("intervals") or []:
            out.append({"start": float(interval["start"]),
                        "end": float(interval.get("end", interval["start"])),
                        "label": finding["label"], "group": finding["status"],
                        "detail": interval.get("detail", "")})
    return out


# ------------------------------------------------------------ caption track

# What a reader wants from a caption track on a chart is not every cue — it is
# where the captions are and where they are not. Cues separated by less than a
# breath are one run of captioning; drawn separately they are fifteen hundred
# rectangles that read as a solid bar.
CAPTION_MERGE_GAP_S = 0.6


def caption_coverage(cues, merge_gap=CAPTION_MERGE_GAP_S):
    """Cue intervals merged into runs of captioning."""
    ordered = sorted(({"start": float(c["start"]), "end": float(c["end"])}
                      for c in (cues or []) if c.get("end") is not None
                      and c["end"] > c["start"]),
                     key=lambda run: run["start"])
    runs = []
    for cue in ordered:
        if runs and cue["start"] - runs[-1]["end"] <= merge_gap:
            runs[-1]["end"] = max(runs[-1]["end"], cue["end"])
        else:
            runs.append(dict(cue))
    return runs


# ----------------------------------------------------------------- chapters

# Short-term loudness is measured over a three-second window, so a value
# reported at the first second of a chapter is mostly about the chapter before
# it. Attributing that to the new chapter makes a quiet chapter after a loud
# one look loud — which is exactly the comparison this table exists to make, so
# it has to be right.
SHORT_TERM_WINDOW_S = 3.0


def chapters(chapter_list, timeline, window_s=SHORT_TERM_WINDOW_S):
    """Per-chapter loudness, limited to what the timeline honestly holds.

    Integrated loudness is a gated measurement over a whole programme and
    cannot be re-derived per chapter from per-second values — computing it
    properly would mean one decode per chapter. What the timeline does hold is
    the loudest short-term and momentary value inside each range, which is
    enough to find the chapter that is six decibels louder than the rest, and
    that is what this reports. It is labelled as what it is.
    """
    out = []
    for index, chapter in enumerate(chapter_list or [], 1):
        start = chapter.get("start_s") or 0.0
        end = chapter.get("end_s")
        # Skip the opening window, whose values still carry the previous
        # chapter. A chapter too short to have a clean window keeps everything
        # rather than reporting nothing.
        settled = start + window_s
        inside = [row for row in (timeline or [])
                  if row["t"] >= settled and (end is None or row["t"] < end)]
        if not inside:
            inside = [row for row in (timeline or [])
                      if row["t"] >= start and (end is None or row["t"] < end)]
        shorts = [row["short_term"] for row in inside
                  if row.get("short_term") is not None]
        moments = [row["momentary"] for row in inside
                   if row.get("momentary") is not None]
        out.append({
            "number": index,
            "title": chapter.get("title") or f"Chapter {index}",
            "start_s": start,
            "end_s": end,
            "loudest_short_term": round(max(shorts), 1) if shorts else None,
            "loudest_momentary": round(max(moments), 1) if moments else None,
            "quietest_short_term": round(min(shorts), 1) if shorts else None,
        })
    return out


# --------------------------------------------------------------------- ASCII

def strip(points, band=None, event_list=None, width=58):
    """One line of loudness, for a terminal that has one line to spare."""
    if not points:
        return []
    columns = _columns([p["high"] for p in points], width)
    highs = [value for value in columns if value is not None]
    if not highs:
        return []
    low, high = min(highs), max(highs)
    if high - low < 1.0:                      # a flat programme still shows
        low, high = high - 3.0, high + 1.0

    bar = ""
    for value in columns:
        if value is None:
            bar += " "
            continue
        step = (value - low) / (high - low)
        bar += BLOCKS[min(len(BLOCKS) - 1, max(0, int(step * len(BLOCKS))))]

    lines = [f"  {bar}"]
    marks = _marks(points, columns, event_list, width)
    if marks.strip():
        lines.append(f"  {marks}")
    duration = points[-1]["t"] + 1
    left = checks.timecode(0)
    right = checks.timecode(duration)
    lines.append("  " + left + " " * max(1, width - len(left) - len(right))
                 + right)
    if band and band.get("min") is not None:
        lines.insert(0, f"  loudness {high:.0f} to {low:.0f} LUFS, "
                        f"target {band['min']:g} to {band['max']:g}")
    return lines


def _columns(values, width):
    """Fit a series into `width` columns, keeping the loudest of each.

    A file shorter than the strip is stretched across it rather than drawn as
    a stub with forty blank columns after it: the strip is a shape, and a shape
    that occupies a fifth of the space reads as missing data.
    """
    if not values:
        return []
    span = len(values) / float(width)
    out = []
    for index in range(width):
        low = int(index * span)
        high = max(int((index + 1) * span), low + 1)
        chunk = [v for v in values[low:high] if v is not None]
        out.append(max(chunk) if chunk else None)
    return out


def _marks(points, columns, event_list, width):
    """A row under the bar showing which columns hold a problem."""
    if not event_list:
        return ""
    duration = max(1.0, points[-1]["t"] + 1 - points[0]["t"])
    start_t = points[0]["t"]
    row = [" "] * width
    for event in event_list:
        if event["group"] not in ("fail", "warn"):
            continue
        first = int((event["start"] - start_t) / duration * width)
        last = int((event["end"] - start_t) / duration * width)
        for column in range(max(0, first), min(width, max(last, first + 1))):
            if event["group"] == "fail" or row[column] == " ":
                row[column] = "✕" if event["group"] == "fail" else "⚠"
    return "".join(row)


# ----------------------------------------------------------------------- SVG

WIDTH, HEIGHT = 720, 220
PAD_LEFT, PAD_RIGHT, PAD_TOP, PAD_BOTTOM = 44, 12, 14, 52

# Two grounds, and the choice matters more than it looks. An SVG referenced by
# an <img> resolves prefers-color-scheme against the reader's *operating
# system*, not against the document it sits in — so a themed chart in a
# light-themed report renders dark for anybody whose laptop is in dark mode.
# Exports are therefore drawn light, like every other figure in a document;
# only the window, which inlines the SVG into a page it controls, follows the
# theme.
LIGHT_STYLE = """
  .bg { fill: #ffffff; }
  .grid { stroke: #dcdfe4; stroke-width: 1; }
  .axis { fill: #5d646e; font: 10px ui-monospace, Menlo, Consolas, monospace; }
  .band { fill: #1c6b3f; fill-opacity: .12; }
  .band-line { stroke: #1c6b3f; stroke-width: 1; stroke-dasharray: 4 3;
               stroke-opacity: .55; }
  .range { fill: #1f5fbf; fill-opacity: .30; }
  .peak { stroke: #1f5fbf; stroke-width: 1.2; fill: none; }
  .ev-fail { fill: #b3261e; fill-opacity: .55; }
  .ev-warn { fill: #8a5a00; fill-opacity: .5; }
  .ev-quiet { fill: #5d646e; fill-opacity: .35; }
  .ev-picture { fill: #5d646e; fill-opacity: .45; }
  .ev-damage { fill: #b3261e; fill-opacity: .55; }
  .chapter { stroke: #8a8f98; stroke-width: 1; stroke-dasharray: 2 3;
             stroke-opacity: .6; }
  .note { fill: #5d646e; font: 10px ui-monospace, Menlo, Consolas, monospace; }
  .caption { fill: #1f5fbf; fill-opacity: .45; }
  .before { stroke: #8a8f98; stroke-width: 1; fill: none; stroke-opacity: .75;
            stroke-dasharray: 3 2; }
  .key { fill: #5d646e; font: 10px ui-monospace, Menlo, Consolas, monospace; }
  .caption-bed { fill: #5d646e; fill-opacity: .13; }
"""

DARK_RULES = """
    .bg { fill: #1e2127; }
    .grid { stroke: #313640; }
    .axis, .note { fill: #99a1ad; }
    .band { fill: #6fd39a; fill-opacity: .14; }
    .band-line { stroke: #6fd39a; }
    .range { fill: #7aa7ff; fill-opacity: .30; }
    .peak { stroke: #7aa7ff; }
    .ev-fail, .ev-damage { fill: #ff8a80; }
    .ev-warn { fill: #f0b849; }
    .ev-quiet, .ev-picture { fill: #99a1ad; }
    .chapter { stroke: #99a1ad; }
    .caption { fill: #7aa7ff; fill-opacity: .55; }
    .before { stroke: #99a1ad; }
    .key { fill: #99a1ad; }
    .caption-bed { fill: #99a1ad; fill-opacity: .16; }
"""

def style_for(theme):
    """'light' for a file somebody will read in a document; 'auto' for a page
    that already knows which way round it is."""
    if theme == "auto":
        return (LIGHT_STYLE + "@media (prefers-color-scheme: dark) {"
                + DARK_RULES + "}")
    return LIGHT_STYLE


def svg(points, band=None, event_list=None, chapter_list=None, duration=None,
        title="Loudness over time", theme="light", caption_runs=None,
        baseline=None, baseline_duration=None):
    """A standalone SVG. Returns '' when there is nothing to draw.

    ``baseline`` is a second, earlier reading of the same programme — the file
    as it was before a correction — drawn faintly behind. Both series are
    plotted against whichever is longer, so a trimmed ending shows as the
    baseline continuing past where the corrected file stops. Scaling them to a
    common width would hide exactly the change somebody wants to see.
    """
    if not points:
        return ""
    duration = duration or (points[-1]["t"] + 1)
    lows = [p["low"] for p in points if p["low"] is not None]
    highs = [p["high"] for p in points if p["high"] is not None]
    if not highs:
        return ""
    if baseline:
        duration = max(duration, baseline_duration or (baseline[-1]["t"] + 1))
        lows += [p["low"] for p in baseline if p["low"] is not None]
        highs += [p["high"] for p in baseline if p["high"] is not None]

    top = max(highs)
    bottom = min(lows) if lows else top - 20
    if band and band.get("min") is not None:
        top = max(top, band["max"] if band.get("max") is not None else top)
        bottom = min(bottom, band["min"])
    # Never let a flat programme become a flat line hugging one edge.
    if top - bottom < 6:
        middle = (top + bottom) / 2
        top, bottom = middle + 3, middle - 3
    top += (top - bottom) * 0.12
    bottom -= (top - bottom) * 0.12

    plot_w = WIDTH - PAD_LEFT - PAD_RIGHT
    plot_h = HEIGHT - PAD_TOP - PAD_BOTTOM

    def x(seconds):
        return PAD_LEFT + min(1.0, max(0.0, seconds / duration)) * plot_w

    def y(value):
        return PAD_TOP + (top - value) / (top - bottom) * plot_h

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {WIDTH} {HEIGHT}"'
        f' width="100%" role="img" aria-label="{_escape(title)}">',
        f"<style>{style_for(theme)}</style>",
        f'<rect class="bg" x="0" y="0" width="{WIDTH}" height="{HEIGHT}"/>',
    ]

    for value in _ticks(bottom, top):
        parts.append(f'<line class="grid" x1="{PAD_LEFT}" y1="{y(value):.1f}" '
                     f'x2="{WIDTH - PAD_RIGHT}" y2="{y(value):.1f}"/>')
        parts.append(f'<text class="axis" x="{PAD_LEFT - 6}" '
                     f'y="{y(value) + 3:.1f}" text-anchor="end">{value:g}</text>')

    if band and band.get("min") is not None:
        high_edge = band["max"] if band.get("max") is not None else top
        parts.append(f'<rect class="band" x="{PAD_LEFT}" y="{y(high_edge):.1f}" '
                     f'width="{plot_w}" '
                     f'height="{abs(y(band["min"]) - y(high_edge)):.1f}"/>')
        for edge in (band["min"], band.get("max")):
            if edge is not None:
                parts.append(f'<line class="band-line" x1="{PAD_LEFT}" '
                             f'y1="{y(edge):.1f}" x2="{WIDTH - PAD_RIGHT}" '
                             f'y2="{y(edge):.1f}"/>')

    for chapter in chapter_list or []:
        if chapter["start_s"]:
            parts.append(f'<line class="chapter" x1="{x(chapter["start_s"]):.1f}" '
                         f'y1="{PAD_TOP}" x2="{x(chapter["start_s"]):.1f}" '
                         f'y2="{PAD_TOP + plot_h}"/>')

    # The reduced range is drawn as an area between quietest and loudest, so a
    # chart that hides 36,000 rows still shows how much it is hiding.
    upper = " ".join(f"{x(p['t']):.1f},{y(p['high']):.1f}"
                     for p in points if p["high"] is not None)
    lower = " ".join(f"{x(p['t']):.1f},{y(p['low'] if p['low'] is not None else p['high']):.1f}"
                     for p in reversed(points) if p["high"] is not None)
    if baseline:
        was = " ".join(f"{x(p['t']):.1f},{y(p['high']):.1f}"
                       for p in baseline if p["high"] is not None)
        parts.append(f'<polyline class="before" points="{was}"/>')
        parts.append(f'<text class="key" x="{WIDTH - PAD_RIGHT}" '
                     f'y="{PAD_TOP + 2}" text-anchor="end">'
                     f'dashed: before the correction</text>')

    parts.append(f'<polygon class="range" points="{upper} {lower}"/>')
    parts.append(f'<polyline class="peak" points="{upper}"/>')

    for index, event in enumerate(event_list or []):
        left = x(event["start"])
        right = max(x(event["end"]), left + 1.5)
        group = event["group"] if event["group"] in (
            "fail", "warn", "quiet", "picture", "damage") else "warn"
        parts.append(
            f'<rect class="ev-{group}" x="{left:.1f}" '
            f'y="{PAD_TOP + plot_h + 6}" width="{right - left:.1f}" height="7" '
            f'data-event="{index}" data-label="{_escape(event["label"])}">'
            f'<title>{_escape(event["label"])}'
            f'{" — " + _escape(event["detail"]) if event.get("detail") else ""}'
            f' at {checks.timecode(event["start"])}</title></rect>')

    # The caption track: a bed the width of the programme with the captioned
    # runs drawn on it, so the gaps are the thing you see.
    if caption_runs:
        top_y = PAD_TOP + plot_h + 16
        parts.append(f'<rect class="caption-bed" x="{PAD_LEFT}" '
                     f'y="{top_y}" width="{plot_w}" height="5"/>')
        for run in caption_runs:
            left = x(run["start"])
            right = max(x(run["end"]), left + 0.8)
            parts.append(f'<rect class="caption" x="{left:.1f}" y="{top_y}" '
                         f'width="{right - left:.1f}" height="5"><title>'
                         f'captioned {checks.timecode(run["start"])}'
                         f'–{checks.timecode(run["end"])}</title></rect>')
        parts.append(f'<text class="axis" x="{PAD_LEFT - 6}" '
                     f'y="{top_y + 5}" text-anchor="end">cc</text>')

    ticks = _time_ticks(duration)
    for index, seconds in enumerate(ticks):
        anchor = ("start" if index == 0 else
                  "end" if index == len(ticks) - 1 and
                  x(seconds) > WIDTH - PAD_RIGHT - 24 else "middle")
        parts.append(f'<text class="axis" x="{x(seconds):.1f}" '
                     f'y="{HEIGHT - 8}" text-anchor="{anchor}">'
                     f'{checks.timecode(seconds)}</text>')
    end = checks.timecode(duration)
    if not ticks or duration - ticks[-1] > duration * 0.06:
        parts.append(f'<text class="axis" x="{WIDTH - PAD_RIGHT}" '
                     f'y="{HEIGHT - 8}" text-anchor="end">{end}</text>')

    if band and band.get("absent"):
        # The full reason is in the report; the drawing gets as much of it as
        # fits between the axes, because a note that runs off the edge or sits
        # on top of the time axis is worse than a short one.
        # The text starts at PAD_LEFT, so the room it has is the plot width.
        room = int(plot_w / 6.3)
        said = band.get("short") or ("No target band drawn: " + band["absent"])
        if len(said) > room:
            said = said[:room - 1].rstrip(" ,.;") + "…"
        parts.append(f'<text class="note" x="{PAD_LEFT}" '
                     f'y="{PAD_TOP + plot_h + 30}">{_escape(said)}</text>')

    parts.append("</svg>")
    return "".join(parts)


def _ticks(bottom, top, count=4):
    """Round LUFS values inside the range, for a readable left-hand axis."""
    span = top - bottom
    if span <= 0:
        return []
    raw = span / count
    step = min((s for s in (1, 2, 5, 10, 20, 50) if s >= raw), default=50)
    first = math.ceil(bottom / step) * step
    return [first + step * index
            for index in range(int(span / step) + 1)
            if first + step * index <= top]


def _time_ticks(duration, count=5):
    if duration <= 0:
        return [0]
    step = duration / count
    nice = min((s for s in (1, 5, 10, 15, 30, 60, 120, 300, 600, 900, 1800,
                            3600) if s >= step), default=3600)
    ticks = [index * nice for index in range(int(duration / nice) + 1)]
    return ticks or [0]


def _escape(text):
    return (str(text).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


# ------------------------------------------------------- the set, as a picture

SET_HEIGHT_PER_FILE = 15
SET_MIN_HEIGHT = 90


def set_svg(spread, band=None, theme="light", limit=60,
            title="Loudness across the delivery"):
    """One bar per file, against the target band.

    This is the picture that makes a thirty-chapter title legible: the chapter
    recorded three decibels hotter than the rest is a bar that sticks out, and
    no amount of reading down a column of numbers does that as fast.
    """
    values = (spread or {}).get("per_file") or []
    if len(values) < 2:
        return ""
    clipped = values[:limit]

    rows = len(clipped)
    height = max(SET_MIN_HEIGHT,
                 PAD_TOP + PAD_BOTTOM + rows * SET_HEIGHT_PER_FILE)
    label_width = 132
    plot_w = WIDTH - label_width - PAD_RIGHT - 30

    numbers = [entry["value"] for entry in clipped]
    low, high = min(numbers), max(numbers)
    if band and band.get("min") is not None:
        low = min(low, band["min"])
        high = max(high, band["max"] if band.get("max") is not None else high)
    if high - low < 4:
        middle = (high + low) / 2
        low, high = middle - 2, middle + 2
    pad = (high - low) * 0.1
    low, high = low - pad, high + pad

    def x(value):
        return label_width + (value - low) / (high - low) * plot_w

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {WIDTH} {height}"'
        f' width="100%" role="img" aria-label="{_escape(title)}">',
        f"<style>{style_for(theme)}</style>",
        f'<rect class="bg" x="0" y="0" width="{WIDTH}" height="{height}"/>',
    ]

    if band and band.get("min") is not None:
        left = x(band["min"])
        right = x(band["max"]) if band.get("max") is not None else x(high)
        parts.append(f'<rect class="band" x="{left:.1f}" y="{PAD_TOP - 4}" '
                     f'width="{max(1.0, right - left):.1f}" '
                     f'height="{rows * SET_HEIGHT_PER_FILE + 8}"/>')

    for index, entry in enumerate(clipped):
        centre = PAD_TOP + index * SET_HEIGHT_PER_FILE + SET_HEIGHT_PER_FILE / 2
        inside = _inside(entry["value"], band)
        parts.append(
            f'<text class="axis" x="{label_width - 8}" y="{centre + 3:.1f}" '
            f'text-anchor="end">{_escape(_shorten(entry["name"]))}</text>')
        parts.append(
            f'<line class="grid" x1="{label_width}" y1="{centre:.1f}" '
            f'x2="{label_width + plot_w}" y2="{centre:.1f}"/>')
        parts.append(
            f'<circle class="{"peak" if inside else "ev-warn"}" '
            f'cx="{x(entry["value"]):.1f}" cy="{centre:.1f}" r="4" '
            f'fill="{"none" if inside else "currentColor"}">'
            f'<title>{_escape(entry["name"])} — {entry["value"]:g} '
            f'{_escape((spread or {}).get("unit", "dB"))}</title></circle>')

    for value in _ticks(low, high):
        parts.append(f'<text class="axis" x="{x(value):.1f}" '
                     f'y="{height - 10}" text-anchor="middle">{value:g}</text>')
    if len(values) > limit:
        parts.append(f'<text class="note" x="{label_width}" y="{height - 24}">'
                     f'{len(values) - limit} more files not drawn</text>')
    parts.append("</svg>")
    return "".join(parts)


def _inside(value, band):
    if not band or band.get("min") is None:
        return True
    if value < band["min"]:
        return False
    return band.get("max") is None or value <= band["max"]


def _shorten(name, width=22):
    if len(name) <= width:
        return name
    return name[:width - 9] + "…" + name[-8:]
