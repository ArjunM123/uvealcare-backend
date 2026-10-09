"""
Roles and permissions for UvealCare. Pure rules: no database, no web server,
so every rule can be tested on its own (see test_roles.py).

THE MODEL IN ONE PARAGRAPH
Every account has one role. Three roles are "specialist" roles and are
deliberately limited:

  radiation_oncologist  (also: the older "medical_oncologist" is treated as
  systemic_oncologist    systemic, see ALIASES)

A specialist can only open cases they have been added to (the case's "care
team"), and can only edit the kinds of data that belong to their specialty.
Everyone else (ocular oncologist, admin, and every older role such as
"ophthalmologist", "nurse_navigator", "coordinator") keeps the access they
had before, so installing this does not lock anyone out except the specialists
until an ocular oncologist or admin adds them to a case.
"""

from __future__ import annotations

import re
from typing import Callable, Optional

# ── The role catalogue (also sent to the website to build dropdowns) ──────
ROLE_INFO = {
    "admin": {
        "label": "Administrator",
        "summary": "Manages accounts and roles. Full access to every case.",
    },
    "ocular_oncologist": {
        "label": "Ocular Oncologist",
        "summary": "Owns the case. Full access: diagnosis, imaging, staging, "
                   "the treatment decision, and what the patient sees.",
    },
    "radiation_oncologist": {
        "label": "Radiation Oncologist",
        "summary": "Sees cases they are added to. Can edit treatment fields and "
                   "clinical notes and add imaging (for example planning scans). "
                   "Cannot change staging, molecular results or the decision.",
    },
    "systemic_oncologist": {
        "label": "Systemic (Medical) Oncologist",
        "summary": "Sees cases they are added to. Can edit molecular results and "
                   "the metastatic work-up. Can view imaging but not upload it.",
    },
    # Older roles, unchanged behaviour (full access):
    "ophthalmologist": {"label": "Ophthalmologist", "summary": "Full access (original role)."},
    "nurse_navigator": {"label": "Nurse Navigator", "summary": "Full access (original role)."},
    "coordinator": {"label": "Tumor Board Coordinator", "summary": "Full access (original role)."},
    "clinician": {"label": "Clinician", "summary": "Full access (original role)."},
}

# Old name -> the role whose rules apply
ALIASES = {"medical_oncologist": "systemic_oncologist"}

RESTRICTED_ROLES = {"radiation_oncologist", "systemic_oncologist"}

# Which kinds of fields each specialist may edit. Anything not listed is
# read-only for them. (Categories are the ones already in the database.)
WRITE_CATEGORIES = {
    "radiation_oncologist": {"treatment", "clinical"},
    "systemic_oncologist": {"molecular", "systemic_workup"},
}

# Who may upload or delete images.
IMAGE_WRITERS = {"radiation_oncologist"}  # restricted roles that may; unrestricted always may

# Roles an account may be switched to by an administrator.
ASSIGNABLE_ROLES = list(ROLE_INFO.keys())

# Roles a consult request can be sent to.
CONSULT_ROLES = ["ocular_oncologist", "radiation_oncologist", "systemic_oncologist"]


def normalize(role: Optional[str]) -> str:
    r = (role or "").strip().lower()
    return ALIASES.get(r, r)


def is_restricted(role: Optional[str]) -> bool:
    return normalize(role) in RESTRICTED_ROLES


def label(role: Optional[str]) -> str:
    r = normalize(role)
    if r in ROLE_INFO:
        return ROLE_INFO[r]["label"]
    return (role or "").replace("_", " ").title()


def can_write_category(role: Optional[str], category: str) -> bool:
    r = normalize(role)
    if r not in RESTRICTED_ROLES:
        return True
    return category in WRITE_CATEGORIES.get(r, set())


def can_write_images(role: Optional[str]) -> bool:
    r = normalize(role)
    return r not in RESTRICTED_ROLES or r in IMAGE_WRITERS


def can_manage_team(role: Optional[str]) -> bool:
    return not is_restricted(role)


def permissions_summary(role: Optional[str], is_admin: bool) -> dict:
    r = normalize(role)
    return {
        "role": "admin" if is_admin else r,
        "label": label("admin" if is_admin else r),
        "is_admin": bool(is_admin),
        "restricted": (not is_admin) and r in RESTRICTED_ROLES,
        "write_categories": sorted(WRITE_CATEGORIES.get(r, [])) if (r in RESTRICTED_ROLES and not is_admin) else None,
        "can_write_images": True if is_admin else can_write_images(r),
        "can_manage_team": True if is_admin else can_manage_team(r),
        "summary": ROLE_INFO.get("admin" if is_admin else r, {}).get("summary", ""),
    }


# ── Request rules (used by one guard that sees every request) ─────────────
_CASE_PATH = re.compile(r"^/cases/([^/]+)(/.*)?$")

# Writes a specialist may make inside a case they are on. Field-level checks
# for /values happen where the field is known (see _enforce_field_write).
_RESTRICTED_WRITES = [
    re.compile(r"^/values(/.*)?$"),
    re.compile(r"^/tasks$"),
    re.compile(r"^/consult-request$"),
    re.compile(r"^/signoff$"),
]
_IMAGE_WRITE = re.compile(r"^/images(/.*)?$")
_TASK_COMPLETE = re.compile(r"^/tasks/[^/]+/complete$")


def parse_case_path(path: str):
    m = _CASE_PATH.match(path)
    if not m:
        return None
    return m.group(1), (m.group(2) or "")


def check_request(role: Optional[str], method: str, path: str,
                  on_team: Callable[[str], bool]) -> Optional[str]:
    """Returns None if allowed, or a plain-language reason it is not."""
    r = normalize(role)
    if r not in RESTRICTED_ROLES:
        return None
    method = method.upper()
    parsed = parse_case_path(path)
    if parsed:
        case_id, rest = parsed
        if not on_team(case_id):
            return ("You're not on this case's care team yet. Ask the ocular "
                    "oncologist to add you.")
        if method in ("GET", "HEAD"):
            return None
        if _IMAGE_WRITE.match(rest):
            if can_write_images(r):
                return None
            return f"Your role ({label(r)}) can view imaging but not upload or delete it."
        if any(p.match(rest) for p in _RESTRICTED_WRITES):
            return None
        return (f"Your role ({label(r)}) can't make this change. "
                "Send a consult request to the ocular oncologist instead.")
    # Not under /cases/<id>
    if method in ("GET", "HEAD"):
        return None
    if method == "PATCH" and _TASK_COMPLETE.match(path):
        return None
    return f"Your role ({label(r)}) can't make this change."


# Reads worth recording in the access log (opening a chart), in addition
# to every write.
_AUDITED_READS = [
    re.compile(r"^$"),                 # GET /cases/<id>
    re.compile(r"^/packet$"),
    re.compile(r"^/patient-journey$"),
    re.compile(r"^/molecular-profile$"),
]


def audit_action(method: str, path: str) -> Optional[str]:
    """Short human label for what to record, or None to skip."""
    parsed = parse_case_path(path)
    if not parsed:
        if method.upper() in ("POST", "PUT", "PATCH", "DELETE") and path not in ("/login", "/signup"):
            return f"{method.upper()} {path}"
        return None
    _, rest = parsed
    m = method.upper()
    if m in ("GET", "HEAD"):
        return "viewed " + (rest.strip("/") or "chart") if any(p.match(rest) for p in _AUDITED_READS) else None
    return f"{m.lower()} {rest.strip('/') or 'case'}"
