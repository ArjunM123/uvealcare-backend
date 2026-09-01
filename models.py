"""
Generalized clinical workflow data model.

Core idea: instead of hardcoding tables per disease (tumor_measurement,
molecular_test, etc.), a DiseaseProfile defines a set of DataFieldDefinitions.
Each Case (a patient's episode of care) collects DataValues against those
field definitions. ReadinessRules say which fields must be filled before a
case is considered ready for multidisciplinary review.

Add a new disease = write a new DiseaseProfile + field definitions + rules.
No schema changes, no new tables, no new frontend code paths.
"""

from sqlalchemy import (
    Column, String, Integer, Float, Boolean, Date, ForeignKey, Text, DateTime
)
from sqlalchemy.orm import declarative_base, relationship
from sqlalchemy.sql import func
import uuid

Base = declarative_base()


def gen_uuid():
    return str(uuid.uuid4())


class User(Base):
    __tablename__ = "users"
    id = Column(String, primary_key=True, default=gen_uuid)
    name = Column(String, nullable=False)
    email = Column(String, unique=True, nullable=False)
    role = Column(String, nullable=False)  # ophthalmologist, radiation_oncologist, nurse, etc.
    password_hash = Column(String, nullable=False)  # bcrypt hash — never store plain text
    password_hash = Column(String, nullable=True)  # set by seed.py; see auth.py for hashing


class Patient(Base):
    __tablename__ = "patients"
    id = Column(String, primary_key=True, default=gen_uuid)
    mrn = Column(String, unique=True, nullable=False)
    name = Column(String, nullable=False)
    dob = Column(Date, nullable=False)
    laterality = Column(String, nullable=True)  # OD / OS / OU — optional, not every disease has this
    diagnosis = Column(String, nullable=True)  # display text, e.g. "Choroidal Melanoma OD"

    cases = relationship("Case", back_populates="patient")


class DiseaseProfile(Base):
    """A pluggable workflow definition. 'uveal_melanoma' is the first one."""
    __tablename__ = "disease_profiles"
    id = Column(String, primary_key=True, default=gen_uuid)
    key = Column(String, unique=True, nullable=False)  # e.g. "uveal_melanoma"
    display_name = Column(String, nullable=False)
    care_stages = Column(Text, nullable=False)  # JSON list, e.g. ["Diagnosis","Imaging",...]

    field_definitions = relationship("DataFieldDefinition", back_populates="disease_profile")
    readiness_rules = relationship("ReadinessRule", back_populates="disease_profile")


class DataFieldDefinition(Base):
    """One collectible field for a disease profile, e.g. 'FAF imaging' or 'GEP result'."""
    __tablename__ = "data_field_definitions"
    id = Column(String, primary_key=True, default=gen_uuid)
    disease_profile_id = Column(String, ForeignKey("disease_profiles.id"), nullable=False)
    key = Column(String, nullable=False)          # e.g. "faf_imaging"
    label = Column(String, nullable=False)        # e.g. "FAF"
    category = Column(String, nullable=False)     # "imaging" | "molecular" | "clinical" | "measurement"
    data_type = Column(String, nullable=False)    # "text" | "number" | "date" | "status"
    required_for_readiness = Column(Boolean, default=True)

    disease_profile = relationship("DiseaseProfile", back_populates="field_definitions")


class ReadinessRule(Base):
    """Groups which fields must be non-empty for a case in this profile to be 'ready'."""
    __tablename__ = "readiness_rules"
    id = Column(String, primary_key=True, default=gen_uuid)
    disease_profile_id = Column(String, ForeignKey("disease_profiles.id"), nullable=False)
    description = Column(String, nullable=False)  # e.g. "All required imaging must be complete"

    disease_profile = relationship("DiseaseProfile", back_populates="readiness_rules")


class Case(Base):
    __tablename__ = "cases"
    id = Column(String, primary_key=True, default=gen_uuid)
    patient_id = Column(String, ForeignKey("patients.id"), nullable=False)
    disease_profile_id = Column(String, ForeignKey("disease_profiles.id"), nullable=False)
    care_stage = Column(String, nullable=False, default="Diagnosis")
    created_at = Column(DateTime, server_default=func.now())

    patient = relationship("Patient", back_populates="cases")
    disease_profile = relationship("DiseaseProfile")
    values = relationship("DataValue", back_populates="case")
    tasks = relationship("Task", back_populates="case")


class DataValue(Base):
    """The actual collected value for a field, on a specific case."""
    __tablename__ = "data_values"
    id = Column(String, primary_key=True, default=gen_uuid)
    case_id = Column(String, ForeignKey("cases.id"), nullable=False)
    field_definition_id = Column(String, ForeignKey("data_field_definitions.id"), nullable=False)
    value = Column(Text, nullable=True)          # store as text, cast on read per data_type
    status = Column(String, default="pending")   # "complete" | "missing" | "pending"
    source = Column(String, nullable=True)       # e.g. "technician: J. Alvarez"
    recorded_at = Column(DateTime, server_default=func.now())

    case = relationship("Case", back_populates="values")
    field_definition = relationship("DataFieldDefinition")


class Task(Base):
    __tablename__ = "tasks"
    id = Column(String, primary_key=True, default=gen_uuid)
    case_id = Column(String, ForeignKey("cases.id"), nullable=False)
    assignee_id = Column(String, ForeignKey("users.id"), nullable=True)
    assignee_name = Column(String, nullable=True)  # free text — "Imaging Dept.", "Dr. K. Hartman" — doesn't require a User row to exist
    description = Column(String, nullable=False)
    status = Column(String, default="open")  # "open" | "done"
    due_date = Column(Date, nullable=True)
    created_at = Column(DateTime, server_default=func.now())

    case = relationship("Case", back_populates="tasks")
    assignee = relationship("User")


class Decision(Base):
    """A recorded multidisciplinary tumor board decision — the actual
    output of the whole case-prep workflow. One case has at most one
    current decision; recording a new one overwrites the old one."""
    __tablename__ = "decisions"
    id = Column(String, primary_key=True, default=gen_uuid)
    case_id = Column(String, ForeignKey("cases.id"), unique=True, nullable=False)
    recommendation = Column(Text, nullable=False)
    rationale = Column(Text, nullable=True)
    next_step = Column(String, nullable=True)
    responsible_provider = Column(String, nullable=True)
    follow_up_date = Column(Date, nullable=True)
    surveillance_protocol = Column(String, nullable=True)
    recorded_at = Column(DateTime, server_default=func.now())

    case = relationship("Case")
