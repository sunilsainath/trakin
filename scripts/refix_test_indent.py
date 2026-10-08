"""Re-indent bodies flattened under `with expect_raises(...)`.

A converted body sits at the same indent as its `with` header (or less), which is
a syntax error. This finds each header and lifts the following block to
header_indent + 4 until a line appears at indent <= header_indent that is not
part of the call.

Idempotent. Usage:
    python scripts/refix_test_indent.py
"""

from __future__ import annotations

from pathlib import Path

TEST = Path("apps/api/tests/test_rls_security.py")
HEADER = "with expect_raises("


def main() -> int:
    lines = TEST.read_text(encoding="utf-8").split("\n")
    out: list[str] = []
    i = 0
    fixed = 0

    while i < len(lines):
        line = lines[i]
        out.append(line)
        if HEADER not in line:
            i += 1
            continue

        base = len(line) - len(line.lstrip())
        i += 1

        # Collect the flattened block: everything more indented than the header,
        # plus the first line that is not (the continuation of c.execute(...).
        block: list[str] = []
        while i < len(lines):
            nxt = lines[i]
            if not nxt.strip():
                break
            indent = len(nxt) - len(nxt.lstrip())
            if indent <= base:
                # Flattened first body line: consume it and its continuation.
                block.append(nxt)
                i += 1
                while i < len(lines):
                    cont = lines[i]
                    cind = len(cont) - len(cont.lstrip())
                    if cont.strip() and cind > base:
                        block.append(cont)
                        i += 1
                    elif not cont.strip():
                        break
                    else:
                        break
                break
            block.append(nxt)
            i += 1

        for b in block:
            stripped = b.lstrip()
            b_indent = len(b) - len(stripped)
            out.append(" " * (base + 4 + max(0, b_indent - base)) + stripped)
            fixed += 1

    TEST.write_text("\n".join(out), encoding="utf-8")
    print(f"re-indented {fixed} line(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
