from sqlalchemy import Column, Integer, String, DateTime, Boolean, create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
import datetime, os

# 自動判斷：有 DATABASE_URL 用 PostgreSQL（Render），否則用本地 SQLite
DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///./license_system.db")

# Render 的 PostgreSQL URL 開頭是 postgres:// 要改成 postgresql://
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(DATABASE_URL, connect_args=connect_args)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

class License(Base):
    __tablename__ = "licenses"
    id              = Column(Integer, primary_key=True, index=True)
    serial_code     = Column(String, unique=True, index=True)
    type            = Column(String)
    status          = Column(String, default="unused")
    note            = Column(String, nullable=True)
    activation_date = Column(DateTime, nullable=True)
    expiry_date     = Column(DateTime, nullable=True)
    created_at      = Column(DateTime, default=datetime.datetime.utcnow)
    last_login_ip   = Column(String, nullable=True)

class AdminUser(Base):
    __tablename__ = "admins"
    id              = Column(Integer, primary_key=True, index=True)
    username        = Column(String, unique=True, index=True)
    hashed_password = Column(String)
    is_superuser    = Column(Boolean, default=False)

Base.metadata.create_all(bind=engine)
