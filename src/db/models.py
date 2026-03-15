import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean, Column, DateTime, Float, ForeignKey, Integer, JSON, String, Text
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, relationship
from sqlalchemy.sql import func


class Base(DeclarativeBase):
    pass


class Agent(Base):
    __tablename__ = "agents"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(64), unique=True, nullable=False)          # "scout", "outreach", etc.
    display_name = Column(String(128), nullable=False)
    system_prompt = Column(Text, default="")
    model = Column(String(128), default="claude-sonnet-4-20250514")
    batch_size = Column(Integer, default=5)
    enabled = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())


class Objective(Base):
    __tablename__ = "objectives"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    title = Column(String(256), nullable=False)
    description = Column(Text, default="")
    target_date = Column(String(16), nullable=True)  # YYYY-MM-DD
    active = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    key_results = relationship(
        "KeyResult", back_populates="objective", cascade="all, delete-orphan"
    )


class KeyResult(Base):
    __tablename__ = "key_results"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    objective_id = Column(
        UUID(as_uuid=True), ForeignKey("objectives.id", ondelete="CASCADE"), nullable=False
    )
    title = Column(String(256), nullable=False)
    metric = Column(String(128), default="")
    target_value = Column(Float, nullable=True)
    current_value = Column(Float, default=0.0)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    objective = relationship("Objective", back_populates="key_results")


class Run(Base):
    __tablename__ = "runs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    agent_name = Column(String(64), nullable=False)
    started_at = Column(DateTime(timezone=True), server_default=func.now())
    completed_at = Column(DateTime(timezone=True), nullable=True)
    status = Column(String(16), default="ok")   # ok, error, skipped
    turns = Column(Integer, nullable=True)
    input_tokens = Column(Integer, nullable=True)
    output_tokens = Column(Integer, nullable=True)
    summary = Column(Text, default="")
    tools_used = Column(JSON, default=list)
    focus = Column(Text, default="")
