"""Produce a portable Windows folder: Studio.exe, runtime, examples and source."""

import argparse
import importlib.metadata
import shutil
import subprocess
import sys
from pathlib import Path

import msdfgen_tool

ROOT = Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default=str(ROOT / "dist"))
    args = parser.parse_args()
    output = Path(args.out).resolve()
    native = ROOT / ".preview"
    if not (native / "reticle_preview.exe").exists():
        raise SystemExit("Run build_native.py first")
    generator = msdfgen_tool.executable()
    example_textures = ROOT / "native/example_textures"
    command = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--onedir",
        "--windowed",
        "--name",
        "MSDFReticleStudio",
        "--distpath",
        str(output),
        "--workpath",
        str(ROOT / "build"),
        "--specpath",
        str(ROOT / "build"),
        "--paths",
        str(ROOT),
    ]
    for source, target in (
        (native / "reticle_preview.exe", ".preview"),
        (native / "shaders", ".preview/shaders"),
        (generator, f".msdfgen/msdfgen-{msdfgen_tool.VERSION}"),
        (ROOT / "third_party", "third_party"),
        (example_textures, "examples/gamedata/textures/wpn"),
    ):
        command += ["--add-data", str(source) + ";" + target]
    subprocess.run(command + [str(ROOT / "author_reticle.py")], check=True)
    bundle = output / "MSDFReticleStudio"
    (bundle / "examples").mkdir(exist_ok=True)
    for source in (ROOT / "reticles").iterdir():
        if source.suffix in (".svg", ".ini", ".json"):
            shutil.copy2(source, bundle / "examples" / source.name)
    sources = bundle / "source"
    sources.mkdir(exist_ok=True)
    for source in ROOT.iterdir():
        if source.suffix in (".py", ".bat", ".txt", ".md") and not source.name.startswith(
            "test_"
        ):
            shutil.copy2(source, sources / source.name)
    shutil.copytree(ROOT / "native", sources / "native", dirs_exist_ok=True)
    for source in (ROOT / "native/shaders").iterdir():
        shutil.copy2(native / "shaders" / source.name, sources / "native/shaders" / source.name)
    shutil.copytree(example_textures, sources / "native/example_textures", dirs_exist_ok=True)
    shutil.copytree(
        ROOT / "reticles",
        sources / "reticles",
        dirs_exist_ok=True,
        ignore=shutil.ignore_patterns("*_reticle"),
    )
    shutil.copytree(ROOT / "third_party", sources / "third_party", dirs_exist_ok=True)
    # Retain notices provided by Python dependencies, including Qt's LGPL terms.
    licences = bundle / "licenses"
    licences.mkdir(exist_ok=True)
    for name in (
        "PySide6-Essentials",
        "shiboken6",
        "numpy",
        "pillow",
        "shapely",
        "pyinstaller",
    ):
        distribution = importlib.metadata.distribution(name)
        for file in distribution.files or []:
            if (
                any(
                    token in str(file).lower()
                    for token in ("license", "licence", "copying", "notice")
                )
                and Path(distribution.locate_file(file)).is_file()
            ):
                target = licences / name / str(file)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(distribution.locate_file(file), target)
    (bundle / "START_HERE.txt").write_text(
        "Run MSDFReticleStudio.exe. Drop a filled SVG into the window or open an example.\n"
        "Use Appearance for paint, illumination and glow. Select a state, then edit its named SVG groups.\n"
        "Hover labels for help. States can reuse patterns without duplicating geometry.\n"
        "Advanced contains calibration and game-controlled lighting. Export DDS writes a validated mod folder.\n"
        "Open File > Examples > Cortex or EOTech for validated personal-test drawings.\n"
        "Preview uses actual game HLSL with explicit SDR scene/player inputs. Live optics/weather require matching inputs.\n"
        "Save Project keeps the drawing and settings together in a portable .reticle file.\n"
        "Keep the entire folder together. Python and Visual Studio are not needed.\n"
        "Authoring instructions and rebuildable source are in source/. Dependency notices are in licenses/.\n",
        encoding="utf-8",
    )
    print(bundle)


if __name__ == "__main__":
    main()
