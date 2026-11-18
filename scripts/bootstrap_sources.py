"""Import provided source archives into an isolated project; never touch original workspaces."""
from pathlib import Path
import hashlib
import json
import shutil
import zipfile

ROOT = Path(__file__).resolve().parents[1]
FRONT = Path('C:/Users/o/Downloads/超导量子自进化测控平台_前端源码_v1.3.1_20261117.zip')
EXPERIENCE = Path('C:/Users/o/Downloads/量子测控智能体工程包_原流程15步版_20261024/量子测控智能体工程包_原流程15步版_20261024')

def copy_archive(source, target):
    target.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(source) as archive:
        for info in archive.infolist():
            parts = Path(info.filename).parts[1:]
            if not parts or info.is_dir():
                continue
            dest = (target.joinpath(*parts)).resolve()
            if not dest.is_relative_to(target.resolve()):
                raise ValueError('Archive path escapes target')
            if dest.exists():
                raise FileExistsError(dest)
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(archive.read(info))

def main():
    copy_archive(FRONT, ROOT / 'frontend')
    dst = ROOT / 'vendor' / 'experience'
    for name in ('quantum_agents', 'config', 'knowledge', 'agents', 'docs', 'tests'):
        shutil.copytree(EXPERIENCE / name, dst / name,
                        ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    for name in ('README.md', 'MANIFEST.json'):
        shutil.copy2(EXPERIENCE / name, dst / name)
    manifest = {
        'frontend': {'source': str(FRONT), 'sha256': hashlib.sha256(FRONT.read_bytes()).hexdigest()},
        'experience': {'source': str(EXPERIENCE), 'files': {
            str(p.relative_to(dst)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in dst.rglob('*') if p.is_file()
        }},
        'protected_workspace': 'D:/qoskit-workspace',
    }
    (ROOT / 'source_manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'project': str(ROOT), 'experience_files': len(manifest['experience']['files'])}, ensure_ascii=False))

if __name__ == '__main__':
    main()
