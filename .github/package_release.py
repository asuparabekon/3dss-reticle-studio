"""Validate the build output and prepare release assets."""
import argparse
import hashlib
import os
from pathlib import Path
import re
import zipfile

ROOT = Path(__file__).resolve().parents[1]
version = (ROOT / 'VERSION').read_text().strip()
name = '3DSS-Reticle-Studio'
parser = argparse.ArgumentParser()
parser.add_argument('--check-version', action='store_true')
args = parser.parse_args()
if not re.fullmatch(r'\d+\.\d+\.\d+', version):
    raise SystemExit('Version must be major.minor.patch')
if os.environ.get('GITHUB_REF_TYPE') == 'tag' and os.environ.get('GITHUB_REF_NAME') != 'v' + version:
    raise SystemExit('Tag must match the source version: v' + version)
if args.check_version:
    print('Version:', version)
    raise SystemExit(0)
output = ROOT / 'release'
output.mkdir(exist_ok=True)
import json
bundle = ROOT / 'dist/MSDFReticleStudio'
required = ('MSDFReticleStudio.exe', 'START_HERE.txt', 'source/README.md', 'source/AUTHORING.md',
            '_internal/.preview/reticle_preview.exe', '_internal/.preview/shaders/preview_reticle.ps.cso',
            'examples/Cortex.svg', 'examples/EOTech.svg', '_internal/.msdfgen/msdfgen-1.13/msdfgen.exe')
for rel in required:
    if not (bundle / rel).is_file():
        raise SystemExit('Missing portable resource: ' + rel)
native = bundle / '_internal/.preview'
manifest = json.loads((native / 'shaders/manifest.json').read_text())
for rel, expected in manifest.items():
    p = bundle / 'source/native/reticle_preview.cpp' if rel == 'reticle_preview.cpp' else native / 'shaders' / rel
    if hashlib.sha256(p.read_bytes()).hexdigest() != expected:
        raise SystemExit('Preview source/bytecode mismatch: ' + rel)
archive = output / f'{name}-{version}-Windows-x64.zip'
files = {p.relative_to(bundle.parent).as_posix(): p for p in bundle.rglob('*')
         if p.is_file() and '__pycache__' not in p.parts}
with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED) as z:
    for rel, p in sorted(files.items()):
        z.write(p, rel)
with zipfile.ZipFile(archive) as z:
    if z.testzip() is not None or set(z.namelist()) != set(files) or len(z.namelist()) != len(files):
        raise SystemExit('Archive contents differ from the build')
    for rel, p in files.items():
        if z.read(rel) != p.read_bytes():
            raise SystemExit('Archive bytes differ: ' + rel)
digest = hashlib.sha256(archive.read_bytes()).hexdigest()
(output / 'SHA256SUMS.txt').write_text(f'{digest}  {archive.name}\n', encoding='utf-8')
print(archive.name, digest)
