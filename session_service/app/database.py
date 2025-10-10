from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from shared.config_loader import get_database_url

# Получаем URL из конфигурации
DATABASE_URL = get_database_url()

engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()