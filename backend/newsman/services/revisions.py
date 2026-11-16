from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from datetime import date, datetime, timezone
from pathlib import Path

from sqlalchemy import func

from newsman.models import Digest, DigestHead, DigestRevision
from newsman.services.run_lock import RunConflict, locked_session


def canonical_json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def content_hash(value):
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def revision_content(revision):
    return {key: getattr(revision, key) for key in ("title", "brief", "sections", "markdown")}


def create_revision(db, run, content, status):
    existing = db.query(DigestRevision).filter_by(run_id=run.id, attempt=run.attempt).one_or_none()
    if existing:
        return existing
    number = (
        db.query(func.max(DigestRevision.revision)).filter_by(digest_date=run.target_date).scalar()
        or 0
    ) + 1
    from newsman.services.newsletter import render_markdown

    context = next(s for s in content["sections"] if s["key"] == "run-context")
    context.update(
        run_id=str(run.id),
        attempt=run.attempt,
        revision=number,
        pipeline_version=run.pipeline_version,
        source_version="source-health-v1/event-v2",
        summary_version="bilingual-v2",
        run_status=status,
        diagnostics={
            key: value for key, value in run.diagnostics.items() if key != "preference_snapshot"
        },
    )
    content["markdown"] = (
        f"<!-- NewsMan revision={number} run_id={run.id} attempt={run.attempt} "
        f"pipeline={run.pipeline_version} sources=source-health-v1/event-v2 "
        f"summary=bilingual-v2 status={status} -->\n"
        + render_markdown(run.target_date, content["sections"])
    )
    digest_hash = content_hash(content)
    revision = DigestRevision(
        digest_date=run.target_date,
        revision=number,
        run_id=run.id,
        attempt=run.attempt,
        pipeline_version=run.pipeline_version,
        source_version="source-health-v1/event-v2",
        summary_version="bilingual-v2",
        content_hash=digest_hash,
        status=status,
        **content,
    )
    db.add(revision)
    db.flush()
    return revision


def serialize_digest(value):
    if value is None:
        return None
    revision = isinstance(value, DigestRevision)
    return {
        "id": str(value.id),
        "digest_date": value.digest_date.isoformat(),
        "created_at": value.created_at.astimezone(timezone.utc).isoformat(),
        **revision_content(value),
        "revision": value.revision if revision else 0,
        "run_id": str(value.run_id) if revision else None,
        "attempt": value.attempt if revision else None,
        "pipeline_version": value.pipeline_version if revision else "legacy_unknown",
        "source_version": value.source_version if revision else "legacy_unknown",
        "summary_version": value.summary_version if revision else "legacy_unknown",
        "content_hash": value.content_hash if revision else content_hash(revision_content(value)),
        "status": value.status if revision else "legacy_unknown",
        "publication_status": ("published" if value.exported_at else "pending_export")
        if revision
        else "legacy_read_only",
        "read_only": True,
    }


def read_digest(db, day, revision=None):
    if revision is None:
        head = db.get(DigestHead, day)
        revision = head.revision if head else 0
    if revision == 0:
        return db.query(Digest).filter_by(digest_date=day).one_or_none()
    return db.query(DigestRevision).filter_by(digest_date=day, revision=revision).one_or_none()


def history(db, limit=100):
    days = db.query(Digest.digest_date).union(db.query(DigestHead.digest_date))
    return [
        serialize_digest(read_digest(db, day))
        for (day,) in days.order_by(Digest.digest_date.desc()).limit(limit).all()
    ]


def export_revision(revision, output_dir=None):
    root = (
        Path(output_dir)
        if output_dir is not None
        else Path(__file__).resolve().parents[3] / "digests"
    )
    parent = root / "revisions" / revision.digest_date.isoformat()
    parent.mkdir(parents=True, exist_ok=True)
    destination = parent / f"v{revision.revision}"
    payload = serialize_digest(revision)
    payload["publication_status"] = "published"
    if content_hash(revision_content(revision)) != revision.content_hash:
        raise RunConflict("Revision hash mismatch")
    contents = {"digest.json": canonical_json(payload) + "\n", "digest.md": revision.markdown}
    if destination.exists():
        for name, value in contents.items():
            if (destination / name).read_bytes() != value.encode("utf-8"):
                raise RunConflict("Existing export differs; never overwrite it")
        return destination
    temporary = Path(tempfile.mkdtemp(prefix=".pending-", dir=parent))
    try:
        for name, value in contents.items():
            with (temporary / name).open("xb") as handle:
                handle.write(value.encode("utf-8"))
                handle.flush()
                os.fsync(handle.fileno())
        temporary.rename(destination)
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)
    return destination


def promote(db, revision):
    head = db.get(DigestHead, revision.digest_date)
    if head is None:
        head = DigestHead(digest_date=revision.digest_date, revision=revision.revision)
        db.add(head)
    head.revision = revision.revision
    head.updated_at = datetime.now(timezone.utc)


def rollback_head(engine, day: date, revision: int, expected_revision: int):
    with locked_session(engine) as db:
        current = read_digest(db, day)
        current_number = current.revision if isinstance(current, DigestRevision) else 0
        if current is None or current_number != expected_revision:
            raise RunConflict("Active revision changed")
        target = read_digest(db, day, revision)
        if target is None:
            raise RunConflict("Unknown revision")
        if isinstance(target, DigestRevision):
            # A failed export is not a published version and cannot be selected by rollback.
            if target.exported_at is None:
                raise RunConflict("Revision export is not complete")
        head = db.get(DigestHead, day)
        if head is None:
            head = DigestHead(digest_date=day, revision=revision)
            db.add(head)
        head.revision = revision
        head.updated_at = datetime.now(timezone.utc)
        db.commit()
        return serialize_digest(target)


def compare(db, day, left, right):
    a, b = read_digest(db, day, left), read_digest(db, day, right)
    if a is None or b is None:
        raise RunConflict("Unknown comparison revision")

    def items(value):
        # cluster IDs can change with membership; article URLs are comparison anchors only.
        return {
            item["url"] or f"insight:{section['key']}": item
            for section in value.sections
            for item in section["items"]
        }

    aa, bb = items(a), items(b)
    return {
        "digest_date": day.isoformat(),
        "from_revision": left,
        "to_revision": right,
        "added": sorted(bb.keys() - aa.keys()),
        "removed": sorted(aa.keys() - bb.keys()),
        "changed": sorted(k for k in aa.keys() & bb.keys() if aa[k] != bb[k]),
        "content_changed": content_hash(revision_content(a)) != content_hash(revision_content(b)),
    }
