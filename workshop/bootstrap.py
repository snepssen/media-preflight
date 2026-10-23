"""Getting the things a tool needs, rather than complaining that they are gone.

Being told "install ffmpeg" and being asked "shall I install ffmpeg?" are
different experiences, and only the second is a tool doing its job. So
everything missing is listed, the exact commands are shown, and the default
answer is yes to all of it: pressing return installs everything.

Two things it will not do. It never invokes `sudo` on anybody's behalf: on a
package manager that needs root the commands are printed for the person to
run, and whatever installs without root (a venv, say) still goes ahead. And
it never installs anything without being asked, except when told `--yes`,
which is there for scripts and for the launcher.

A tool makes one `Bootstrap` from its `Registry` and its own words, and its
start script runs `main()`: `--check` is silent and answers by exit code.
"""

import subprocess
import sys

from . import programs as _programs


def run(argv, on_line=None):
    """Run one install command. None when it succeeded, else the last line it said."""
    if on_line:
        on_line(f"$ {' '.join(argv)}")
    try:
        done = subprocess.run(argv, capture_output=True, text=True, timeout=3600)
    except (OSError, subprocess.SubprocessError) as error:
        return str(error)
    if done.returncode == 0:
        return None
    detail = (done.stderr or done.stdout or "").strip().splitlines()
    return detail[-1] if detail else f"exit {done.returncode}"


class Bootstrap:
    def __init__(self, registry, *, banner, again, note=None, manager=None, runner=None):
        """
        registry: the tool's programs.Registry.
        banner: the first line shown, such as "siphon needs a few programs it does not ship."
        again: what to say after a "no", such as "`siphon setup` will ask again."
        note: optional; returns a paragraph to show before the offer, or None.
        manager: optional; returns the package manager to use. A tool whose
            tests replace its own `current_manager` passes a lookup of that
            name, so the replacement is the one asked.
        runner: optional; runs one argv as `run` does. The same reason.
        """
        self.registry = registry
        self.banner = banner
        self.again = again
        self.note = note
        self.manager = manager or (lambda: _programs.current_manager())
        self.runner = runner or run

    @property
    def tool(self):
        return self.registry.tool

    @property
    def width(self):
        return max(len(key) for key in self.registry.programs) + 2

    def survey(self):
        """Everything missing, split into what the tool cannot work without."""
        absent = self.registry.missing()
        return {"manager": self.manager(),
                "required": [p for p in absent if p.required],
                "optional": [p for p in absent if not p.required]}

    @staticmethod
    def command_for(program, manager):
        """The argv that would install one program, or None if it cannot."""
        return _programs.command_for(program, manager)

    def commands(self, program, manager):
        """Every argv that installs one program, in order; [] if none can."""
        if program.steps:
            return list(program.steps())
        argv = self.command_for(program, manager)
        return [argv] if argv else []

    def describe(self, program, manager):
        argvs = self.commands(program, manager)
        if not argvs:
            return f"(no {manager} package known)"
        return "\n  ".join(" ".join(argv) for argv in argvs)

    def install(self, chosen, manager, on_line=None):
        """Run the installs. Returns [(program, ok, detail)] in the order tried."""
        results = []
        for program in chosen:
            argvs = self.commands(program, manager)
            if not argvs:
                results.append((program, False, "no package known for this system"))
                continue
            problem = None
            for argv in argvs:
                problem = self.runner(argv, on_line)
                if problem:
                    break
            # Ask the disk, not the exit code: Homebrew returns non-zero for
            # things that are not failures, such as already installed.
            self.registry.forget()
            if self.registry.find(program.key) is not None:
                results.append((program, True, ""))
            else:
                results.append((program, False, problem or "installed, but not found afterwards"))
        return results

    def offer(self, assume_yes=False, quiet=False):
        """Show what is missing and install it. Returns True if nothing required is left."""
        state = self.survey()
        manager = state["manager"]
        outstanding = state["required"] + state["optional"]
        width = self.width

        if not outstanding:
            if not quiet:
                print(f"Everything {self.tool} needs is already installed.")
            return True

        if not quiet:
            print(self.banner)
            print()
            for program in state["required"]:
                print(f"  {program.key:<{width}} needed  — {program.purpose}")
            for program in state["optional"]:
                print(f"  {program.key:<{width}} extra   — {program.purpose}")
            print()
            paragraph = self.note() if self.note else None
            if paragraph:
                print(paragraph)
                print()

        packaged = [p for p in outstanding if not p.steps]
        chosen = outstanding
        if packaged and (manager is None or _programs.MANAGERS[manager]["needs_root"]):
            # Name the packages when something else is still being installed.
            these = "these" if len(packaged) == len(outstanding) else ", ".join(p.key for p in packaged)
            if manager is None:
                them = "them" if these == "these" else these
                print(f"{self.tool} could not find a package manager to install {them} with.", file=sys.stderr)
                print("On macOS, Homebrew is the usual one: https://brew.sh", file=sys.stderr)
            elif not quiet:
                # Never sudo on somebody's behalf. Show the line; let them run it.
                print(f"Installing {these} needs root, so {self.tool} will not run it for you. Copy this:\n")
                names = [p.package_for(manager) for p in packaged if p.package_for(manager)]
                print("  " + " ".join(_programs.MANAGERS[manager]["command"] + names))
                print()
            chosen = [p for p in outstanding if p.steps]
        if not chosen:
            return not state["required"]

        if not assume_yes:
            for program in chosen:
                print(f"  {self.describe(program, manager)}")
            print()
            what = "it" if len(chosen) == 1 else f"all {len(chosen)} of these"
            labels = sorted({(p.via or "its own installer") if p.steps
                             else _programs.MANAGERS[manager]["label"] for p in chosen})
            try:
                answer = input(f"Install {what} with {' and '.join(labels)}? [Y/n] ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                print()
                return not state["required"]
            if answer in {"n", "no"}:
                print(f"\nNothing installed. {self.again}")
                return not state["required"]

        print()
        results = self.install(chosen, manager, on_line=print)
        print()
        for program, ok, detail in results:
            if ok:
                version = self.registry.version(program.key) or "installed"
                print(f"  ✓ {program.key:<{width}} {version}")
            else:
                print(f"  ✗ {program.key:<{width}} {detail}", file=sys.stderr)
                print(f"    try by hand: {self.describe(program, manager)}", file=sys.stderr)
        self.registry.forget()
        return not self.registry.missing(required_only=True)

    def ready(self):
        """True when nothing the tool cannot work without is missing."""
        return not self.registry.missing(required_only=True)

    def main(self, argv=None):
        argv = list(sys.argv[1:] if argv is None else argv)
        if "--check" in argv:
            return 0 if self.ready() else 1
        return 0 if self.offer(assume_yes="--yes" in argv or "-y" in argv) else 1
