"""Independent R23 source closure, pinning every unchanged earlier runtime."""
import json
from pathlib import Path
from adapters.science_runtime.policy import sha
from adapters.science_v3_irb.policy import verify_sources as verify_prior

FROZEN_IRB='504771ef9bd10b2951c1e999456f39da9bad05213f423032acd1e7b7c489e5e4'

def verify_sources(root):
    root=Path(root).resolve()
    if sha(root/'adapters/science_v3_irb/source_manifest.json')!=FROZEN_IRB:raise ValueError('Frozen earlier scientific runtime changed')
    sources=verify_prior(root);path=root/'adapters/science_shape_repeat5/source_manifest.json'
    rows=json.loads(path.read_text('utf8'))
    if not rows:raise ValueError('Repeat5 source manifest is empty')
    for row in rows:
        file=(root/row['path']).resolve()
        if not file.is_relative_to(root/'adapters/science_shape_repeat5') or sha(file)!=row['sha256']:
            raise ValueError('Repeat5 source changed: '+row['path'])
    sources['adapters/science_shape_repeat5/source_manifest.json']={'sha256':sha(path),'fileCount':len(rows)}
    return sources
