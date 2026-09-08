"""Finding the tools, on whichever machine this happens to be.

Nothing here fails hard. A missing ffmpeg is reported as a missing ffmpeg with
the install line for this platform, not as a stack trace forty frames deep in a
parser that was handed an empty string.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys


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
