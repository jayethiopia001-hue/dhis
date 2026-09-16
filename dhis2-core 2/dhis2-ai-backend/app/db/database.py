from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

from app.config import settings


def _make_engine():
    if settings.cloud_sql_instance:
        from google.cloud.sql.connector import Connector
        import pg8000

        connector = Connector()

        def getconn():
            return connector.connect(
                settings.cloud_sql_instance,
                "pg8000",
                user=settings.postgres_user,
                password=settings.postgres_password,
                db=settings.postgres_db,
            )

        return create_engine(
            "postgresql+pg8000://",
            creator=getconn,
            pool_pre_ping=True,
        )

    return create_engine(
        settings.database_url,
        pool_pre_ping=True,
        connect_args=settings.db_connect_args,
    )


engine = _make_engine()
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
