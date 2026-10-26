"""Local evidence/report store. Database-relative paths remain compatible with backups."""
import os
import re
import stat
import tempfile
from pathlib import Path


class LocalStore:
    def __init__(self, database):
        self.root = Path(database).absolute().parent

    def folder(self, kind, create=False):
        if kind not in ('uploads', 'artifacts'):
            raise ValueError('Geçersiz dosya deposu')
        path = self.root / kind
        if create:
            path.mkdir(mode=0o700, parents=True, exist_ok=True)
        if path.is_symlink() or path.exists() and not path.is_dir():
            raise ValueError('Dosya deposu güvenli bir klasör olmalı')
        return path

    def path(self, kind, identifier, create=False):
        if not isinstance(identifier, str) or not re.fullmatch(r'[a-f0-9]{32}', identifier):
            raise ValueError('Geçersiz dosya kimliği')
        path = self.folder(kind, create) / (identifier + '.txt' if kind == 'artifacts' else identifier)
        if path.is_symlink():
            raise ValueError('Dosya bağlantısı kullanılamaz')
        return path

    def read(self, kind, identifier):
        path = self.path(kind, identifier)
        fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
        with os.fdopen(fd, 'rb') as source:
            if not stat.S_ISREG(os.fstat(source.fileno()).st_mode):
                raise ValueError('Normal dosya gerekli')
            return source.read()

    def write(self, kind, identifier, content, replace=False):
        target = self.path(kind, identifier, create=True)
        # Finish private temporary bytes before publishing the final opaque name.
        fd, name = tempfile.mkstemp(prefix='.pending-', dir=target.parent)
        temp = Path(name)
        try:
            with os.fdopen(fd, 'wb') as out:
                out.write(content)
                out.flush()
                os.fsync(out.fileno())
            if replace:
                # replace replaces a link itself, never follows its destination.
                os.replace(temp, target)
            else:
                os.link(temp, target)  # Exclusive publication; never overwrite evidence.
        finally:
            temp.unlink(missing_ok=True)
        return target

    def remove(self, kind, identifier):
        self.path(kind, identifier).unlink(missing_ok=True)


def inventory(db, database, scan_limit=10000):
    """Metadata only, no hashing, network, creation, chmod or automatic cleanup."""
    store = LocalStore(database)
    result = {'backend': 'local', 'read_only': True, 'hashes_checked': False,
              'scan_limit': scan_limit, 'truncated': False, 'database_bytes': 0, 'stores': {}}
    for suffix in ('', '-wal', '-shm'):
        path = Path(str(database) + suffix)
        if path.is_file() and not path.is_symlink():
            result['database_bytes'] += path.stat().st_size
    for kind, table in (('uploads', 'files'), ('artifacts', 'tasks')):
        refs = {r[0]: r[1] for r in db.execute('SELECT id,' + ('size' if kind == 'uploads' else '0') + ' FROM ' + table + ' ORDER BY id LIMIT ?', (scan_limit + 1,))}
        complete = len(refs) <= scan_limit
        if not complete:
            result['truncated'] = True
            refs = dict(list(refs.items())[:scan_limit])
        counts = {'files': 0, 'bytes': 0, 'unreferenced': 0, 'unsafe_entries': 0,
                  'missing': 0, 'size_mismatches': 0}
        folder = store.folder(kind)
        seen = set()
        if folder.exists():
            with os.scandir(folder) as entries:
                for index, entry in enumerate(entries):
                    if index >= scan_limit:
                        result['truncated'] = True
                        break
                    valid = re.fullmatch(r'[a-f0-9]{32}' + (r'\.txt' if kind == 'artifacts' else ''), entry.name)
                    if not valid or not entry.is_file(follow_symlinks=False):
                        counts['unsafe_entries'] += 1
                        continue
                    identifier = entry.name[:32]
                    size = entry.stat(follow_symlinks=False).st_size
                    counts['files'] += 1
                    counts['bytes'] += size
                    seen.add(identifier)
                    if identifier in refs:
                        if kind == 'uploads' and size != refs[identifier]:
                            counts['size_mismatches'] += 1
                    elif complete:
                        counts['unreferenced'] += 1
        # Reports are optional; a task without an approved artifact is not missing.
        if kind == 'uploads':
            for identifier in refs.keys() - seen:
                try:
                    path = store.path(kind, identifier)
                    if not path.is_file():
                        counts['missing'] += 1
                except ValueError:
                    counts['missing'] += 1
        counts['references_complete'] = complete
        result['stores'][kind] = counts
    return result
