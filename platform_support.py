"""Finding the tools, on whichever machine this happens to be.

Nothing here fails hard. A missing ffmpeg is reported as a missing ffmpeg with
the install line for this platform, not as a stack trace forty frames deep in a
parser that was handed an empty string.

Reporting it was as far as this went for a long time, which left the last step
to the person reading the sentence. The programs are now described in a table
instead of in prose, so that the same fact can serve both the line shown to
somebody and the argv `bootstrap.py` runs when they say yes. A sentence cannot
be executed and a command is unpleasant to read; deriving both from one entry
keeps them from drifting apart, and is what lets this tool offer to install
what it needs rather than telling somebody to go and get it.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass, field


IS_MACOS = sys.platform == "darwin"
IS_WINDOWS = os.name == "nt"
IS_LINUX = sys.platform.startswith("linux")

# Extensions the drop zone and the folder scanner accept. Deliberately broad:
# refusing a container we could have probed is a worse failure than probing one
# that turns out to be unreadable, because ffprobe's own error is specific.
AUDIO_EXTS = ("wav", "mp3", "m4a", "m4b", "aac", "flac", "aiff", "aif",
              "ogg", "opus", "wma")
VIDEO_EXTS = ("mp4", "mov", "mkv", "webm", "avi", "m4v", "mxf", "ts", "mts")
CAPTION_EXTS = ("srt", "vtt", "ass", "ssa", "sbv", "ttml")
MEDIA_EXTS = AUDIO_EXTS + VIDEO_EXTS


def _exe(name):
    return name + ".exe" if IS_WINDOWS else name


def no_console():
    """Keep console windows from flashing up on Windows."""
    if not IS_WINDOWS:
        return {}
    startup = subprocess.STARTUPINFO()
    startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    return {"startupinfo": startup,
            "creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)}


def find_ffmpeg():
    """An ffmpeg that actually carries the filters the report is built from.

    Slim distribution builds exist that lack ``ebur128``, and a build without
    it fails only once analysis is already running, with a filtergraph error
    that says nothing about why. Probe for the filter rather than trusting the
    first binary on PATH.
    """
    candidates = []
    env = os.environ.get("MEDIA_PREFLIGHT_FFMPEG")
    if env:
        candidates.append(env)
    found = shutil.which(_exe("ffmpeg"))
    if found:
        candidates.append(found)
    if IS_MACOS:
        candidates += ["/opt/homebrew/bin/ffmpeg", "/usr/local/bin/ffmpeg"]
    elif IS_LINUX:
        candidates += ["/usr/bin/ffmpeg", "/usr/local/bin/ffmpeg",
                       "/snap/bin/ffmpeg"]
    elif IS_WINDOWS:
        for base in (os.environ.get("ProgramFiles", ""),
                     os.environ.get("LOCALAPPDATA", "")):
            if base:
                candidates.append(os.path.join(base, "ffmpeg", "bin",
                                               "ffmpeg.exe"))

    seen = set()
    for candidate in candidates:
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)
        if has_filter(candidate, "ebur128"):
            return candidate
    return None


def has_filter(ffmpeg_path, name):
    """True when this ffmpeg build knows the named filter."""
    try:
        result = subprocess.run(
            [ffmpeg_path, "-hide_banner", "-h", "filter=" + name],
            capture_output=True, text=True, timeout=10, **no_console())
    except (OSError, subprocess.TimeoutExpired):
        return False
    text = result.stdout + result.stderr
    return result.returncode == 0 and "Unknown filter" not in text


def find_ffprobe(ffmpeg_path=None):
    """ffprobe from the same build as the chosen ffmpeg, else whatever's on PATH."""
    env = os.environ.get("MEDIA_PREFLIGHT_FFPROBE")
    if env and os.path.isfile(env):
        return env
    if ffmpeg_path:
        folder = os.path.dirname(ffmpeg_path)
        if folder:
            sibling = os.path.join(folder, _exe("ffprobe"))
            if os.path.isfile(sibling):
                return sibling
    return shutil.which(_exe("ffprobe"))


def install_hint():
    if IS_MACOS:
        return "brew install ffmpeg"
    if IS_WINDOWS:
        return "winget install Gyan.FFmpeg"
    return "sudo apt install ffmpeg  (or your distribution's package)"


class ToolsMissing(RuntimeError):
    """Raised with a sentence a person can act on, not a diagnostic code."""


def require_tools():
    """Return (ffmpeg, ffprobe) or raise with the install line for this platform."""
    ffmpeg = find_ffmpeg()
    ffprobe = find_ffprobe(ffmpeg)
    if not ffmpeg or not ffprobe:
        missing = "ffmpeg" if not ffmpeg else "ffprobe"
        raise ToolsMissing(
            f"{missing} was not found, or the build present lacks the "
            f"ebur128 filter this tool measures loudness with.\n"
            f"Install it with:  {install_hint()}")
    return ffmpeg, ffprobe


# ------------------------------------------------------- the external programs
#
# Above this line, each tool is found by a function written for it. That is
# still how ffmpeg and ffprobe are located — the ebur128 probe and the
# same-build rule are particular enough to deserve their own code — but a
# function cannot be asked what package it comes from, and that is the question
# the offer to install has to answer.
#
# So the same programs are described once more, as data: what they are called,
# what stops working without them, whether the tool can run at all if they are
# absent, and the name each package manager knows them by. `bootstrap.py` reads
# this table and nothing else.


@dataclass(frozen=True)
class Program:
    """An external program: how to find it, and how to go and get it.

    ``packages`` holds the name each package manager knows it by, rather than a
    ready-made sentence, so the same fact serves both the line shown to a
    person and the argv that is run when they say yes.

    ``locator`` is the escape hatch for a program that is not simply the first
    binary of that name on PATH. ffmpeg is exactly that: a build without the
    ebur128 filter is on PATH, is executable, and is of no use here, so
    ``find`` has to call the function that knows to reject it rather than
    trusting ``locate``.
    """

    key: str
    binaries: tuple            # tried in order; first hit wins
    purpose: str               # what stops working without it
    required: bool             # False for checks that degrade rather than fail
    packages: dict = field(default_factory=dict)   # manager -> package name
    provided_by: str = None    # another program's package installs this one
    version_args: tuple = ("--version",)
    locator: object = None     # callable returning a path, for the odd cases

    def package_for(self, manager):
        return self.packages.get(manager)

    def install_line(self):
        """The command a person could type, for the manager they have."""
        manager = current_manager()
        if manager is None:
            return ""
        name = self.package_for(manager)
        if not name:
            return ""
        return " ".join(MANAGERS[manager]["command"] + name.split())


def _locate_ffprobe():
    """ffprobe belonging to the ffmpeg this tool settled on, not just any."""
    return find_ffprobe(find("ffmpeg"))


PROGRAMS = {
    "ffmpeg": Program(
        key="ffmpeg",
        binaries=("ffmpeg",),
        purpose="measuring loudness, true peak and the rest of the report",
        required=True,
        packages={"brew": "ffmpeg", "apt": "ffmpeg", "dnf": "ffmpeg",
                  "pacman": "ffmpeg", "winget": "Gyan.FFmpeg"},
        version_args=("-version",),
        locator=find_ffmpeg,       # rejects a build without ebur128
    ),
    "ffprobe": Program(
        key="ffprobe",
        binaries=("ffprobe",),
        purpose="reading what is inside a file before anything measures it",
        required=True,
        packages={"brew": "ffmpeg", "apt": "ffmpeg", "dnf": "ffmpeg",
                  "pacman": "ffmpeg", "winget": "Gyan.FFmpeg"},
        provided_by="ffmpeg",      # one package, two programs
        version_args=("-version",),
        locator=_locate_ffprobe,
    ),
    "fc-list": Program(
        key="fc-list",
        binaries=("fc-list",),
        purpose="checking a caption file's fonts are on this machine",
        required=False,
        packages={"brew": "fontconfig", "apt": "fontconfig",
                  "dnf": "fontconfig", "pacman": "fontconfig"},
    ),
}


# How to install things, per package manager. `needs_root` decides whether this
# tool may run the command itself: it will never invoke sudo on somebody's
# behalf, so those are printed for them to run instead.
MANAGERS = {
    "brew":   {"probe": "brew",   "command": ["brew", "install"],
               "needs_root": False, "label": "Homebrew"},
    "winget": {"probe": "winget", "command": ["winget", "install", "-e", "--id"],
               "needs_root": False, "label": "winget"},
    "apt":    {"probe": "apt-get", "command": ["sudo", "apt-get", "install", "-y"],
               "needs_root": True, "label": "apt"},
    "dnf":    {"probe": "dnf",    "command": ["sudo", "dnf", "install", "-y"],
               "needs_root": True, "label": "dnf"},
    "pacman": {"probe": "pacman", "command": ["sudo", "pacman", "-S", "--noconfirm"],
               "needs_root": True, "label": "pacman"},
}

_manager = None


def current_manager():
    """The package manager on this machine, or None."""
    global _manager
    if _manager is None:
        for name in ("brew", "winget", "apt", "dnf", "pacman"):
            if shutil.which(MANAGERS[name]["probe"]):
                _manager = name
                break
        else:
            _manager = False
    return _manager or None


# Homebrew on Apple silicon is not on the PATH of a process launched from
# Finder, which is how most people will start this. Looking in the usual places
# is the difference between working and a false "it is missing" — the same
# reason `find_ffmpeg` carries its own list of candidates.
_EXTRA_PATHS = (
    "/opt/homebrew/bin",
    "/usr/local/bin",
    "/opt/local/bin",
    os.path.expanduser("~/.local/bin"),
    "/usr/bin",
)

_cache = {}


def locate(*binaries):
    """Absolute path to the first of these that exists, or None.

    Looks beyond PATH on purpose. A process launched from Finder does not
    inherit a shell's environment, so Homebrew's directory is simply absent and
    a perfectly installed program looks missing.
    """
    for binary in binaries:
        name = _exe(binary)
        found = shutil.which(name)
        if found:
            return found
        for directory in _EXTRA_PATHS:
            candidate = os.path.join(directory, name)
            if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
                return candidate
    return None


def find(key):
    """Absolute path to a known program, or None. Cached; `forget` clears it."""
    if key in _cache:
        return _cache[key]
    program = PROGRAMS[key]
    _cache[key] = (program.locator() if program.locator
                   else locate(*program.binaries))
    return _cache[key]


def forget():
    """Drop the cache. For after somebody installs something mid-session."""
    _cache.clear()


def version(key):
    """First line of the program's version output, or None."""
    path = find(key)
    if path is None:
        return None
    try:
        out = subprocess.run([path, *PROGRAMS[key].version_args],
                             capture_output=True, text=True, timeout=15,
                             **no_console())
    except (OSError, subprocess.SubprocessError):
        return None
    lines = (out.stdout or out.stderr or "").strip().splitlines()
    return lines[0].strip() if lines else None


def missing(required_only=False):
    """Programs that are not installed, as Program objects.

    Deduplicated by package: ffprobe and ffmpeg come from one formula, so
    offering to install both would ask somebody to approve the same download
    twice and then do it twice.
    """
    found, seen = [], set()
    for key, program in PROGRAMS.items():
        if find(key) is not None:
            continue
        if required_only and not program.required:
            continue
        if program.provided_by and program.provided_by in seen:
            continue
        seen.add(key)
        found.append(program)
    return found


# -------------------------------------------------------------- file dialogs
#
# A browser cannot tell a local program where a dropped file lives — it hands
# over the bytes and the basename and nothing else. For a tool whose whole
# promise is that your file never moves, uploading a two-gigabyte master to
# localhost to learn its path would be absurd. So the window asks the desktop
# for a real native dialog, and falls back to a typed path.


class PickerUnavailable(RuntimeError):
    """No dialog toolkit — the caller should offer a typed path instead."""


def pick_file(prompt="Choose a media file"):
    """A native open dialog. '' means the person cancelled."""
    if IS_MACOS:
        types = ", ".join(f'"{ext}"' for ext in MEDIA_EXTS)
        out = _osascript(f'POSIX path of (choose file with prompt "{prompt}" '
                         f'of type {{{types}}})')
        if out is not None:
            return out
    return _tk_dialog("file", prompt)


def pick_folder(prompt="Choose a folder"):
    if IS_MACOS:
        out = _osascript(f'POSIX path of (choose folder with prompt "{prompt}")')
        if out is not None:
            return out
    return _tk_dialog("directory", prompt)


def _osascript(script):
    """Returns None when osascript itself is unusable, and '' when the person
    cancelled — the two need different fallbacks."""
    try:
        result = subprocess.run(["osascript", "-e", script],
                                capture_output=True, text=True, timeout=300)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        # -128 is Cancel; anything else is a broken picker.
        return "" if "-128" in (result.stderr or "") else None
    return result.stdout.strip()


def _tk_dialog(mode, prompt):
    try:
        import tkinter
        from tkinter import filedialog
    except ImportError as exc:
        raise PickerUnavailable(
            "No file dialog is available. Install Python's tkinter support "
            "(for example 'sudo apt install python3-tk'), or type the file "
            "path into the field instead.") from exc
    try:
        root = tkinter.Tk()
    except tkinter.TclError as exc:
        raise PickerUnavailable(
            "No desktop session was found for a file dialog. Type the file "
            "path into the field instead.") from exc
    root.withdraw()
    root.attributes("-topmost", True)
    try:
        if mode == "directory":
            chosen = filedialog.askdirectory(title=prompt, parent=root)
        else:
            patterns = " ".join(f"*.{ext}" for ext in MEDIA_EXTS)
            chosen = filedialog.askopenfilename(
                title=prompt, parent=root,
                filetypes=[("Media files", patterns), ("All files", "*.*")])
    finally:
        root.destroy()
    return chosen or ""


# ------------------------------------------------------------------- reveal

def reveal(path):
    """Show a produced file in the platform's file manager. Best effort."""
    if not path or not os.path.exists(path):
        return False
    folder = path if os.path.isdir(path) else os.path.dirname(path)
    try:
        if IS_MACOS:
            subprocess.run(["open", "-R", path], timeout=20, **no_console())
        elif IS_WINDOWS:
            subprocess.run(["explorer", "/select,", os.path.normpath(path)],
                           timeout=20, **no_console())
        else:
            opener = (shutil.which("xdg-open") or shutil.which("gio")
                      or shutil.which("nautilus"))
            if not opener:
                return False
            args = ([opener, "open", folder] if opener.endswith("gio")
                    else [opener, folder])
            subprocess.run(args, timeout=20, **no_console())
    except (OSError, subprocess.TimeoutExpired):
        return False
    return True


def config_dir(name="media-preflight"):
    """Where a person's own settings live — custom profiles, remembered target.

    MEDIA_PREFLIGHT_CONFIG overrides it, for a portable install that keeps its
    settings beside itself and for anything that needs to point this
    somewhere harmless.
    """
    override = os.environ.get("MEDIA_PREFLIGHT_CONFIG")
    if override:
        return override
    if IS_WINDOWS:
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
    elif IS_MACOS:
        base = os.path.expanduser("~/Library/Application Support")
    else:
        base = (os.environ.get("XDG_CONFIG_HOME")
                or os.path.expanduser("~/.config"))
    return os.path.join(base, name)
