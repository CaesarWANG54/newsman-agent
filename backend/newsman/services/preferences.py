from datetime import datetime, timezone

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert

from newsman.models import ReadingPreference, Feedback


def preferences(db):
    result = {}
    for old in db.scalars(select(Feedback).order_by(Feedback.created_at, Feedback.id)).all():
        result[str(old.article_id)] = {
            "follow": "saved",
            "noisy": "irrelevant",
            "useful": "useful",
        }[old.vote.value]
    for p in db.scalars(select(ReadingPreference)).all():
        if p.vote == "cleared":
            result.pop(str(p.article_id), None)
        else:
            result[str(p.article_id)] = p.vote
    return result


def set_preference(db, article_id, vote):
    stmt = insert(ReadingPreference).values(article_id=article_id, vote=vote)
    db.execute(
        stmt.on_conflict_do_update(
            index_elements=[ReadingPreference.article_id],
            set_={"vote": vote, "updated_at": datetime.now(timezone.utc)},
            where=ReadingPreference.vote != vote,
        )
    )
    db.commit()
    return {"article_id": str(article_id), "vote": vote}


def clear_preferences(db, article_id=None):
    stmt = delete(ReadingPreference)
    if article_id is not None:
        stmt = stmt.where(ReadingPreference.article_id == article_id)
    db.execute(stmt)
    old_ids = select(Feedback.article_id).distinct()
    if article_id is not None:
        old_ids = old_ids.where(Feedback.article_id == article_id)
    # Tombstones suppress old preference state without deleting the original feedback log.
    for key in db.scalars(old_ids).all():
        insert_stmt = insert(ReadingPreference).values(article_id=key, vote="cleared")
        db.execute(
            insert_stmt.on_conflict_do_update(
                index_elements=[ReadingPreference.article_id],
                set_={"vote": "cleared", "updated_at": datetime.now(timezone.utc)},
            )
        )
    db.commit()
    return {"status": "cleared"}
