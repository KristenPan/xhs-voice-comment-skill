#!/usr/bin/env python3
"""Lazy dependency bootstrap for the XHS voice-comment skill.

The skill stays lightweight by default. This script creates a skill-local venv
that can see system site packages, then installs only the capabilities the
current task needs.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import venv
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
VENV_DIR = ROOT / ".venv"
MARKER = VENV_DIR / ".xhs_voice_capabilities.json"

FEATURES: dict[str, dict[str, object]] = {
    "tls": {
        "packages": ["certifi>=2024.8.30"],
        "check": "import certifi",
        "post": [],
        "description": "trusted certificate bundle",
    },
    "qr": {
        "packages": ["opencv-python-headless>=4.9.0.80"],
        "check": "import cv2",
        "post": [],
        "description": "QR-code decoding from share images",
    },
    "precision": {
        "packages": ["playwright>=1.44.0"],
        "check": "import playwright.sync_api",
        "post": [["-m", "playwright", "install", "chromium"]],
        "description": "signed H5 comment pagination for exact anchor lookup",
    },
    "convert": {
        "packages": ["imageio-ffmpeg>=0.5.1"],
        "check": "import imageio_ffmpeg; imageio_ffmpeg.get_ffmpeg_exe()",
        "post": [],
        "description": "bundled ffmpeg for MP3/WAV conversion",
    },
}


def venv_python() -> Path:
    if os.name == "nt":
        return VENV_DIR / "Scripts" / "python.exe"
    return VENV_DIR / "bin" / "python"


def run(cmd: list[str], *, quiet: bool = False) -> None:
    if not quiet:
        print("+", " ".join(cmd))
    subprocess.run(cmd, check=True)


def ensure_venv(*, quiet: bool = False) -> None:
    if venv_python().exists():
        return
    if not quiet:
        print(f"Creating skill-local Python runtime: {VENV_DIR}")
    venv.EnvBuilder(with_pip=True, clear=False, system_site_packages=True).create(VENV_DIR)


def python_check(code: str) -> bool:
    if not venv_python().exists():
        return False
    proc = subprocess.run([str(venv_python()), "-c", code], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return proc.returncode == 0


def chromium_check() -> bool:
    code = (
        "from pathlib import Path\n"
        "from playwright.sync_api import sync_playwright\n"
        "with sync_playwright() as p:\n"
        "    raise SystemExit(0 if Path(p.chromium.executable_path).exists() else 1)\n"
    )
    return python_check(code)


def feature_ok(feature: str) -> bool:
    spec = FEATURES[feature]
    if not python_check(str(spec["check"])):
        return False
    if feature == "precision" and not chromium_check():
        return False
    return True


def read_marker() -> dict[str, object]:
    if not MARKER.exists():
        return {}
    try:
        return json.loads(MARKER.read_text(encoding="utf-8"))
    except Exception:
        return {}


def write_marker(installed: list[str]) -> None:
    data = read_marker()
    features = set(data.get("features", []))
    features.update(installed)
    data["features"] = sorted(features)
    data["python"] = str(venv_python())
    MARKER.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def ensure_feature(feature: str, *, quiet: bool = False, force: bool = False) -> bool:
    if feature not in FEATURES:
        raise SystemExit(f"Unknown feature: {feature}")
    ensure_venv(quiet=quiet)
    if not force and feature_ok(feature):
        if not quiet:
            print(f"Capability ready: {feature}")
        return False

    spec = FEATURES[feature]
    if not quiet:
        print(f"Preparing capability: {feature} ({spec['description']})")
    py = str(venv_python())
    run([py, "-m", "pip", "install", "--upgrade", "pip", "wheel"], quiet=quiet)
    packages = list(spec["packages"])  # type: ignore[arg-type]
    if packages:
        run([py, "-m", "pip", "install", *packages], quiet=quiet)
    for post_args in spec["post"]:  # type: ignore[union-attr]
        run([py, *post_args], quiet=quiet)
    if not feature_ok(feature):
        raise SystemExit(f"Capability setup failed: {feature}")
    write_marker([feature])
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description="Prepare XHS voice-comment skill capabilities on demand.")
    parser.add_argument("--ensure-venv", action="store_true", help="Only create the lightweight skill-local venv.")
    parser.add_argument("--feature", action="append", choices=sorted(FEATURES), help="Capability to verify/install. Can repeat.")
    parser.add_argument("--all", action="store_true", help="Install every optional capability.")
    parser.add_argument("--force", action="store_true", help="Reinstall requested capabilities.")
    parser.add_argument("--quiet", action="store_true", help="Reduce setup logging.")
    parser.add_argument("--print-python", action="store_true", help="Print the skill-local Python path.")
    args = parser.parse_args()

    if args.ensure_venv or args.print_python:
        ensure_venv(quiet=args.quiet)
    features = sorted(FEATURES) if args.all else (args.feature or [])
    installed: list[str] = []
    for feature in features:
        changed = ensure_feature(feature, quiet=args.quiet, force=args.force)
        if changed:
            installed.append(feature)
    if installed:
        write_marker(installed)
    if args.print_python:
        print(venv_python())
    if not features and not args.print_python and not args.quiet:
        print("Skill-local runtime is ready.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
