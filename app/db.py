from sqlmodel import SQLModel, create_engine, Session
from sqlalchemy import event

DB_URL = "sqlite:///./emoji.db"
engine = create_engine(DB_URL, connect_args={"check_same_thread": False})

@event.listens_for(engine, "connect")
def _fk_pragma(dbapi_conn, _):
    cur = dbapi_conn.cursor()
    cur.execute("PRAGMA foreign_keys=ON")
    cur.close()

def init_db() -> None:
    from app import models
    SQLModel.metadata.create_all(engine)

def get_session():
    with Session(engine) as session:
        yield session