from sqlalchemy import Column, Integer, String, Float, Boolean, JSON, DateTime, ForeignKey
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.sql import func

Base = declarative_base()

class User(Base):
    __tablename__ = "users"
    user_id = Column(Integer, primary_key=True)
    username = Column(String, nullable=True)
    settings = Column(JSON, default=dict)
    created_at = Column(DateTime, server_default=func.now())

class Watchlist(Base):
    __tablename__ = "watchlist"
    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.user_id"))
    chain_id = Column(String, nullable=False)
    token_address = Column(String, nullable=False)
    symbol = Column(String)
    name = Column(String)
    added_at = Column(DateTime, server_default=func.now())

class AlertHistory(Base):
    __tablename__ = "alert_history"
    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer)
    token_address = Column(String)
    pair_address = Column(String)
    ratio = Column(Float)
    timestamp = Column(DateTime, server_default=func.now())
