#!/usr/bin/env python3
"""Every asset a built docs page references actually resolves.

`check_docs.py` validates links in the *markdown source*. It cannot see this class of bug, because
the failure is introduced by the build: MkDocs rewrites markdown paths (`![](img/x.png)` becomes
`../img/x.png` on a page served at `/portal/`) and leaves **raw HTML attributes untouched**. So a
hand-written `<video><source src="img/…">` on that page resolves to `/portal/img/…` — a 404 that
looks exactly like a codec problem, and cost two wrong fixes before it was found.

    mkdocs build && python scripts/check_site_assets.py site
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

#: Attributes that name a file the browser must fetch.
_REF = re.compile(r'(?:src|poster|href)\s*=\s*"([^"]+)"', re.I)

#: Only local media matters here. A missing stylesheet breaks loudly; a missing video does not.
_MEDIA = {".mp4", ".webm", ".vtt", ".png", ".jpg", ".jpeg", ".svg", ".gif", ".webp"}


def main() -> int:
    root = Path(sys.argv[1] if len(sys.argv) > 1 else "site")
    if not root.is_dir():
        print(f"no built site at {root} — run `mkdocs build` first")
        return 1

    missing: list[str] = []
    checked = 0
    for page in sorted(root.rglob("*.html")):
        for ref in _REF.findall(page.read_text(errors="ignore")):
            if ref.startswith(("http://", "https://", "//", "#", "data:", "mailto:")):
                continue
            if Path(ref).suffix.lower() not in _MEDIA:
                continue
            checked += 1
            clean = ref.split("#")[0].split("?")[0]
            if clean.startswith("/"):
                # Root-absolute, and the site is published under a base path (`/swarmkit/`), so
                # the leading segment is the base rather than a directory in the build. Resolving
                # it against `root` naively reports a false failure, and a check that cries wolf
                # is one people learn to skip.
                parts = clean.lstrip("/").split("/")
                candidates = [root.joinpath(*parts), root.joinpath(*parts[1:])]
            else:
                candidates = [(page.parent / clean).resolve()]
            if not any(c.exists() for c in candidates):
                missing.append(f"{page.relative_to(root)} -> {ref}")

    print(f"checked {checked} asset reference(s) across the built site")
    if missing:
        print("\nFAIL — referenced but not built:")
        for m in missing:
            print(f"  {m}")
        return 1
    print("OK — every referenced asset exists.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
