# resume_customization/build_resume.py
# Usage examples:
#   uv run python resume_customization/build_resume.py
#   uv run python resume_customization/build_resume.py --open
#   uv run python resume_customization/build_resume.py --clean

from __future__ import annotations
import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
TEX  = HERE / "main.tex"
OUTDIR = HERE / "build"
PDF  = OUTDIR / "main.pdf"

def run(cmd: list[str], cwd: Path):
    print("+", " ".join(cmd))
    subprocess.check_call(cmd, cwd=str(cwd))

def have(cmd: str) -> bool:
    return shutil.which(cmd) is not None

def clean():
    if OUTDIR.exists():
        for p in OUTDIR.iterdir():
            try:
                p.unlink()
            except IsADirectoryError:
                pass
    OUTDIR.mkdir(exist_ok=True)

def build_with_tectonic() -> bool:
    # Prefer new CLI first; fall back to old flags
    try:
        run(["tectonic", "-X", "compile", str(TEX), "--outdir", str(OUTDIR)], HERE)
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        try:
            run(["tectonic", "-o", str(OUTDIR), str(TEX)], HERE)
            return True
        except (subprocess.CalledProcessError, FileNotFoundError):
            return False

def build_with_latexmk() -> bool:
    if not have("latexmk"):
        return False
    try:
        # Clean intermediates only for this project dir
        run(["latexmk", "-lualatex", "-C"], HERE)
        run(["latexmk", "-lualatex", f"-output-directory={OUTDIR.name}", TEX.name], HERE)
        return True
    except subprocess.CalledProcessError:
        return False

def build_with_lualatex() -> bool:
    if not have("lualatex"):
        return False
    try:
        # Two passes is typically enough for this resume
        run(["lualatex", "-interaction=nonstopmode", "-halt-on-error",
             f"-output-directory={OUTDIR.name}", TEX.name], HERE)
        run(["lualatex", "-interaction=nonstopmode", "-halt-on-error",
             f"-output-directory={OUTDIR.name}", TEX.name], HERE)
        return True
    except subprocess.CalledProcessError:
        return False

def open_pdf():
    if sys.platform == "darwin":
        subprocess.call(["open", str(PDF)])
    elif os.name == "nt":
        os.startfile(str(PDF))  # type: ignore[attr-defined]
    else:
        subprocess.call(["xdg-open", str(PDF)])

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--clean", action="store_true", help="Clean build artifacts")
    ap.add_argument("--open",  action="store_true", help="Open PDF after building")
    args = ap.parse_args()

    if args.clean:
        clean()
        print("Cleaned.")
        return

    if not TEX.exists():
        print(f"ERROR: {TEX} not found", file=sys.stderr)
        sys.exit(2)

    OUTDIR.mkdir(exist_ok=True)

    # Try engines in preferred order
    ok = False
    if have("tectonic"):
        ok = build_with_tectonic()
    if not ok:
        ok = build_with_latexmk()
    if not ok:
        ok = build_with_lualatex()

    if not ok or not PDF.exists():
        print("\nBuild failed, and no PDF was produced.", file=sys.stderr)
        print("Quick fix: install the tiny Tectonic engine and re-run:", file=sys.stderr)
        print("  macOS:  brew install tectonic")
        print("  Ubuntu: sudo apt-get install tectonic")
        print("  Windows (Admin): choco install tectonic   (or)   scoop install tectonic")
        sys.exit(3)

    print(f"\nOK → {PDF}")
    if args.open:
        open_pdf()

if __name__ == "__main__":
    main()
