from sqlalchemy.engine import URL
from sqlalchemy.ext.asyncio import create_async_engine

from config.settings import get_settings

settings = get_settings()

db_url = URL.create(
    drivername="postgresql+asyncpg",
    username=settings.db_user,
    password=settings.db_password,
    host=settings.db_host,
    port=settings.db_port,
    database=settings.db_name,
)

engine = create_async_engine(db_url)
