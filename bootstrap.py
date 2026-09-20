#!/usr/bin/env python3
"""Getting the things Media Preflight needs, rather than complaining they are gone.

This tool ships neither Python nor ffmpeg, and that is the right call: bundling
ffmpeg means eighty megabytes and somebody else's licensing decision, and the
ffmpeg already on this machine is almost certainly built with more codecs than
a portable one would carry. But shipping nothing leaves a gap, and for a long
time this tool handed that gap to the person in front of it — a sentence saying
`brew install ffmpeg`, and then nothing.

Being told "install ffmpeg" and being asked "shall I install ffmpeg?" are a
different experience, and only one of them is a tool doing its job. So:
everything missing is listed, the exact commands are shown, and the default
answer is yes to all of it. Pressing return installs everything.

**Two things it will not do.** It never invokes `sudo` on anybody's behalf — on
a distribution whose package manager needs root, the commands are printed to
run by hand, because a program that silently escalates is a program nobody
should run. And it never installs anything without being asked, except when
explicitly told `--yes`, which is there for scripts and for the launcher.

One case is particular to this tool and worth the extra sentence it prints.
ffmpeg is not judged by whether a binary of that name exists but by whether the
build carries `ebur128`, which is the filter every loudness figure in the report
comes from. A slim distribution build is present, is executable, and is of no
use here — so "missing" and "the one you have will not do" have to read as
different problems, or somebody reinstalls a package they already have and is
told it is up to date.
"""

import shutil
import subprocess
import sys

import platform_support as programs

BANNER = "Media Preflight needs a few programs it does not ship."


def survey():
    """Everything missing, split into what the tool cannot work without."""
    absent = programs.missing()
    return {
        "manager": programs.current_manager(),
        "required": [p for p in absent if p.required],
        "optional": [p for p in absent if not p.required],
    }


def unusable_build():
    """True when an ffmpeg exists but is not one this tool can measure with.

    The remedy is the same command either way, so this changes the sentence
    rather than the offer — but a person told to install what they can see
    installed will reasonably conclude the tool is broken.
    """
    return shutil.which("ffmpeg") is not None and programs.find("ffmpeg") is None


def command_for(program, manager):
    """The argv that would install one program, or None if it cannot."""
    name = program.package_for(manager)
    if not name:
        return None
    return programs.MANAGERS[manager]["command"] + name.split()


def describe(program, manager):
    argv = command_for(program, manager)
    return " ".join(argv) if argv else f"(no {manager} package known)"


def install(chosen, manager, on_line=None):
    """Run the installs. Returns [(program, ok, detail)] in the order tried."""
    results = []
    for program in chosen:
        argv = command_for(program, manager)
        if argv is None:
            results.append((program, False, "no package known for this system"))
            continue
        if on_line:
            on_line(f"$ {' '.join(argv)}")
        try:
            done = subprocess.run(argv, capture_output=True, text=True,
                                  timeout=1800)
        except (OSError, subprocess.SubprocessError) as error:
            results.append((program, False, str(error)))
            continue

        # Ask the disk, not the exit code. Homebrew returns non-zero for things
        # that are not failures — already installed, a warning about a linked
        # keg — and the only question that matters is whether the program is
        # there now, and in this tool's case whether it is the right build.
        programs.forget()
        if programs.find(program.key) is not None:
            results.append((program, True, ""))
        else:
            detail = (done.stderr or done.stdout or "").strip().splitlines()
            results.append((program, False, detail[-1] if detail else
                            f"exit {done.returncode}"))
    return results


def offer(assume_yes=False, quiet=False):
    """Show what is missing and install it. Returns True if nothing is left.

    The default is to install everything, because that is what somebody who has
    just opened a media tool wants, and the alternative is a checklist they have
    no way to evaluate.
    """
    state = survey()
    manager = state["manager"]
    outstanding = state["required"] + state["optional"]

    if not outstanding:
        if not quiet:
            print("Everything Media Preflight needs is already installed.")
        return True

    if not quiet:
        print(BANNER)
        print()
        for program in state["required"]:
            print(f"  {program.key:<9} needed  — {program.purpose}")
        for program in state["optional"]:
            print(f"  {program.key:<9} extra   — {program.purpose}")
        print()
        if unusable_build():
            print("There is an ffmpeg here, but it was built without the "
                  "ebur128 filter\nthis tool measures loudness with. "
                  "Reinstalling is what fixes that.")
            print()

    if manager is None:
        print("Media Preflight could not find a package manager to install "
              "them with.", file=sys.stderr)
        print("On macOS, Homebrew is the usual one: https://brew.sh",
              file=sys.stderr)
        return not state["required"]

    label = programs.MANAGERS[manager]["label"]
    needs_root = programs.MANAGERS[manager]["needs_root"]

    if needs_root:
        # Never sudo on somebody's behalf. Show the line; let them run it.
        if not quiet:
            print("Installing these needs root, so Media Preflight will not "
                  "run it for you. Copy this:\n")
            names = [p.package_for(manager) for p in outstanding
                     if p.package_for(manager)]
            print("  " + " ".join(programs.MANAGERS[manager]["command"] + names))
            print()
        return not state["required"]

    if not assume_yes:
        for program in outstanding:
            print(f"  {describe(program, manager)}")
        print()
        what = ("it" if len(outstanding) == 1
                else f"all {len(outstanding)} of these")
        try:
            answer = input(f"Install {what} with {label}? [Y/n] ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print()
            return not state["required"]
        if answer in {"n", "no"}:
            print("\nNothing installed. Starting the tool will ask again.")
            return not state["required"]

    print()
    results = install(outstanding, manager, on_line=print)
    print()
    for program, ok, detail in results:
        if ok:
            version = programs.version(program.key) or "installed"
            print(f"  ✓ {program.key:<9} {version}")
        else:
            print(f"  ✗ {program.key:<9} {detail}", file=sys.stderr)
            print(f"    try by hand: {describe(program, manager)}",
                  file=sys.stderr)

    programs.forget()
    return not programs.missing(required_only=True)


def ready():
    """True when nothing the tool cannot work without is missing."""
    return not programs.missing(required_only=True)


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--check" in argv:
        # For the launcher: silent, and the exit code is the answer.
        return 0 if ready() else 1
    return 0 if offer(assume_yes="--yes" in argv or "-y" in argv) else 1


if __name__ == "__main__":
    sys.exit(main())
