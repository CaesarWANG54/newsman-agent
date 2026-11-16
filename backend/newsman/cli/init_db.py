from newsman.db import SessionLocal, init_db
from newsman.services.sources import sync_default_sources


def main() -> None:
    init_db()
    db = SessionLocal()
    try:
        result = sync_default_sources(db)
    finally:
        db.close()
    print(
        "NewsMan database initialized. "
        f"Seeded sources: {result.inserted}; updated sources: {result.updated}"
    )


if __name__ == "__main__":
    main()
