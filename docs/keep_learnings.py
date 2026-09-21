#!/usr/bin/env python3
"""Keep only some pillar sections of LEARNINGS.md, for a release tag.

  python3 docs/keep_learnings.py General Build Scale            # rewrite docs/LEARNINGS.md
  python3 docs/keep_learnings.py General Build --check          # print what would remain

Each section sits between `<!-- BEGIN: Name -->` and `<!-- END: Name -->`. Sections not
listed are removed, markers included. Run it on a TAG's copy, never on main: it also replaces
the authoring notes at the top with a one-line intro.
"""
import pathlib
import re
import sys

PATH = pathlib.Path(__file__).with_name("LEARNINGS.md")


def main(argv: list[str]) -> int:
    check = "--check" in argv
    keep = {a for a in argv if not a.startswith("--")}
    text = PATH.read_text()
    names = re.findall(r"<!-- BEGIN: (\w+) -->", text)
    unknown = keep - set(names)
    if not keep or unknown:
        print(f"usage: keep_learnings.py <section>... [--check]\nsections: {', '.join(names)}"
              + (f"\nunknown: {', '.join(sorted(unknown))}" if unknown else ""))
        return 2
    for name in names:
        if name not in keep:
            text = re.sub(rf"\n?<!-- BEGIN: {name} -->.*?<!-- END: {name} -->\n?", "\n", text, flags=re.S)
    # A tag's copy is for attendees: swap the authoring notes (how to cut tags) for one line.
    text = re.sub(r"\*\*How this file is organised\.\*\*.*?\n---\n",
                  "Entries are grouped by pillar.\n\n---\n", text, count=1, flags=re.S)
    entries = len(re.findall(r"(?m)^### ", text))
    if check:
        print(f"would keep {sorted(keep)}: {entries} entries, {len(text.splitlines())} lines")
        return 0
    PATH.write_text(text)
    print(f"kept {sorted(keep)}: {entries} entries")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
