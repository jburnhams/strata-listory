"""Which files make up each listen (library-relative paths), for matching listens to decisions made elsewhere on the same files."""

from __future__ import annotations

from sqlalchemy import text

from app.linkage.query import relpath

FILES = text("""with recursive g(listen_id, id) as (
    select p.listen_id, p.filegroup_id from listen_part p join listen l on l.id = p.listen_id where p.role = 'input' and p.unlinked_at is null and p.filegroup_id is not null and l.merged_into_id is null
    union all select g.listen_id, m.child_group_id from part_filegroup_member m join g on m.group_id = g.id where m.child_group_id is not null)
select g.listen_id, loc.path from g join part_filegroup_member m on m.group_id = g.id join part_file_location loc on loc.file_id = m.file_id where loc.gone_at is null""")
PATHS = text(
    "select p.listen_id, p.detail->>'path' from listen_part p join listen l on l.id = p.listen_id where p.role = 'input' and p.unlinked_at is null and p.ordinal = 1 and l.merged_into_id is null"
)


def listen_files(s) -> tuple[dict[int, set[str]], dict[int, str]]:
    files: dict[int, set[str]] = {}
    for lid, path in s.execute(FILES):
        files.setdefault(lid, set()).add(relpath(path))
    return files, {lid: p for lid, p in s.execute(PATHS) if p}
