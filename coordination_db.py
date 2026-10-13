"""
Database tables for care coordination: imaging study log, surveillance
schedule, decision history and outcomes, tumor board agenda, clinical trials.
Kept in its own file so nothing in models.py changes. The tables are created
automatically the first time the server starts.

Decision history: the existing decisions table keeps one row per case and
overwrites it when a new decision is recorded. A small listener below copies
every saved version into decision_history, so the board can see what was
recommended at the time even after the plan changes.
"""

import datetime as _dt
from sqlalchemy import Column, String, DateTime, Text, Integer, Boolean, Date, event, select, insert
from sqlalchemy.sql import func

from models import Base, Decision, gen_uuid


class ImagingStudy(Base):
    __tablename__ = "imaging_studies"
    id = Column(String, primary_key=True, default=gen_uuid)
    case_id = Column(String, nullable=False, index=True)
    modality = Column(String, nullable=False)
    eye = Column(String, nullable=False)
    status = Column(String, nullable=False)
    ordered_date = Column(Date, nullable=True)
    performed_date = Column(Date, nullable=True)
    due_next_date = Column(Date, nullable=True)
    location = Column(String, nullable=True)
    outside = Column(Boolean, default=False)
    read_by = Column(String, nullable=True)
    result_summary = Column(Text, nullable=True)
    comparison_note = Column(Text, nullable=True)
    created_by = Column(String, nullable=True)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class SurveillanceItem(Base):
    __tablename__ = "surveillance_items"
    id = Column(String, primary_key=True, default=gen_uuid)
    case_id = Column(String, nullable=False, index=True)
    kind = Column(String, nullable=False)
    label = Column(String, nullable=False)
    interval_months = Column(Integer, nullable=False)
    start_date = Column(Date, nullable=True)
    last_done = Column(Date, nullable=True)
    study_type = Column(String, nullable=True)
    eye = Column(String, nullable=True)
    notes = Column(Text, nullable=True)
    active = Column(Boolean, default=True)
    created_by = Column(String, nullable=True)
    created_at = Column(DateTime, server_default=func.now())


class DecisionHistory(Base):
    __tablename__ = "decision_history"
    id = Column(String, primary_key=True, default=gen_uuid)
    case_id = Column(String, nullable=False, index=True)
    recommendation = Column(Text, nullable=True)
    rationale = Column(Text, nullable=True)
    next_step = Column(String, nullable=True)
    responsible_provider = Column(String, nullable=True)
    follow_up_date = Column(Date, nullable=True)
    surveillance_protocol = Column(String, nullable=True)
    logged_at = Column(DateTime, server_default=func.now())


class DecisionOutcome(Base):
    __tablename__ = "decision_outcomes"
    id = Column(String, primary_key=True, default=gen_uuid)
    case_id = Column(String, nullable=False, index=True)
    status = Column(String, nullable=False)
    note = Column(Text, nullable=False)
    event_date = Column(Date, nullable=True)
    recommendation_then = Column(Text, nullable=True)   # what the board had recommended when this was written
    recorded_by = Column(String, nullable=True)
    recorded_at = Column(DateTime, server_default=func.now())


class BoardAgendaItem(Base):
    __tablename__ = "board_agenda"
    id = Column(String, primary_key=True, default=gen_uuid)
    case_id = Column(String, nullable=False, unique=True)
    note = Column(String, nullable=True)
    added_by = Column(String, nullable=True)
    added_at = Column(DateTime, server_default=func.now())


class Trial(Base):
    __tablename__ = "trials"
    id = Column(String, primary_key=True, default=gen_uuid)
    name = Column(String, nullable=False)
    nct_id = Column(String, nullable=True)
    summary = Column(Text, nullable=True)
    source_note = Column(String, nullable=True)
    last_verified = Column(Date, nullable=True)
    criteria_json = Column(Text, nullable=False, default="[]")
    active = Column(Boolean, default=True)
    created_by = Column(String, nullable=True)
    created_at = Column(DateTime, server_default=func.now())


_VERSION_FIELDS = ("recommendation", "rationale", "next_step", "responsible_provider",
                   "follow_up_date", "surveillance_protocol")


def _log_decision_version(mapper, connection, target):
    t = DecisionHistory.__table__
    vals = {f: getattr(target, f, None) for f in _VERSION_FIELDS}
    last = connection.execute(
        select(*[t.c[f] for f in _VERSION_FIELDS])
        .where(t.c.case_id == target.case_id)
        .order_by(t.c.logged_at.desc(), t.c.id.desc())
        .limit(1)
    ).first()
    if last is not None and all(last[i] == vals[f] for i, f in enumerate(_VERSION_FIELDS)):
        return  # saved again with nothing changed
    connection.execute(insert(t).values(id=gen_uuid(), case_id=target.case_id, logged_at=_dt.datetime.utcnow(), **vals))


event.listen(Decision, "after_insert", _log_decision_version)
event.listen(Decision, "after_update", _log_decision_version)
