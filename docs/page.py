"""Media Preflight's page, as content.

The chrome — head, rail, header, jump navigation, ecosystem grid, footer —
is build.py's. What is here is what is particular to this project.

Sections carry a `body`: an HTML partial under `sections/`. This page is meters,
waveform strips and annotated report windows, markup whose shape is the content;
retyping it as Python dicts would lose syntax highlighting and gain nothing.
The generator supports structured blocks too, and siphon's page uses them,
because its content is prose and figures that genuinely are regular.
"""

PAGE = {
    "meta": {
        "slug": "media-preflight",
        "name": "Media Preflight",
        "title": "Media Preflight",
        "badge": "macOS · Windows · Linux · MIT",
        "fonts": "https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@500;600;700&family=Inter:wght@400;500;600&family=JetBrains+Mono:wght@400;500&display=swap",
        "description": "Local-first delivery validation for finished audio and video. Will this be accepted, and what exactly is wrong with it — with the timestamps, and a corrected copy that is measured again before it claims to be fixed.",
        "og_description": "Will this be accepted — and can you safely fix what is wrong? An inspector with carefully bounded, previewable transformations.",
        "subhead": "Drop in a finished file — sound, picture, captions — and find out where it stands. Not against one target you had to name first, but against every one that applies, with the exact timestamp of everything that is wrong. Then, if you want, a corrected copy that is measured again from scratch before it claims to be fixed.",
        "stats": [
            "<b>9</b> delivery targets, and your own",
            "<b>1</b> decode per stream, every measurement",
            "<b>442</b> checks passing",
            "<b>0</b> uploads, accounts, credits",
            "<b>Local-first.</b> Nothing leaves the machine",
        ],
    },
    "sections": [
    {
        "id": "report",
        "jump": "The report",
        "eyebrow": "What comes back",
        "heading": "A verdict, and where to look",
        "body": "report.html",
    },
    {
        "id": "workflow",
        "jump": "Workflow",
        "eyebrow": "One engine, two ways in",
        "heading": "Guided when you want the answer. Professional when you know the handoff.",
        "body": "workflow.html",
    },
    {
        "id": "delivery",
        "jump": "Deliveries",
        "eyebrow": "A folder, not a file",
        "heading": "Some faults belong to the set",
        "body": "delivery.html",
    },
    {
        "id": "targets",
        "jump": "Targets",
        "eyebrow": "Where it is going",
        "heading": "Nine targets, and the one you write",
        "body": "targets.html",
    },
    {
        "id": "fix",
        "jump": "Corrections",
        "eyebrow": "The corrected copy",
        "heading": "Four promises, kept in code",
        "body": "fix.html",
    },
    {
        "id": "how",
        "jump": "How",
        "eyebrow": "Under it",
        "heading": "One decode per stream",
        "body": "how.html",
    },
    {
        "id": "install",
        "jump": "Install",
        "eyebrow": "Running it",
        "heading": "Two commands, no packages",
        "body": "install.html",
    },
    {"grid": True},
    {
        "id": "contact",
        "eyebrow": "If a number looks wrong",
        "heading": "Say so",
        "body": "contact.html",
    },
    ],
    "footer": [
        "Media Preflight is MIT licensed. Your media stays on your machine and outside the repository; the test fixtures are generated from ffmpeg's own sources rather than committed.",
    ],
}
