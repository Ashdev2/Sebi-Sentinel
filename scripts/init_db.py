from app.database import Base, engine
import app.models  # noqa: F401


def main():
    print("Creating SEBI Sentinel database tables...")
    Base.metadata.create_all(bind=engine)
    print("Database ready.")


if __name__ == "__main__":
    main()
