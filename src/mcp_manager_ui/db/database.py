# ================================================
# FILE: src/mcp_manager_ui/db/database.py
# ================================================
import os
from sqlmodel import create_engine, Session, SQLModel
from dotenv import load_dotenv

# Загружаем переменные окружения (если есть .env файл)
load_dotenv()

# Определяем путь к файлу БД. Можно взять из .env или использовать дефолт.
# Лучше хранить данные не прямо в src, а, например, в корне проекта или специальной data/ папке.
DATABASE_DIR = os.path.join('mcpo_manager_data')
os.makedirs(DATABASE_DIR, exist_ok=True)
DATABASE_FILE = os.path.join(DATABASE_DIR, "mcp_manager_data.db")
DATABASE_URL = f"sqlite:///{DATABASE_FILE}"

# Создаем "движок" для подключения к БД.
# connect_args={"check_same_thread": False} нужен только для SQLite,
# чтобы разрешить использование сессии из разных потоков (что FastAPI делает).
engine = create_engine(DATABASE_URL, echo=True, connect_args={"check_same_thread": False})

def create_db_and_tables():
    """
    Создает файл базы данных и все таблицы, определенные через SQLModel.
    Вызывается один раз при старте приложения.
    """
    SQLModel.metadata.create_all(engine)

def get_session():
    """
    Зависимость FastAPI для получения сессии базы данных.
    Обеспечивает открытие и закрытие сессии для каждого запроса.
    """
    with Session(engine) as session:
        yield session