"""Build the Dictate Windows x64 installer using Python 3.12 and Inno Setup 6."""

from argparse import ArgumentParser
from hashlib import file_digest
from pathlib import Path
from shutil import copy2, copyfileobj, copytree, ignore_patterns
from subprocess import run
import os
import re
import sys
import sysconfig
from tempfile import TemporaryDirectory
from urllib.request import urlopen
from zipfile import ZipFile


PROJECT = Path(__file__).resolve().parents[1]
PACKAGING = PROJECT / "packaging" / "windows"
OUTPUT = PROJECT / "artifacts" / "windows"
GEMMA_RUNTIME_TAG = "b12774"
GEMMA_RUNTIME_FILE = f"llama-{GEMMA_RUNTIME_TAG}-bin-win-vulkan-x64.zip"
GEMMA_RUNTIME_URL = f"https://github.com/ggml-org/llama.cpp/releases/download/{GEMMA_RUNTIME_TAG}/{GEMMA_RUNTIME_FILE}"
GEMMA_RUNTIME_SHA256 = "d29a16265d26ef7598209caaf1a544cbfa6abe2858c24b0ce4ae6bff87a76f22"
GEMMA_LICENSE_URL = f"https://raw.githubusercontent.com/ggml-org/llama.cpp/{GEMMA_RUNTIME_TAG}/LICENSE"
GEMMA_LICENSE_SHA256 = "94f29bbed6a22c35b992c5c6ebf0e7c92f13b836b90f36f461c9cf2f0f1d010d"
SMOKE_CHECK = """
import sys
import tkinter as tk
from tkinter import font

from src.recording_cues import RECORDING_CUES
from src.desktop_theme import DesktopStyles
from src.config import DEFAULT_CONFIG
from src.gemma import gemma_executable
from src.paths import IS_INSTALLED
from src.recorder import list_microphones
from src.transcriber import WhisperTranscriber
from src.tray import SystemTrayApp

assert IS_INSTALLED and DEFAULT_CONFIG['device'] == 'auto'
assert sys.version_info[:2] == (3, 12)
assert all(path.is_file() for path in RECORDING_CUES.values())
assert gemma_executable().is_file()
root = tk.Tk()
root.withdraw()
try:
    styles = DesktopStyles(root)
    assert font.Font(root=root, family=styles.font).actual('family').startswith('Segoe UI')
finally:
    root.destroy()
print('Packaged Python, application imports, Tk, and Windows fonts verified.')
"""


def verified_build_asset(cache, scratch, filename, url, expected):
    asset = cache / filename
    if not asset.is_file():
        staged = scratch / filename
        with urlopen(url, timeout=60) as response, staged.open("wb") as output:
            copyfileobj(response, output)
        asset = staged
    with asset.open("rb") as binary:
        if file_digest(binary, "sha256").hexdigest() != expected:
            raise ValueError(f"Checksum mismatch for {asset}")
    if asset.parent == scratch:
        asset = asset.replace(cache / filename)
    return asset


def prepare_gemma_runtime(payload):
    cache = PROJECT / "artifacts" / "windows-build" / "gemma-runtime"
    cache.mkdir(parents=True, exist_ok=True)
    archive = verified_build_asset(cache, payload.parent, GEMMA_RUNTIME_FILE, GEMMA_RUNTIME_URL, GEMMA_RUNTIME_SHA256)
    license_file = verified_build_asset(cache, payload.parent, "llama.LICENSE", GEMMA_LICENSE_URL, GEMMA_LICENSE_SHA256)
    gemma = payload / "assets" / "gemma"
    gemma.mkdir(parents=True)
    required = {
        "llama-server.exe", "llama-server-impl.dll", "llama.dll", "llama-common.dll", "mtmd.dll",
        "ggml.dll", "ggml-base.dll", "ggml-vulkan.dll", "libomp.dll", "LICENSE-LLVM-OpenMP",
    }
    with ZipFile(archive) as package:
        if missing := required.difference(package.namelist()):
            raise ValueError(f"Missing llama.cpp files: {', '.join(sorted(missing))}")
        for name in package.namelist():
            if name in required or re.fullmatch(r"ggml-cpu-[a-z0-9]+\.dll", name):
                package.extract(name, gemma)
    copy2(license_file, gemma / "LICENSE-llama.cpp")
    run([gemma / "llama-server.exe", "--version"], cwd=gemma, check=True)


def prepare_payload(payload, version):
    base = Path(sys.base_prefix)
    runtime = payload / "runtime"
    runtime.mkdir(parents=True)
    for filename in ("python.exe", "pythonw.exe", "python3.dll", "python312.dll",
                     "vcruntime140.dll", "vcruntime140_1.dll", "LICENSE.txt"):
        copy2(base / filename, runtime / filename)
    for directory in ("DLLs", "Lib", "tcl"):
        copytree(base / directory, runtime / directory,
                 ignore=ignore_patterns("*.pdb", "__pycache__", "site-packages", "test"))

    copytree(PROJECT / "src", payload / "src", ignore=ignore_patterns("__pycache__"))
    copytree(PROJECT / "assets" / "sounds", payload / "assets" / "sounds")
    prepare_gemma_runtime(payload)
    copy2(PROJECT / "icon.ico", payload)
    copy2(PACKAGING / "requirements-cuda.txt", payload)
    (payload / "dictate-installed.txt").write_text(version + "\n", encoding="utf-8")

    python = runtime / "python.exe"
    run([python, "-I", "-m", "ensurepip", "--upgrade"], cwd=payload, check=True)
    run([python, "-I", "-m", "pip", "--isolated", "install", "--disable-pip-version-check",
         "--only-binary=:all:", "-r", PROJECT / "requirements-core.txt", "ctranslate2==4.8.0"],
        cwd=payload, check=True)
    run([python, "-I", "-m", "pip", "check"], cwd=payload, check=True)

    with TemporaryDirectory(prefix="dictate-test-", dir=payload.parent) as profile:
        run([python, "-E", "-s", "-B", "-c", SMOKE_CHECK], cwd=payload,
            env={**os.environ, "LOCALAPPDATA": profile}, check=True)


def main():
    parser = ArgumentParser(description=__doc__)
    parser.add_argument("--compiler", type=Path, required=True, help="Path to Inno Setup's ISCC.exe")
    parser.add_argument("--version", required=True, help="Release version, for example 0.1.0")
    args = parser.parse_args()
    if sysconfig.get_platform() != "win-amd64" or sys.version_info[:2] != (3, 12):
        parser.error("Use 64-bit Windows Python 3.12, including Tk and ensurepip.")
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", args.version):
        parser.error("Use a numeric major.minor.patch version.")
    if not args.compiler.is_file():
        parser.error("ISCC.exe was not found at --compiler.")

    build_dir = PROJECT / "artifacts" / "windows-build"
    build_dir.mkdir(parents=True, exist_ok=True)
    OUTPUT.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix="build-", dir=build_dir) as temporary:
        scratch = Path(temporary)
        payload = scratch / "payload"
        prepare_payload(payload, args.version)
        run([args.compiler.resolve(), "/Q", f"/DVersion={args.version}", f"/DPayloadDir={payload}",
             f"/DOutputPath={scratch}", PACKAGING / "dictate.iss"], check=True)
        installer = OUTPUT / "Dictate-Setup-x64.exe"
        (scratch / installer.name).replace(installer)

    with installer.open("rb") as binary:
        checksum = file_digest(binary, "sha256").hexdigest()
    installer.with_suffix(".exe.sha256").write_text(f"{checksum}  {installer.name}\n", encoding="ascii")
    print(f"Built {installer}")


if __name__ == "__main__":
    main()
