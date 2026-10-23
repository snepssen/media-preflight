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

How it asks is workshop-core's `Bootstrap`; the words are this tool's.
"""

import shutil
import sys

import platform_support as programs
from workshop.bootstrap import Bootstrap

BANNER = "Media Preflight needs a few programs it does not ship."


def unusable_build():
    """True when an ffmpeg exists but is not one this tool can measure with.

    The remedy is the same command either way, so this changes the sentence
    rather than the offer — but a person told to install what they can see
    installed will reasonably conclude the tool is broken.
    """
    return shutil.which("ffmpeg") is not None and programs.find("ffmpeg") is None


def _note():
    if unusable_build():
        return ("There is an ffmpeg here, but it was built without the "
                "ebur128 filter\nthis tool measures loudness with. "
                "Reinstalling is what fixes that.")
    return None


_setup = Bootstrap(
    programs._registry,
    banner=BANNER,
    again="Starting the tool will ask again.",
    note=_note,
    # Asked through this module's names, so a test that replaces them is heard.
    manager=lambda: programs.current_manager(),
)

survey = _setup.survey
command_for = _setup.command_for
describe = _setup.describe
install = _setup.install
ready = _setup.ready


def offer(assume_yes=False, quiet=False):
    """Show what is missing and install it. Returns True if nothing is left."""
    return _setup.offer(assume_yes=assume_yes, quiet=quiet)


def main(argv=None):
    return _setup.main(argv)


if __name__ == "__main__":
    sys.exit(main())
