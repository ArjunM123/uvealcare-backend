"""
Database tables for roles: the care team on each case, the access/audit log,
and role sign-offs. Kept in its own file so nothing in models.py changes.
The tables are created automatically the first time the server starts.
"""

from sqlalchemy import Column, String, DateTime, Text, Integer, UniqueConstraint
from sqlalchemy.sql import func

from models import Base, gen_uuid


class CareTeamMember(Base):
    __tablename__ = "care_team_members"
    __table_args__ = (UniqueConstraint("case_id", "user_id", name="uq_team_case_user"),)
    id = Column(String, primary_key=True, default=gen_uuid)
    case_id = Column(String, nullable=False, index=True)
    user_id = Column(String, nullable=False, index=True)
    team_role = Column(String, nullable=False)        # the role they hold on this case
    added_by = Column(String, nullable=True)          # name of whoever added them
    created_at = Column(DateTime, server_default=func.now())


class AuditEntry(Base):
    __tablename__ = "audit_log"
    id = Column(String, primary_key=True, default=gen_uuid)
    at = Column(DateTime, server_default=func.now(), index=True)
    user_id = Column(String, nullable=True)
    user_name = Column(String, nullable=True)
    role = Column(String, nullable=True)
    action = Column(String, nullable=False)
    method = Column(String, nullable=True)
    path = Column(String, nullable=True)
    case_id = Column(String, nullable=True, index=True)
    status_code = Column(Integer, nullable=True)
    detail = Column(Text, nullable=True)


class CaseSignoff(Base):
    __tablename__ = "case_signoffs"
    id = Column(String, primary_key=True, default=gen_uuid)
    case_id = Column(String, nullable=False, index=True)
    role = Column(String, nullable=False)
    user_id = Column(String, nullable=False)
    user_name = Column(String, nullable=False)
    note = Column(Text, nullable=True)
    at = Column(DateTime, server_default=func.now())
