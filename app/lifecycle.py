"""Read-only retention planning. No scheduler, policy activation or deletion endpoint."""
from datetime import timedelta
from .time_utils import instant


def preview(db, user, body, server):
    if user['role'] != 'admin':
        raise PermissionError('Yönetici erişimi gerekli')
    days = body.get('days', 90)
    offset = body.get('offset', 0)
    if type(days) is not int or not 1 <= days <= 3650 or type(offset) is not int or not 0 <= offset <= 1000000:
        raise ValueError('Saklama günü 1–3650, sayfa konumu geçerli bir sayı olmalı')
    project = body.get('project_id') or None
    server.workspace.check_project(db, user, project)
    cutoff = instant(server.now()) - timedelta(days=days)
    # julianday compares actual instants, including legacy timestamps with offsets.
    where = "project_id IS ? AND status IN ('completed','failed','cancelled','incomplete') AND reserved=0 AND julianday(updated_at)<julianday(?) AND NOT EXISTS(SELECT 1 FROM runs WHERE runs.task_id=tasks.id AND runs.status='running')"
    args = (project, cutoff.isoformat())
    total, cost = db.execute('SELECT count(*),coalesce(sum(cost),0) FROM tasks WHERE ' + where, args).fetchone()
    rows = db.execute('SELECT id,status,updated_at FROM tasks WHERE ' + where + ' ORDER BY updated_at,id LIMIT 101 OFFSET ?', args + (offset,)).fetchall()
    items = [dict(row) for row in rows[:100]]
    # Copies/linked evidence are deliberately retained by history deletion.
    return {'preview_only': True, 'automatic_deletion': False, 'days': days,
            'project_id': project, 'cutoff': cutoff.isoformat(), 'total': total,
            'cost_preserved_usd': cost, 'items': items, 'offset': offset,
            'next_offset': offset + 100 if len(rows) > 100 else None,
            'preserved': ['uploaded_files', 'audit_trail', 'usage_totals', 'knowledge_copies', 'findings', 'playbook_and_lab_snapshots']}
