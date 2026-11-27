"""Third-batch immutable closure; does not change either earlier manifest."""
import json
from pathlib import Path
from adapters.science_runtime.policy import sha,digest
from adapters.science_v3_adapter import verify_extension

FROZEN_V3='3b52bd3e5684ea8c66cc4a08106daf6b3ccf0bb04be43c5b0b16db784271652d'

def verify_sources(root):
    root=Path(root).resolve()
    if sha(root/'adapters/science_v3/source_manifest.json')!=FROZEN_V3:raise ValueError('Frozen six-method V3 source closure changed')
    sources=verify_extension(root)
    path=root/'adapters/science_v3_irb/source_manifest.json';rows=json.loads(path.read_text(encoding='utf-8'))
    if not rows:raise ValueError('IRB source manifest is empty')
    for row in rows:
        file=(root/row['path']).resolve()
        if not file.is_relative_to(root/'adapters/science_v3_irb') or sha(file)!=row['sha256']:raise ValueError('IRB source changed: '+row['path'])
    sources['adapters/science_v3_irb/source_manifest.json']={'sha256':sha(path),'fileCount':len(rows)}
    return sources
