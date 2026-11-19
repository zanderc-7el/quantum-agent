"""Root-run migration check on an existing backup, never the live database.

python -m scripts.verify_result_archive_migration --backup data/backups/<backup>.sqlite
    --output reports/<private-stage>/migration-check
No controller, adapter, model or instrument is instantiated.
"""
import argparse
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3

from backend.common import ROOT, checked_path, dumps, file_hash, now
from backend.result_archive import ResultArchive, backfill
from backend.store import Store


def table_fingerprints(db):
    result = {}
    for (table,) in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name").fetchall():
        quoted = '"' + table.replace('"', '""') + '"'
        content = hashlib.sha256()
        count = 0
        for row in db.execute('SELECT * FROM ' + quoted + ' ORDER BY rowid'):
            value = [({'bytesHex': v.hex()} if isinstance(v, bytes) else v) for v in row]
            content.update(dumps(value).encode('utf-8') + b'\n')
            count += 1
        result[table] = {'count': count, 'sha256': content.hexdigest()}
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--backup', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    source = checked_path(args.backup, parent=ROOT / 'data' / 'backups')
    output = checked_path(args.output, parent=ROOT / 'reports')
    if not source.is_file():
        raise ValueError('backup file missing')
    output.mkdir(parents=True, exist_ok=False)
    target = output / 'migration.sqlite'
    with closing(sqlite3.connect(source.as_uri() + '?mode=ro', uri=True)) as source_db:
        source_db.execute('PRAGMA query_only=ON')
        with closing(sqlite3.connect(str(target))) as target_db:
            source_db.backup(target_db)
    with closing(sqlite3.connect(str(target))) as db:
        before = table_fingerprints(db)
    archive_store = Store(target, root=ROOT)
    with archive_store.transaction() as rt:
        after = table_fingerprints(rt.db)
        added_on_second_backfill = backfill(rt.db, ROOT)
    # Archive tables may already exist in a newer backup; those are excluded
    # from the original authority comparison, never any experiment table.
    archive_tables = {'platform_archive_migrations', 'platform_archive_outbox', 'platform_result_archive'}
    unchanged = all(after.get(name) == fingerprint for name, fingerprint in before.items() if name not in archive_tables)
    status = ResultArchive(archive_store, ROOT).status()
    receipt = {'schema': 'local-archive-migration-check/1', 'at': now(), 'sourceBackup': str(source),
        'sourceSha256': file_hash(source), 'migrationCopy': str(target), 'copySha256': file_hash(target),
        'authorityTablesUnchanged': unchanged, 'secondBackfillAdded': added_on_second_backfill,
        'before': before, 'after': after, 'archiveStatus': status, 'networkAndHardwareUsed': False,
        'passed': unchanged and added_on_second_backfill == 0 and status['local']['recordCount'] == status['cloud']['pendingCount']}
    (output / 'migration-review.json').write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({k: receipt[k] for k in ('passed', 'authorityTablesUnchanged', 'secondBackfillAdded', 'archiveStatus')}, ensure_ascii=False))
    if not receipt['passed']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
