"""Finding the external programs a tool does not ship, and saying how to get them.

A tool declares its programs once, as a table of `Program`s, and wraps them in a
`Registry`: what is here, where, which version, what is missing, and the one
sentence to show when something is. The same table gives both the line shown
to a person and the argv a bootstrap runs when they say yes, so the two cannot
drift apart.
"""

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass, field

IS_MACOS = sys.platform == "darwin"
IS_WINDOWS = os.name == "nt"
IS_LINUX = sys.platform.startswith("linux")


@dataclass(frozen=True)
class Program:
    """An external program: how to find it, and how to go and get it.

    `packages` holds the name each package manager knows it by. `locator`,
    when set, finds the program instead of `binaries`: for a tool that needs a
    capability rather than a name, such as an ffmpeg built with ebur128.
    `provided_by` names another program whose package installs this one, so
    one download is not offered twice. `steps`, when set, is installed by its
    own commands rather than a package manager: a callable returning a list of
    argvs, such as making a venv and running pip in it, with `via` naming
    what runs them ("pip"). `version_args` of None means not to ask.
    """

    key: str
    binaries: tuple
    purpose: str
    required: bool
    packages: dict = field(default_factory=dict)
    provided_by: str = None
    version_args: tuple = ("--version",)
    locator: object = None
    steps: object = None
    via: str = None

    def package_for(self, manager):
        return self.packages.get(manager)

    def install_line(self):
        """The command a person could type, for the manager they have."""
        return install_line(self)


# How to install, per package manager. `needs_root` decides whether a tool may
# run the command itself: it never invokes sudo on anybody's behalf, so those
# are printed for the person to run instead.
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
        _manager = next((name for name in ("brew", "winget", "apt", "dnf", "pacman")
                         if shutil.which(MANAGERS[name]["probe"])), False)
    return _manager or None


def command_for(program, manager):
    """The argv that would install `program` with `manager`, or None."""
    name = program.package_for(manager) if manager else None
    return MANAGERS[manager]["command"] + name.split() if name else None


def install_line(program, manager=None):
    """The command a person could type, for the manager they have, or ''."""
    if program.steps:
        return " && ".join(" ".join(argv) for argv in program.steps())
    argv = command_for(program, manager or current_manager())
    return " ".join(argv) if argv else ""


def exe(name):
    return name + ".exe" if IS_WINDOWS else name


def no_console():
    """subprocess options that keep a console window from flashing up on Windows."""
    if not IS_WINDOWS:
        return {}
    startup = subprocess.STARTUPINFO()
    startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    return {"startupinfo": startup, "creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)}


# Homebrew on Apple silicon is not on the PATH of a process launched from
# Finder, which is how most people start these tools. Looking in the usual
# places is the difference between working and a false "it is missing".
EXTRA_PATHS = (
    "/opt/homebrew/bin",
    "/usr/local/bin",
    "/opt/local/bin",
    os.path.expanduser("~/.local/bin"),
    "/usr/bin",
)


def locate(*binaries):
    """Absolute path to the first of these that exists, or None. Looks beyond PATH."""
    for binary in binaries:
        name = exe(binary)
        found = shutil.which(name)
        if found:
            return found
        for directory in EXTRA_PATHS:
            candidate = os.path.join(directory, name)
            if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
                return candidate
    return None


class MissingProgram(Exception):
    """A program a tool needs is not installed. The message is the remedy."""

    def __init__(self, key, tool="this tool", program=None):
        self.key = key
        message = f"{tool} needs {key}"
        if program is not None:
            message += f" for {program.purpose}"
        message += ", and could not find it."
        line = install_line(program) if program is not None else ""
        if line:
            message += f" Install it with: {line}"
        super().__init__(message)


class Registry:
    """One tool's programs: found once each, until `forget`."""

    def __init__(self, programs, tool):
        self.programs = programs
        self.tool = tool
        self._cache = {}

    def find(self, key):
        """Absolute path to a known program, or None. Cached; `forget` clears it."""
        if key not in self._cache:
            program = self.programs[key]
            self._cache[key] = program.locator() if program.locator else locate(*program.binaries)
        return self._cache[key]

    def forget(self):
        """Drop the cache, for after something is installed mid-session."""
        self._cache.clear()

    def require(self, key):
        """Path to a program, or MissingProgram carrying the sentence to show."""
        path = self.find(key)
        if path is None:
            raise MissingProgram(key, self.tool, self.programs[key])
        return path

    def version(self, key):
        """First line of the program's version output, or None."""
        path = self.find(key)
        if path is None or self.programs[key].version_args is None:
            return None
        try:
            out = subprocess.run([path, *self.programs[key].version_args],
                                 capture_output=True, text=True, timeout=15, **no_console())
        except (OSError, subprocess.SubprocessError):
            return None
        lines = (out.stdout or out.stderr or "").strip().splitlines()
        return lines[0].strip() if lines else None

    def survey(self):
        """Every program, whether it is here, and what to do if it is not."""
        report = {}
        for key, program in self.programs.items():
            path = self.find(key)
            report[key] = {
                "present": path is not None,
                "path": path,
                "version": self.version(key) if path else None,
                "required": program.required,
                "purpose": program.purpose,
                "install": install_line(program),
            }
        return report

    def missing(self, required_only=False):
        """Programs that are not installed, deduplicated by package.

        ffprobe and ffmpeg come from one formula: offering both would ask for
        the same download twice.
        """
        found, seen = [], set()
        for key, program in self.programs.items():
            if self.find(key) is not None:
                continue
            if required_only and not program.required:
                continue
            if program.provided_by and program.provided_by in seen:
                continue
            seen.add(key)
            found.append(program)
        return found

    def missing_required(self):
        """The required programs that are not here, as sentences."""
        return [str(MissingProgram(key, self.tool, program))
                for key, program in self.programs.items()
                if program.required and self.find(key) is None]
