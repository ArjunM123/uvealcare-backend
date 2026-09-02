from fastapi import FastAPI, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional
import json
import bcrypt
import jwt
import os
import datetime as dt

from database import get_db, init_db
from models import Case, DataFieldDefinition, DataValue, DiseaseProfile, Patient, User, Decision, Task, ImageUpload

app = FastAPI(title="Clinical Workflow Platform API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

init_db()

# ─── Authentication ─────────────────────────────────────────────────────
# JWT_SECRET should come from a real environment variable once this is
# hosted anywhere beyond your own laptop — the fallback here is only safe
# because this is local-only development. Set the JWT_SECRET env var
# before deploying this anywhere real.
JWT_SECRET = os.environ.get("JWT_SECRET")
if not JWT_SECRET:
    JWT_SECRET = "local-dev-only-insecure-secret-change-before-hosting"
    print("⚠️  JWT_SECRET not set — using an insecure local-dev default. "
          "Set a real JWT_SECRET environment variable before hosting this anywhere.")

JWT_ALGORITHM = "HS256"
JWT_EXPIRY_HOURS = 12

security = HTTPBearer()


def create_access_token(user: User) -> str:
    """Issues a signed token proving who this user is. The frontend sends
    this back on every subsequent request instead of the password."""
    payload = {
        "sub": user.id,
        "email": user.email,
        "role": user.role,
        "exp": dt.datetime.utcnow() + dt.timedelta(hours=JWT_EXPIRY_HOURS),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: Session = Depends(get_db),
) -> User:
    """
    This is the actual enforcement mechanism: every protected endpoint
    below requires this dependency, which verifies the token's signature
    and expiry, then confirms the user it names still exists. Without a
    valid token, the request is rejected before any patient data is
    touched — this is what closes the gap where the login screen checked
    passwords but nothing after it actually verified who was asking.
    """
    token = credentials.credentials
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except jwt.ExpiredSignatureError:
        raise HTTPException(401, "Session expired — please sign in again.")
    except jwt.InvalidTokenError:
        raise HTTPException(401, "Invalid authentication token.")

    user = db.query(User).filter_by(id=payload.get("sub")).first()
    if not user:
        raise HTTPException(401, "User no longer exists.")
    return user


class LoginRequest(BaseModel):
    email: str
    password: str


@app.post("/login")
def login(payload: LoginRequest, db: Session = Depends(get_db)):
    """
    Real authentication: looks up the user by email, then checks the
    submitted password against the stored bcrypt hash. The plain-text
    password is never stored anywhere — only this one-way hash is.
    On success, issues a signed token the frontend must include on every
    subsequent request — this is what actually protects patient data,
    not just the login screen itself.
    """
    user = db.query(User).filter_by(email=payload.email).first()

    # Deliberately vague error message for both "no such user" and "wrong
    # password" — telling an attacker which one is true is itself a
    # security leak (it confirms which emails have accounts).
    invalid_credentials = HTTPException(401, "Invalid email or password")

    if not user:
        raise invalid_credentials

    if not bcrypt.checkpw(payload.password.encode(), user.password_hash.encode()):
        raise invalid_credentials

    return {
        "ok": True,
        "name": user.name,
        "email": user.email,
        "role": user.role,
        "token": create_access_token(user),
    }


class SignupRequest(BaseModel):
    name: str
    email: str
    password: str
    role: str = "clinician"


@app.post("/signup")
def signup(payload: SignupRequest, db: Session = Depends(get_db)):
    """
    Creates a real account — this is the piece that was missing before:
    the app only ever had one hardcoded demo login. Passwords are hashed
    with bcrypt before storage, exactly like the seeded demo account,
    and a real token is issued immediately so a new user lands signed in,
    the same as after a normal login.
    """
    existing = db.query(User).filter_by(email=payload.email).first()
    if existing:
        raise HTTPException(400, "An account with this email already exists.")

    if len(payload.password) < 8:
        raise HTTPException(400, "Password must be at least 8 characters.")

    hashed = bcrypt.hashpw(payload.password.encode(), bcrypt.gensalt()).decode()
    user = User(
        name=payload.name,
        email=payload.email,
        role=payload.role,
        password_hash=hashed,
    )
    db.add(user)
    db.commit()

    return {
        "ok": True,
        "name": user.name,
        "email": user.email,
        "role": user.role,
        "token": create_access_token(user),
    }


class DataValueIn(BaseModel):
    field_key: str
    value: Optional[str] = None
    status: str = "complete"  # "complete" | "missing" | "pending"
    source: Optional[str] = None


@app.get("/cases/{case_id}")
def get_case(case_id: str, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    case = db.query(Case).filter_by(id=case_id).first()
    if not case:
        raise HTTPException(404, "Case not found")
    return {
        "id": case.id,
        "patient": case.patient.name,
        "mrn": case.patient.mrn,
        "diagnosis": case.patient.diagnosis,
        "laterality": case.patient.laterality,
        "dob": case.patient.dob.isoformat() if case.patient.dob else None,
        "sex": case.patient.sex,
        "phone": case.patient.phone,
        "insurance": case.patient.insurance,
        "primary_provider": case.patient.primary_provider,
        "referring_provider": case.patient.referring_provider,
        "disease_profile": case.disease_profile.key,
        "care_stage": case.care_stage,
        "care_stages": json.loads(case.disease_profile.care_stages),
    }


class PatientUpdateIn(BaseModel):
    sex: Optional[str] = None
    phone: Optional[str] = None
    insurance: Optional[str] = None
    primary_provider: Optional[str] = None
    referring_provider: Optional[str] = None


@app.patch("/cases/{case_id}/patient")
def update_patient_info(case_id: str, payload: PatientUpdateIn, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """
    Lets the demographic/contact fields the app previously always showed
    as 'Not recorded' actually be filled in and saved — this is real
    data now, editable through the UI, not a placeholder.
    """
    case = db.query(Case).filter_by(id=case_id).first()
    if not case:
        raise HTTPException(404, "Case not found")

    patient = case.patient
    # Only update fields that were actually included in the request —
    # this lets the frontend save one field at a time if it wants to,
    # without accidentally blanking out the others.
    update_data = payload.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(patient, field, value)

    db.commit()
    return {
        "ok": True,
        "sex": patient.sex,
        "phone": patient.phone,
        "insurance": patient.insurance,
        "primary_provider": patient.primary_provider,
        "referring_provider": patient.referring_provider,
    }


@app.post("/cases/{case_id}/values")
def record_value(case_id: str, payload: DataValueIn, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Record or update a data value for a case — this is what each screen
    (imaging upload, molecular test result, etc.) calls under the hood."""
    case = db.query(Case).filter_by(id=case_id).first()
    if not case:
        raise HTTPException(404, "Case not found")

    field_def = db.query(DataFieldDefinition).filter_by(
        disease_profile_id=case.disease_profile_id, key=payload.field_key
    ).first()
    if not field_def:
        raise HTTPException(400, f"Unknown field '{payload.field_key}' for this disease profile")

    existing = db.query(DataValue).filter_by(
        case_id=case_id, field_definition_id=field_def.id
    ).first()

    if existing:
        existing.value = payload.value
        existing.status = payload.status
        existing.source = payload.source
    else:
        existing = DataValue(
            case_id=case_id, field_definition_id=field_def.id,
            value=payload.value, status=payload.status, source=payload.source,
        )
        db.add(existing)

    db.commit()
    return {"ok": True, "field": field_def.label, "status": existing.status}


@app.get("/cases/{case_id}/readiness")
def get_readiness(case_id: str, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """
    This is the generalized version of your 'Case Readiness: 82%' screen.
    It works for ANY disease profile — it just reads whatever fields that
    profile marked as required_for_readiness and checks their status.

    Each checklist item also includes 'value' and 'source' (e.g. a
    technician's name) when available, so screens like Imaging can show
    real recorded details, not just a complete/missing badge.
    """
    case = db.query(Case).filter_by(id=case_id).first()
    if not case:
        raise HTTPException(404, "Case not found")

    required_fields = db.query(DataFieldDefinition).filter_by(
        disease_profile_id=case.disease_profile_id, required_for_readiness=True
    ).all()

    values_by_field = {
        v.field_definition_id: v
        for v in db.query(DataValue).filter_by(case_id=case_id).all()
    }

    checklist = []
    complete_count = 0
    for field in required_fields:
        val = values_by_field.get(field.id)
        status = val.status if val else "missing"
        if status == "complete":
            complete_count += 1
        checklist.append({
            "key": field.key,
            "field": field.label,
            "category": field.category,
            "status": status,
            "value": val.value if val else None,
            "source": val.source if val else None,
        })

    pct = round((complete_count / len(required_fields)) * 100) if required_fields else 0
    missing = [c["field"] for c in checklist if c["status"] != "complete"]

    return {
        "case_id": case_id,
        "readiness_pct": pct,
        "ready_for_review": pct == 100,
        "checklist": checklist,
        "missing_information": missing,
    }


def compute_readiness_pct(case: Case, db: Session) -> int:
    """Shared by both the single-case readiness endpoint and the case
    list endpoint, so the two never disagree with each other."""
    required_fields = db.query(DataFieldDefinition).filter_by(
        disease_profile_id=case.disease_profile_id, required_for_readiness=True
    ).all()
    if not required_fields:
        return 0
    complete_ids = {
        v.field_definition_id
        for v in db.query(DataValue).filter_by(case_id=case.id, status="complete").all()
    }
    complete_count = sum(1 for f in required_fields if f.id in complete_ids)
    return round((complete_count / len(required_fields)) * 100)


@app.get("/cases")
def list_cases(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """
    Powers the dashboard's patient table. Every row here is computed from
    real data — no hardcoded percentages — so as fields get resolved
    elsewhere in the app, this list updates too.
    """
    cases = db.query(Case).all()
    results = []
    for case in cases:
        pct = compute_readiness_pct(case, db)
        if pct == 100:
            status = "complete"
        elif pct >= 70:
            status = "warning"
        else:
            status = "missing"
        results.append({
            "case_id": case.id,
            "patient_name": case.patient.name,
            "mrn": case.patient.mrn,
            "diagnosis": case.patient.diagnosis,
            "care_stage": case.care_stage,
            "readiness_pct": pct,
            "status": status,
        })
    return results


@app.get("/disease-profiles")
def list_profiles(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    profiles = db.query(DiseaseProfile).all()
    return [{"key": p.key, "display_name": p.display_name,
             "field_count": len(p.field_definitions)} for p in profiles]


class DecisionIn(BaseModel):
    recommendation: str
    rationale: Optional[str] = None
    next_step: Optional[str] = None
    responsible_provider: Optional[str] = None
    follow_up_date: Optional[str] = None  # "YYYY-MM-DD", parsed loosely
    surveillance_protocol: Optional[str] = None


@app.post("/cases/{case_id}/decision")
def save_decision(case_id: str, payload: DecisionIn, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """
    Records the actual output of the whole case-prep workflow: a tumor
    board decision. This is the piece that used to only exist in the
    browser's memory — refreshing the page would erase it. Now it
    persists, and recording a decision also advances the case's stage,
    since in practice a recorded decision IS what moves a case from
    Multidisciplinary Review into Treatment Planning.
    """
    case = db.query(Case).filter_by(id=case_id).first()
    if not case:
        raise HTTPException(404, "Case not found")

    follow_up = None
    if payload.follow_up_date:
        try:
            from datetime import date
            follow_up = date.fromisoformat(payload.follow_up_date)
        except ValueError:
            pass  # leave as None rather than fail the whole save over a bad date

    existing = db.query(Decision).filter_by(case_id=case_id).first()
    if existing:
        existing.recommendation = payload.recommendation
        existing.rationale = payload.rationale
        existing.next_step = payload.next_step
        existing.responsible_provider = payload.responsible_provider
        existing.follow_up_date = follow_up
        existing.surveillance_protocol = payload.surveillance_protocol
    else:
        existing = Decision(
            case_id=case_id,
            recommendation=payload.recommendation,
            rationale=payload.rationale,
            next_step=payload.next_step,
            responsible_provider=payload.responsible_provider,
            follow_up_date=follow_up,
            surveillance_protocol=payload.surveillance_protocol,
        )
        db.add(existing)

    # Advance the care stage — a recorded decision is what moves a case
    # out of "Multidisciplinary Review" in the real world.
    if case.care_stage == "Multidisciplinary Review":
        case.care_stage = "Treatment Planning"

    db.commit()
    return {"ok": True, "case_id": case_id, "care_stage": case.care_stage}


@app.get("/cases/{case_id}/decision")
def get_decision(case_id: str, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Returns the recorded decision for this case, or null if none has
    been recorded yet — lets the frontend know whether to show the form
    or the already-recorded summary when the page loads."""
    decision = db.query(Decision).filter_by(case_id=case_id).first()
    if not decision:
        return None
    return {
        "recommendation": decision.recommendation,
        "rationale": decision.rationale,
        "next_step": decision.next_step,
        "responsible_provider": decision.responsible_provider,
        "follow_up_date": decision.follow_up_date.isoformat() if decision.follow_up_date else None,
        "surveillance_protocol": decision.surveillance_protocol,
        "recorded_at": decision.recorded_at.isoformat() if decision.recorded_at else None,
    }


class TaskIn(BaseModel):
    description: str
    assignee_name: Optional[str] = None
    due_date: Optional[str] = None  # "YYYY-MM-DD"


@app.post("/cases/{case_id}/tasks")
def create_task(case_id: str, payload: TaskIn, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """
    Creates a real task tied to a case. This is what 'Assign task' buttons
    across the app now actually do, instead of being decorative. Tasks
    aren't tied to a specific missing field in the schema — they're just
    a description someone wrote, same as how a real coordinator would
    jot down 'get FAF scheduled' without the system needing to understand
    what FAF means.
    """
    case = db.query(Case).filter_by(id=case_id).first()
    if not case:
        raise HTTPException(404, "Case not found")

    due = None
    if payload.due_date:
        try:
            from datetime import date
            due = date.fromisoformat(payload.due_date)
        except ValueError:
            pass

    task = Task(
        case_id=case_id,
        description=payload.description,
        assignee_name=payload.assignee_name,
        due_date=due,
    )
    db.add(task)
    db.commit()
    return {
        "id": task.id,
        "description": task.description,
        "assignee_name": task.assignee_name,
        "status": task.status,
        "due_date": task.due_date.isoformat() if task.due_date else None,
    }


@app.get("/cases/{case_id}/tasks")
def list_tasks(case_id: str, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Lists all tasks for a case, most recent first — this is what makes
    'Assign task' a real, verifiable action instead of a one-way click
    into nothing. Refreshing the page shows the same assigned tasks."""
    tasks = db.query(Task).filter_by(case_id=case_id).order_by(Task.created_at.desc()).all()
    return [
        {
            "id": t.id,
            "description": t.description,
            "assignee_name": t.assignee_name,
            "status": t.status,
            "due_date": t.due_date.isoformat() if t.due_date else None,
        }
        for t in tasks
    ]


@app.get("/tasks")
def list_all_open_tasks(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """
    Powers the Dashboard's 'Upcoming Tasks' sidebar — every open task
    across every patient, not just one case. This is what replaces the
    5 hardcoded fake tasks that used to live directly in the frontend.
    """
    tasks = (
        db.query(Task)
        .filter_by(status="open")
        .order_by(Task.due_date.is_(None), Task.due_date.asc())
        .limit(10)
        .all()
    )
    return [
        {
            "id": t.id,
            "description": t.description,
            "assignee_name": t.assignee_name,
            "due_date": t.due_date.isoformat() if t.due_date else None,
            "patient_name": t.case.patient.name,
        }
        for t in tasks
    ]


@app.patch("/tasks/{task_id}/complete")
def complete_task(task_id: str, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Marks a task done — closes the loop so assigned tasks aren't
    permanent open items with no way to resolve them."""
    task = db.query(Task).filter_by(id=task_id).first()
    if not task:
        raise HTTPException(404, "Task not found")
    task.status = "done"
    db.commit()
    return {"ok": True, "id": task.id, "status": task.status}


class PatientIn(BaseModel):
    mrn: str
    name: str
    dob: str  # "YYYY-MM-DD"
    laterality: Optional[str] = None
    diagnosis: Optional[str] = None
    disease_profile_key: Optional[str] = None  # defaults to the first configured profile


@app.post("/patients")
def create_patient(payload: PatientIn, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """
    This is what '+ New Patient' actually does now: creates a real Patient
    and a real Case at the "Diagnosis" stage. Every other screen in the
    app already reads whichever patient is selected generically, so a
    brand-new patient immediately works everywhere — Dashboard, Case
    Readiness, Imaging, Tumor Board — with no special-casing needed.
    """
    if db.query(Patient).filter_by(mrn=payload.mrn).first():
        raise HTTPException(400, f"A patient with MRN {payload.mrn} already exists")

    if payload.disease_profile_key:
        profile = db.query(DiseaseProfile).filter_by(key=payload.disease_profile_key).first()
    else:
        profile = db.query(DiseaseProfile).first()
    if not profile:
        raise HTTPException(400, "No disease profile is configured yet")

    try:
        from datetime import date
        dob = date.fromisoformat(payload.dob)
    except ValueError:
        raise HTTPException(400, "dob must be in YYYY-MM-DD format")

    patient = Patient(
        mrn=payload.mrn,
        name=payload.name,
        dob=dob,
        laterality=payload.laterality,
        diagnosis=payload.diagnosis,
    )
    db.add(patient)
    db.flush()

    care_stages = json.loads(profile.care_stages)
    case = Case(
        patient_id=patient.id,
        disease_profile_id=profile.id,
        care_stage=care_stages[0] if care_stages else "Diagnosis",
    )
    db.add(case)
    db.commit()

    return {"case_id": case.id, "patient_id": patient.id, "mrn": patient.mrn, "name": patient.name}


class ImageUploadIn(BaseModel):
    field_key: str
    filename: str
    content_type: str
    data_base64: str  # raw base64 payload, no "data:...;base64," prefix


MAX_IMAGE_BYTES = 8 * 1024 * 1024  # 8MB — generous for a demo photo, small enough to keep this reasonable


@app.post("/cases/{case_id}/images")
def upload_image(case_id: str, payload: ImageUploadIn, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """
    Stores a real image for one field on one case. Replaces any existing
    image for that same field — same upsert pattern as recording a
    regular data value, just for image content instead of text.
    """
    case = db.query(Case).filter_by(id=case_id).first()
    if not case:
        raise HTTPException(404, "Case not found")

    import base64
    try:
        decoded = base64.b64decode(payload.data_base64)
    except Exception:
        raise HTTPException(400, "Invalid image data.")

    if len(decoded) > MAX_IMAGE_BYTES:
        raise HTTPException(400, f"Image too large — max {MAX_IMAGE_BYTES // (1024*1024)}MB.")

    if not payload.content_type.startswith("image/"):
        raise HTTPException(400, "Only image files are accepted.")

    existing = db.query(ImageUpload).filter_by(case_id=case_id, field_key=payload.field_key).first()
    if existing:
        existing.filename = payload.filename
        existing.content_type = payload.content_type
        existing.data_base64 = payload.data_base64
    else:
        db.add(ImageUpload(
            case_id=case_id,
            field_key=payload.field_key,
            filename=payload.filename,
            content_type=payload.content_type,
            data_base64=payload.data_base64,
        ))
    db.commit()
    return {"ok": True, "field_key": payload.field_key, "filename": payload.filename}


@app.get("/cases/{case_id}/images")
def list_images(case_id: str, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Lists which fields have a real uploaded image, without sending the
    (potentially large) image data itself — the frontend uses this to
    know which studies show a real photo vs. just a text finding."""
    images = db.query(ImageUpload).filter_by(case_id=case_id).all()
    return [
        {"field_key": img.field_key, "filename": img.filename, "uploaded_at": img.uploaded_at.isoformat() if img.uploaded_at else None}
        for img in images
    ]


from fastapi.responses import Response as FastAPIResponse


@app.get("/cases/{case_id}/images/{field_key}")
def get_image(case_id: str, field_key: str, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """
    Returns the actual raw image bytes for one field. This stays behind
    the same authentication as everything else — unlike a plain <img>
    tag pointed at a public URL, the frontend has to fetch this with a
    valid token, same as any other protected endpoint.
    """
    import base64
    img = db.query(ImageUpload).filter_by(case_id=case_id, field_key=field_key).first()
    if not img:
        raise HTTPException(404, "No image uploaded for this field.")
    raw = base64.b64decode(img.data_base64)
    return FastAPIResponse(content=raw, media_type=img.content_type)
