#!/usr/bin/env python3
"""
Builds the chat page's stylesheet with Tailwind CSS.

Tailwind reads src/fastapi_ai_assistant/tailwind.css, finds the classes the templates use,
and writes only the CSS they need to src/fastapi_ai_assistant/static/app.css. That file is
committed, so the app runs without this step: run it after changing classes or tailwind.css.
    uv run python scripts/build_css.py           # Build once
    uv run python scripts/build_css.py --watch   # Rebuild on every change while you work
    uv run python scripts/build_css.py --check   # Fail if app.css is out of date (CI does this)

It uses Tailwind's standalone CLI, so it doesn't need Node.js. The first run downloads the CLI
into .cache/, and every run checks it against the SHA-256 below before running it, the way
browsers check the SRI hashes in index.html. To update Tailwind, change VERSION and the hashes
together: they are in the sha256sums.txt of the release.
"""

import hashlib
import platform
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

VERSION = "v4.3.3"
# SHA-256 of the CLI for each platform, from the release's sha256sums.txt.
SHA256 = {
    "linux-x64": "dc61b3ac6b8c9ca874c0cc4c57b2409791a64c5540404ca5f5367360babc313a",
    "linux-arm64": "55fd0b241214eff3de1e8ee4f22796662f2d2e7a49bcfca7477cfd0bac398195",
    "macos-x64": "7922e0953f2110c05976e3bf58f14e643d90427575e766b7d433f5f80cbee7e1",
    "macos-arm64": "cdf646702987a743464dff4d9c60fd4480d1c1e73dd819a9a67f1078815dce9d",
    "windows-x64.exe": "e0e260ce048014e9268f6237ff18f8ccf02cef521cbd0ae04e82c2cdf7aa3955",
}
# Tailwind's name for each (sys.platform, platform.machine()).
PLATFORMS = {
    ("linux", "x86_64"): "linux-x64",
    ("linux", "aarch64"): "linux-arm64",
    ("darwin", "x86_64"): "macos-x64",
    ("darwin", "arm64"): "macos-arm64",
    ("win32", "AMD64"): "windows-x64.exe",
}

ROOT = Path(__file__).resolve().parent.parent
PACKAGE = ROOT / "src" / "fastapi_ai_assistant"
SOURCE = PACKAGE / "tailwind.css"
STYLESHEET = PACKAGE / "static" / "app.css"
CACHE = ROOT / ".cache" / "tailwindcss" / VERSION


def tailwind_cli() -> Path:
    """
    The CLI for this machine: downloaded on first use and checked on every use.
    """
    name = PLATFORMS.get((sys.platform, platform.machine()))
    if name is None:
        sys.exit(f"Tailwind has no standalone CLI for {sys.platform} on {platform.machine()}.")
    cli = CACHE / f"tailwindcss-{name}"
    if not cli.exists():
        url = f"https://github.com/tailwindlabs/tailwindcss/releases/download/{VERSION}/{cli.name}"
        print(f"Downloading {url}", file=sys.stderr)
        CACHE.mkdir(parents=True, exist_ok=True)
        partial = cli.with_name(f"{cli.name}.part")  # Renamed once complete
        with urllib.request.urlopen(url, timeout=60) as response, partial.open("wb") as file:
            shutil.copyfileobj(response, file)
        partial.chmod(0o755)
        partial.replace(cli)

    with cli.open("rb") as file:
        digest = hashlib.file_digest(file, "sha256").hexdigest()
    if digest != SHA256[name]:
        cli.unlink()
        sys.exit(f"{cli.name} doesn't match the SHA-256 pinned here: deleted without running it.")
    return cli


def build(output: Path, *options: str) -> None:
    """
    Run Tailwind on tailwind.css and write the minified CSS to `output`.
    """
    command = [tailwind_cli(), "--input", SOURCE, "--output", output, "--minify", *options]
    try:
        returncode = subprocess.run(command, cwd=ROOT).returncode
    except KeyboardInterrupt:  # Ctrl+C is how --watch ends
        returncode = 0
    if returncode != 0:
        sys.exit(returncode)


def main() -> None:
    options = sys.argv[1:]
    if options != ["--check"]:
        build(STYLESHEET, *options)  # Other options, like --watch, go to Tailwind
        return

    with tempfile.TemporaryDirectory() as directory:
        fresh = Path(directory) / "app.css"
        build(fresh)
        if not STYLESHEET.is_file() or fresh.read_bytes() != STYLESHEET.read_bytes():
            sys.exit(
                f"{STYLESHEET.relative_to(ROOT)} is out of date: "
                "run `uv run python scripts/build_css.py` and commit the result."
            )
    print(f"{STYLESHEET.relative_to(ROOT)} is up to date.")


if __name__ == "__main__":
    main()
