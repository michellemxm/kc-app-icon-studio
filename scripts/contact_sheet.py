#!/usr/bin/env python3
"""Render an Icon Studio contact sheet. This is the agent's PROVE step.

    python3 contact_sheet.py --job 20260805-001
    python3 contact_sheet.py --dir /path/to/svgs --out /tmp/sheet.png

Prints the PNG paths on success and exits 0; prints the reason and exits 1 when
the sheet cannot be produced (no icons, or no browser installed). It never
reports success without having written a PNG — an unverified icon set is the one
failure mode this whole script exists to prevent.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parent.parent
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from backend.contact_sheet import (  # noqa: E402
    RenderError,
    render_dir,
)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Render an icon contact sheet.")
    ap.add_argument("--job", help="Icon Studio job id (resolves paths automatically)")
    ap.add_argument("--dir", help="Directory of .svg files (ad-hoc mode)")
    ap.add_argument("--out", help="Output PNG path (ad-hoc mode)")
    ap.add_argument(
        "--sizes",
        help="Comma-separated target sizes in px (default: the job's, or 16,24,32)",
    )
    args = ap.parse_args(argv)

    sizes = None
    if args.sizes:
        try:
            sizes = [int(s) for s in args.sizes.split(",") if s.strip()]
        except ValueError:
            print("--sizes must be comma-separated integers", file=sys.stderr)
            return 2

    try:
        if args.job:
            # store, not contact_sheet: only the store knows which library the job
            # belongs to, and therefore which folder its SVGs are in.
            from backend.store import render_job_sheet

            sheet = render_job_sheet(args.job, sizes)
        elif args.dir:
            directory = Path(args.dir).expanduser().resolve()
            out = Path(args.out).expanduser() if args.out else directory / "contact-sheet.png"
            out_2x = out.with_name(out.stem + "@2x" + out.suffix)
            sheet = render_dir(directory, out, out_2x, sizes, title=directory.name)
        else:
            ap.error("one of --job or --dir is required")
            return 2
    except RenderError as exc:
        print(f"contact sheet FAILED: {exc}", file=sys.stderr)
        return 1
    except ValueError as exc:
        print(f"contact sheet FAILED: {exc}", file=sys.stderr)
        return 1

    print(f"icons:  {sheet.icon_count}")
    print(f"sizes:  {', '.join(str(s) + 'px' for s in sheet.sizes)}")
    print(f"1x:     {sheet.png_1x}")
    print(f"2x:     {sheet.png_2x}")
    print("Now READ the 1x PNG and judge the pixels before claiming these work.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
