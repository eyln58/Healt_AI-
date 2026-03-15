import csv
import io
import json
import operator
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Any, Dict, List, Literal, Optional, Tuple, TypedDict

import pdfplumber
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, Field, ValidationError

try:
    from litellm import completion
except ImportError:  # pragma: no cover
    completion = None


def resolve_data_dir() -> Path:
    configured = os.getenv("DATA_DIR")
    if configured:
        return Path(configured).expanduser().resolve()
    if Path("/app").exists():
        return Path("/app/data")
    return (Path.cwd() / "data").resolve()


DATA_DIR = resolve_data_dir()
RUNS_DIR = DATA_DIR / "runs"
RUNS_DIR.mkdir(parents=True, exist_ok=True)

MODEL_NAME = os.getenv("MODEL_NAME", "groq/llama-3.1-8b-instant").strip()
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "").strip()
SUPPORTED_EXTENSIONS = {".txt", ".csv", ".pdf"}

ICD10_MAP = {
    "asthma": "J45.909",
    "atrial fibrillation": "I48.91",
    "chronic kidney disease": "N18.9",
    "coronary artery disease": "I25.10",
    "diabetes": "E11.9",
    "heart failure": "I50.9",
    "hyperlipidemia": "E78.5",
    "hypertension": "I10",
    "pneumonia": "J18.9",
    "stroke": "I63.9",
}

RXNORM_MAP = {
    "albuterol": "435",
    "aspirin": "1191",
    "atorvastatin": "83367",
    "furosemide": "4603",
    "insulin": "5856",
    "lisinopril": "29046",
    "metformin": "6809",
    "metoprolol": "6918",
    "warfarin": "11289",
}

CONDITION_SYNONYMS = {
    "a fib": "atrial fibrillation",
    "afib": "atrial fibrillation",
    "cad": "coronary artery disease",
    "ckd": "chronic kidney disease",
    "cva": "stroke",
    "diabetes mellitus": "diabetes",
    "dm": "diabetes",
    "hf": "heart failure",
    "high blood pressure": "hypertension",
    "hld": "hyperlipidemia",
    "htn": "hypertension",
}

KNOWN_CONDITIONS = sorted(ICD10_MAP.keys())
KNOWN_MEDICATIONS = sorted(RXNORM_MAP.keys())
ROUTE_ALIASES = {
    "by mouth": "oral",
    "im": "intramuscular",
    "inhalation": "inhaled",
    "inhaled": "inhaled",
    "intramuscular": "intramuscular",
    "intravenous": "intravenous",
    "iv": "intravenous",
    "nasal": "nasal",
    "neb": "nebulized",
    "nebulized": "nebulized",
    "oral": "oral",
    "po": "oral",
    "sc": "subcutaneous",
    "sq": "subcutaneous",
    "subcutaneous": "subcutaneous",
    "topical": "topical",
}
DOSAGE_PATTERN = re.compile(
    r"\b\d+(?:\.\d+)?\s?(?:mg|mcg|g|ml|units?|puffs?|tablets?|capsules?)\b",
    re.IGNORECASE,
)
ROUTE_PATTERN = re.compile(
    r"\b(?:oral|po|iv|intravenous|im|intramuscular|subcutaneous|sc|sq|inhaled|inhalation|topical|nasal|neb|nebulized|by mouth)\b",
    re.IGNORECASE,
)


class MedicationItem(BaseModel):
    drug: str
    dosage: str = "unknown"
    route: str = "unknown"


class ConditionCode(BaseModel):
    condition: str
    icd10: str


class MedicationCode(BaseModel):
    drug: str
    dosage: str
    route: str
    rxnorm: str


class CodedEntity(BaseModel):
    chunk: str
    entity_type: Literal["condition", "medication"]
    code_system: Literal["ICD-10-CM", "RxNorm"]
    code: str
    display: str


class AuditEvent(BaseModel):
    node: str
    summary: str
    status: Literal["completed", "paused"]
    timestamp: str


class ConditionExtractionPayload(BaseModel):
    conditions: List[str] = Field(default_factory=list)


class MedicationExtractionPayload(BaseModel):
    medications: List[MedicationItem] = Field(default_factory=list)


class SoapSectionsPayload(BaseModel):
    subjective: str
    objective: str
    assessment: str
    plan: str


class ProcessResponse(BaseModel):
    run_id: str
    status: Literal["awaiting_approval"]
    soap_draft: str
    conditions: List[str]
    medications: List[MedicationItem]
    condition_codes: List[ConditionCode]
    medication_codes: List[MedicationCode]
    coded_entities: List[CodedEntity]
    audit_log: List[AuditEvent]
    source_files: List[str]


class UploadResponse(BaseModel):
    run_id: str
    status: Literal["stored"]
    stored_files: List[str]


class ProcessStorageRequest(BaseModel):
    run_id: str
    text: str = ""


class ReviewRequest(BaseModel):
    run_id: str
    edited_soap: str = Field(min_length=1)
    approve: bool
    reviewer_name: str = "Clinician"
    review_notes: str = ""


class StatusResponse(BaseModel):
    run_id: str
    status: Literal["stored", "awaiting_approval", "completed"]
    soap_draft: Optional[str] = None
    final_note: Optional[str] = None
    conditions: List[str] = Field(default_factory=list)
    medications: List[MedicationItem] = Field(default_factory=list)
    condition_codes: List[ConditionCode] = Field(default_factory=list)
    medication_codes: List[MedicationCode] = Field(default_factory=list)
    coded_entities: List[CodedEntity] = Field(default_factory=list)
    audit_log: List[AuditEvent] = Field(default_factory=list)
    source_files: List[str] = Field(default_factory=list)
    reviewer_name: str = ""
    review_notes: str = ""


class HealthResponse(BaseModel):
    status: Literal["ok"]
    llm_enabled: bool
    model_name: str
    data_dir: str


class AgentState(TypedDict, total=False):
    run_id: str
    source_text: str
    source_files: List[str]
    conditions: List[str]
    medications: List[Dict[str, str]]
    condition_codes: List[Dict[str, str]]
    medication_codes: List[Dict[str, str]]
    coded_entities: Annotated[List[Dict[str, str]], operator.add]
    audit_log: Annotated[List[Dict[str, str]], operator.add]
    soap_draft: str
    final_note: str
    reviewer_name: str
    review_notes: str


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def make_event(node: str, summary: str, status: Literal["completed", "paused"] = "completed") -> list[dict[str, str]]:
    return [{
        "node": node,
        "summary": summary,
        "status": status,
        "timestamp": utc_now(),
    }]


def merge_audit_log(existing: list[dict[str, Any]], events: list[dict[str, str]]) -> list[dict[str, Any]]:
    return [*existing, *events]


def review_event(reviewer_name: str, approved: bool) -> list[dict[str, str]]:
    reviewer = reviewer_name.strip() or "Clinician"
    if approved:
        summary = f"{reviewer} approved the SOAP draft for final sign-off."
        return make_event("clinician_review", summary)
    summary = f"{reviewer} saved edits and kept the workflow paused for review."
    return make_event("clinician_review", summary, status="paused")


def normalize_condition(value: str) -> str:
    cleaned = " ".join(value.lower().strip().split())
    if not cleaned:
        return ""
    return CONDITION_SYNONYMS.get(cleaned, cleaned)


def normalize_route(value: str) -> str:
    cleaned = " ".join(value.lower().strip().split())
    if not cleaned:
        return "unknown"
    return ROUTE_ALIASES.get(cleaned, cleaned)


def normalize_dosage(value: str) -> str:
    cleaned = " ".join(value.strip().split())
    return cleaned if cleaned else "unknown"


def dedupe_preserve_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value and value not in seen:
            seen.add(value)
            result.append(value)
    return result


def serialize_model_list(items: List[Any]) -> List[Dict[str, Any]]:
    output: List[Dict[str, Any]] = []
    for item in items:
        if isinstance(item, BaseModel):
            output.append(item.model_dump())
        else:
            output.append(dict(item))
    return output


def safe_filename(filename: str) -> str:
    base = Path(filename or "document.txt").name
    sanitized = re.sub(r"[^A-Za-z0-9._-]+", "_", base).strip("._")
    return sanitized or "document.txt"


def ensure_run_dir(run_id: str) -> Path:
    run_dir = RUNS_DIR / run_id
    (run_dir / "uploads").mkdir(parents=True, exist_ok=True)
    return run_dir


def record_path(run_id: str) -> Path:
    return RUNS_DIR / run_id / "record.json"


def load_record(run_id: str) -> Optional[dict[str, Any]]:
    path = record_path(run_id)
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def save_record(run_id: str, payload: dict[str, Any]) -> None:
    path = record_path(run_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def extract_text_from_bytes(filename: str, content: bytes) -> str:
    suffix = Path(filename.lower()).suffix
    if suffix not in SUPPORTED_EXTENSIONS:
        raise HTTPException(status_code=400, detail=f"Unsupported file type: {suffix or filename}")
    if suffix == ".txt":
        return content.decode("utf-8", errors="ignore")
    if suffix == ".csv":
        buffer = io.StringIO(content.decode("utf-8", errors="ignore"))
        return "\n".join(" ".join(row) for row in csv.reader(buffer))
    text: list[str] = []
    with pdfplumber.open(io.BytesIO(content)) as pdf:
        for page in pdf.pages:
            text.append(page.extract_text() or "")
    return "\n".join(text)


async def store_uploads(run_id: str, files: List[UploadFile]) -> Tuple[List[str], str]:
    upload_dir = ensure_run_dir(run_id) / "uploads"
    saved_files: List[str] = []
    extracted_chunks: List[str] = []
    for file in files:
        safe_name = safe_filename(file.filename or "document.txt")
        raw = await file.read()
        (upload_dir / safe_name).write_bytes(raw)
        saved_files.append(safe_name)
        extracted_chunks.append(extract_text_from_bytes(safe_name, raw))
    return saved_files, "\n\n".join(chunk for chunk in extracted_chunks if chunk.strip())


def read_stored_files(run_id: str) -> Tuple[List[str], str]:
    upload_dir = ensure_run_dir(run_id) / "uploads"
    if not upload_dir.exists():
        return [], ""
    names: List[str] = []
    chunks: List[str] = []
    for path in sorted(upload_dir.iterdir()):
        if path.is_file():
            names.append(path.name)
            chunks.append(extract_text_from_bytes(path.name, path.read_bytes()))
    return names, "\n\n".join(chunk for chunk in chunks if chunk.strip())


def llm_ready() -> bool:
    return bool(GROQ_API_KEY and completion is not None)


def invoke_json_model(
    system_prompt: str,
    user_prompt: str,
    schema: type[BaseModel],
    *,
    temperature: float = 0.0,
    max_tokens: int = 1200,
) -> Optional[BaseModel]:
    if not llm_ready():
        return None
    try:
        response = completion(
            model=MODEL_NAME,
            api_key=GROQ_API_KEY,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=temperature,
            response_format={"type": "json_object"},
            max_tokens=max_tokens,
            timeout=45,
        )
        content = response.choices[0].message.content or "{}"
        return schema.model_validate(json.loads(content))
    except Exception:
        return None


def fallback_extract_conditions(text: str) -> List[str]:
    normalized = text.lower()
    found: List[str] = []
    for phrase, canonical in CONDITION_SYNONYMS.items():
        if phrase in normalized:
            found.append(canonical)
    for condition in KNOWN_CONDITIONS:
        if condition in normalized:
            found.append(condition)
    return dedupe_preserve_order(found)


def fallback_extract_medications(text: str) -> List[MedicationItem]:
    lowered = text.lower()
    medications: List[MedicationItem] = []
    for med in KNOWN_MEDICATIONS:
        for match in re.finditer(rf"\b{re.escape(med)}\b", lowered):
            window = text[match.start(): min(len(text), match.start() + 120)]
            dosage_match = DOSAGE_PATTERN.search(window)
            route_match = ROUTE_PATTERN.search(window)
            medications.append(
                MedicationItem(
                    drug=med,
                    dosage=normalize_dosage(dosage_match.group(0) if dosage_match else "unknown"),
                    route=normalize_route(route_match.group(0) if route_match else "unknown"),
                )
            )
            break
    deduped: List[MedicationItem] = []
    seen = set()
    for med in medications:
        key = (med.drug, med.dosage, med.route)
        if key not in seen:
            seen.add(key)
            deduped.append(med)
    return deduped


def llm_extract_conditions(text: str) -> List[str]:
    system_prompt = (
        "Extract only explicit patient conditions mentioned in the clinical text. "
        "Return JSON with a single key `conditions`, which must be an array of short lowercase strings. "
        "Do not infer unstated diagnoses."
    )
    result = invoke_json_model(system_prompt, text[:12000], ConditionExtractionPayload, temperature=0.0)
    if not result:
        return fallback_extract_conditions(text)
    normalized = [normalize_condition(value) for value in result.conditions]
    normalized = [value for value in normalized if value]
    return dedupe_preserve_order(normalized)


def llm_extract_medications(text: str) -> List[MedicationItem]:
    system_prompt = (
        "Extract medications from the clinical text. Return JSON with key `medications`, "
        "an array of objects containing exactly `drug`, `dosage`, and `route`. "
        "Only include drug, dose, and route. Exclude frequency, duration, and commentary. "
        "Use `unknown` when dosage or route is not explicit."
    )
    result = invoke_json_model(system_prompt, text[:12000], MedicationExtractionPayload, temperature=0.0)
    if not result:
        return fallback_extract_medications(text)
    cleaned: List[MedicationItem] = []
    for med in result.medications:
        drug = med.drug.lower().strip()
        if not drug:
            continue
        cleaned.append(
            MedicationItem(
                drug=drug,
                dosage=normalize_dosage(med.dosage),
                route=normalize_route(med.route),
            )
        )
    if not cleaned:
        return fallback_extract_medications(text)
    deduped: List[MedicationItem] = []
    seen = set()
    for med in cleaned:
        key = (med.drug, med.dosage, med.route)
        if key not in seen:
            seen.add(key)
            deduped.append(med)
    return deduped


def build_fallback_soap(
    source_text: str,
    condition_codes: List[ConditionCode],
    medication_codes: List[MedicationCode],
) -> str:
    source_line = next((line.strip() for line in source_text.splitlines() if line.strip()), "Patient presents for clinical review.")
    objective_parts: list[str] = []
    if condition_codes:
        objective_parts.append(
            "Conditions: " + "; ".join(f"{item.condition} (ICD-10-CM {item.icd10})" for item in condition_codes)
        )
    if medication_codes:
        objective_parts.append(
            "Medications: " + "; ".join(
                f"{item.drug} {item.dosage} {item.route} (RxNorm {item.rxnorm})" for item in medication_codes
            )
        )
    if not objective_parts:
        objective_parts.append("No structured conditions or medications extracted from the source.")
    return "\n".join([
        f"S: {source_line[:220]}",
        f"O: {' '.join(objective_parts)}",
        "A: Clinical documentation draft prepared from the supplied materials. Clinician review is required before any care decision.",
        "P: Review extracted entities, confirm terminology codes, and update the care plan after clinician approval.",
    ])


def llm_draft_soap(
    source_text: str,
    condition_codes: List[ConditionCode],
    medication_codes: List[MedicationCode],
) -> str:
    user_prompt = json.dumps(
        {
            "source_text": source_text[:12000],
            "condition_codes": serialize_model_list(condition_codes),
            "medication_codes": serialize_model_list(medication_codes),
            "requirements": [
                "Return four strings: subjective, objective, assessment, plan.",
                "Use concise professional clinical language.",
                "Do not invent facts not present in the source or coded outputs.",
                "Mention that clinician review is required when information is incomplete.",
            ],
        },
        ensure_ascii=True,
    )
    system_prompt = (
        "Create a SOAP note draft as structured JSON with keys subjective, objective, assessment, and plan. "
        "Do not include markdown, bullets, or extra keys."
    )
    result = invoke_json_model(system_prompt, user_prompt, SoapSectionsPayload, temperature=0.2, max_tokens=900)
    if not result:
        return build_fallback_soap(source_text, condition_codes, medication_codes)
    sections = {
        "S": result.subjective.strip(),
        "O": result.objective.strip(),
        "A": result.assessment.strip(),
        "P": result.plan.strip(),
    }
    if not all(sections.values()):
        return build_fallback_soap(source_text, condition_codes, medication_codes)
    return "\n".join(f"{label}: {content}" for label, content in sections.items())


def condition_extractor(state: AgentState) -> AgentState:
    conditions = llm_extract_conditions(state.get("source_text", ""))
    return {
        "conditions": conditions,
        "audit_log": make_event("condition_extractor", f"Extracted {len(conditions)} condition(s)."),
    }


def medication_extractor(state: AgentState) -> AgentState:
    medications = llm_extract_medications(state.get("source_text", ""))
    return {
        "medications": serialize_model_list(medications),
        "audit_log": make_event("medication_extractor", f"Extracted {len(medications)} medication(s)."),
    }


def condition_coder(state: AgentState) -> AgentState:
    codes: List[ConditionCode] = []
    coded_entities: List[CodedEntity] = []
    for condition in state.get("conditions", []):
        code = ICD10_MAP.get(normalize_condition(condition), "R69")
        codes.append(ConditionCode(condition=condition, icd10=code))
        coded_entities.append(
            CodedEntity(
                chunk=condition,
                entity_type="condition",
                code_system="ICD-10-CM",
                code=code,
                display=condition.title(),
            )
        )
    return {
        "condition_codes": serialize_model_list(codes),
        "coded_entities": serialize_model_list(coded_entities),
        "audit_log": make_event("condition_coder", f"Assigned ICD-10-CM codes to {len(codes)} condition(s)."),
    }


def medication_coder(state: AgentState) -> AgentState:
    codes: List[MedicationCode] = []
    coded_entities: List[CodedEntity] = []
    for item in state.get("medications", []):
        medication = MedicationItem.model_validate(item)
        rxnorm = RXNORM_MAP.get(medication.drug, "0")
        code = MedicationCode(
            drug=medication.drug,
            dosage=normalize_dosage(medication.dosage),
            route=normalize_route(medication.route),
            rxnorm=rxnorm,
        )
        chunk = f"{code.drug} | {code.dosage} | {code.route}"
        codes.append(code)
        coded_entities.append(
            CodedEntity(
                chunk=chunk,
                entity_type="medication",
                code_system="RxNorm",
                code=code.rxnorm,
                display=code.drug.title(),
            )
        )
    return {
        "medication_codes": serialize_model_list(codes),
        "coded_entities": serialize_model_list(coded_entities),
        "audit_log": make_event("medication_coder", f"Assigned RxNorm codes to {len(codes)} medication(s)."),
    }


def soap_drafter(state: AgentState) -> AgentState:
    condition_codes = [ConditionCode.model_validate(item) for item in state.get("condition_codes", [])]
    medication_codes = [MedicationCode.model_validate(item) for item in state.get("medication_codes", [])]
    soap_draft = llm_draft_soap(state.get("source_text", ""), condition_codes, medication_codes)
    return {
        "soap_draft": soap_draft,
        "audit_log": make_event(
            "soap_drafter",
            "SOAP draft created and workflow paused for human review.",
            status="paused",
        ),
    }


def finalize_note(state: AgentState) -> AgentState:
    reviewer = (state.get("reviewer_name") or "Clinician").strip() or "Clinician"
    review_notes = (state.get("review_notes") or "").strip()
    signed_note = [
        state.get("soap_draft", "").strip(),
        "",
        "Sign-off",
        f"Approved by: {reviewer}",
        f"Approved at: {utc_now()}",
    ]
    if review_notes:
        signed_note.append(f"Review notes: {review_notes}")
    return {
        "final_note": "\n".join(line for line in signed_note if line is not None),
        "audit_log": make_event("finalize_note", f"Final SOAP note signed by {reviewer}."),
    }


graph_builder = StateGraph(AgentState)
graph_builder.add_node("condition_extractor", condition_extractor)
graph_builder.add_node("medication_extractor", medication_extractor)
graph_builder.add_node("condition_coder", condition_coder)
graph_builder.add_node("medication_coder", medication_coder)
graph_builder.add_node("soap_drafter", soap_drafter)
graph_builder.add_node("finalize_note", finalize_note)
graph_builder.add_edge(START, "condition_extractor")
graph_builder.add_edge("condition_extractor", "medication_extractor")
graph_builder.add_edge("medication_extractor", "condition_coder")
graph_builder.add_edge("condition_coder", "medication_coder")
graph_builder.add_edge("medication_coder", "soap_drafter")
graph_builder.add_edge("soap_drafter", "finalize_note")
graph_builder.add_edge("finalize_note", END)
GRAPH = graph_builder.compile(checkpointer=MemorySaver(), interrupt_after=["soap_drafter"])


def thread_config(run_id: str) -> dict[str, Any]:
    return {"configurable": {"thread_id": run_id}}


def checkpoint_values(run_id: str) -> Optional[dict[str, Any]]:
    try:
        snapshot = GRAPH.get_state(thread_config(run_id))
    except Exception:
        return None
    if not snapshot or not snapshot.values:
        return None
    return dict(snapshot.values)


def build_initial_state(run_id: str, source_text: str, source_files: List[str]) -> AgentState:
    return {
        "run_id": run_id,
        "source_text": source_text,
        "source_files": source_files,
        "conditions": [],
        "medications": [],
        "condition_codes": [],
        "medication_codes": [],
        "coded_entities": [],
        "audit_log": [],
        "soap_draft": "",
        "final_note": "",
        "reviewer_name": "",
        "review_notes": "",
    }


def normalize_status_from_values(
    values: dict[str, Any],
    fallback: Literal["stored", "awaiting_approval", "completed"],
) -> Literal["stored", "awaiting_approval", "completed"]:
    if values.get("final_note"):
        return "completed"
    if values.get("soap_draft"):
        return "awaiting_approval"
    return fallback


def record_from_values(
    run_id: str,
    values: Dict[str, Any],
    fallback_status: Literal["stored", "awaiting_approval", "completed"] = "awaiting_approval",
) -> Dict[str, Any]:
    return {
        "run_id": run_id,
        "status": normalize_status_from_values(values, fallback_status),
        "source_text": values.get("source_text", ""),
        "source_files": list(values.get("source_files", [])),
        "conditions": list(values.get("conditions", [])),
        "medications": list(values.get("medications", [])),
        "condition_codes": list(values.get("condition_codes", [])),
        "medication_codes": list(values.get("medication_codes", [])),
        "coded_entities": list(values.get("coded_entities", [])),
        "audit_log": list(values.get("audit_log", [])),
        "soap_draft": values.get("soap_draft") or None,
        "final_note": values.get("final_note") or None,
        "reviewer_name": values.get("reviewer_name", ""),
        "review_notes": values.get("review_notes", ""),
        "updated_at": utc_now(),
    }


def save_checkpoint_record(
    run_id: str,
    fallback_status: Literal["stored", "awaiting_approval", "completed"] = "awaiting_approval",
) -> dict[str, Any]:
    values = checkpoint_values(run_id)
    if values is None:
        raise HTTPException(status_code=404, detail="Workflow state not found")
    payload = record_from_values(run_id, values, fallback_status)
    save_record(run_id, payload)
    return payload


def rehydrate_checkpoint(run_id: str, record: dict[str, Any]) -> None:
    if checkpoint_values(run_id):
        return
    if record.get("status") != "awaiting_approval":
        raise HTTPException(status_code=400, detail="Run is not ready to resume")
    initial_state = build_initial_state(run_id, record.get("source_text", ""), list(record.get("source_files", [])))
    GRAPH.invoke(initial_state, config=thread_config(run_id))
    restore_values = {
        "conditions": record.get("conditions", []),
        "medications": record.get("medications", []),
        "condition_codes": record.get("condition_codes", []),
        "medication_codes": record.get("medication_codes", []),
        "coded_entities": record.get("coded_entities", []),
        "audit_log": record.get("audit_log", []),
        "soap_draft": record.get("soap_draft") or "",
        "reviewer_name": record.get("reviewer_name", ""),
        "review_notes": record.get("review_notes", ""),
    }
    GRAPH.update_state(thread_config(run_id), restore_values, as_node="soap_drafter")


def save_review_without_checkpoint(record: dict[str, Any], review: ReviewRequest) -> dict[str, Any]:
    reviewer_name = review.reviewer_name.strip() or "Clinician"
    updated = dict(record)
    updated["soap_draft"] = review.edited_soap.strip()
    updated["reviewer_name"] = reviewer_name
    updated["review_notes"] = review.review_notes.strip()
    updated["audit_log"] = merge_audit_log(updated.get("audit_log", []), review_event(reviewer_name, review.approve))
    updated["updated_at"] = utc_now()

    if not review.approve:
        updated["status"] = "awaiting_approval"
        save_record(review.run_id, updated)
        return updated

    final_payload = finalize_note(
        {
            "run_id": review.run_id,
            "source_text": updated.get("source_text", ""),
            "source_files": list(updated.get("source_files", [])),
            "conditions": list(updated.get("conditions", [])),
            "medications": list(updated.get("medications", [])),
            "condition_codes": list(updated.get("condition_codes", [])),
            "medication_codes": list(updated.get("medication_codes", [])),
            "coded_entities": list(updated.get("coded_entities", [])),
            "audit_log": list(updated.get("audit_log", [])),
            "soap_draft": updated["soap_draft"],
            "final_note": updated.get("final_note") or "",
            "reviewer_name": reviewer_name,
            "review_notes": updated["review_notes"],
        }
    )
    updated["final_note"] = final_payload["final_note"]
    updated["audit_log"] = merge_audit_log(updated["audit_log"], final_payload["audit_log"])
    updated["status"] = "completed"
    updated["updated_at"] = utc_now()
    save_record(review.run_id, updated)
    return updated


def to_status_response(payload: dict[str, Any]) -> StatusResponse:
    try:
        return StatusResponse.model_validate(payload)
    except ValidationError as exc:
        raise HTTPException(status_code=500, detail=f"Stored state validation failed: {exc}") from exc


def to_process_response(payload: dict[str, Any]) -> ProcessResponse:
    try:
        return ProcessResponse.model_validate(payload)
    except ValidationError as exc:
        raise HTTPException(status_code=500, detail=f"Process response validation failed: {exc}") from exc


app = FastAPI(title="Project 03 Advanced Agent API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://localhost:5173",
        "http://localhost:8501",
        "http://127.0.0.1:3000",
        "http://127.0.0.1:5173",
        "http://127.0.0.1:8501",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(
        status="ok",
        llm_enabled=llm_ready(),
        model_name=MODEL_NAME,
        data_dir=str(DATA_DIR),
    )


@app.post("/upload", response_model=UploadResponse)
async def upload_documents(files: List[UploadFile] = File(...)) -> UploadResponse:
    if not files:
        raise HTTPException(status_code=400, detail="Upload at least one file.")
    run_id = str(uuid.uuid4())
    stored_files, _ = await store_uploads(run_id, files)
    save_record(
        run_id,
        {
            "run_id": run_id,
            "status": "stored",
            "source_text": "",
            "source_files": stored_files,
            "conditions": [],
            "medications": [],
            "condition_codes": [],
            "medication_codes": [],
            "coded_entities": [],
            "audit_log": [],
            "soap_draft": None,
            "final_note": None,
            "reviewer_name": "",
            "review_notes": "",
            "updated_at": utc_now(),
        },
    )
    return UploadResponse(run_id=run_id, status="stored", stored_files=stored_files)


@app.post("/process", response_model=ProcessResponse)
async def process_case(
    text: Optional[str] = Form(None),
    files: Optional[List[UploadFile]] = File(None),
) -> ProcessResponse:
    supplied_text = (text or "").strip()
    uploaded_files = files or []
    if not supplied_text and not uploaded_files:
        raise HTTPException(status_code=400, detail="Provide text or file upload.")

    run_id = str(uuid.uuid4())
    stored_files: List[str] = []
    stored_text = ""
    if uploaded_files:
        stored_files, stored_text = await store_uploads(run_id, uploaded_files)

    source_text = "\n\n".join(part for part in [supplied_text, stored_text] if part).strip()
    GRAPH.invoke(build_initial_state(run_id, source_text, stored_files), config=thread_config(run_id))
    payload = save_checkpoint_record(run_id, "awaiting_approval")
    return to_process_response(payload)


@app.post("/process-storage", response_model=ProcessResponse)
def process_storage(request: ProcessStorageRequest) -> ProcessResponse:
    record = load_record(request.run_id)
    if not record:
        raise HTTPException(status_code=404, detail="Run ID not found")
    if record.get("status") != "stored":
        raise HTTPException(status_code=400, detail="Only stored uploads can be processed with this endpoint.")

    source_files, stored_text = read_stored_files(request.run_id)
    source_text = "\n\n".join(part for part in [request.text.strip(), stored_text] if part).strip()
    if not source_text:
        raise HTTPException(status_code=400, detail="No stored content found for processing.")

    GRAPH.invoke(
        build_initial_state(request.run_id, source_text, source_files),
        config=thread_config(request.run_id),
    )
    payload = save_checkpoint_record(request.run_id, "awaiting_approval")
    return to_process_response(payload)


@app.post("/review", response_model=StatusResponse)
def review_case(review: ReviewRequest) -> StatusResponse:
    record = load_record(review.run_id)
    if not record:
        raise HTTPException(status_code=404, detail="Run ID not found")
    if record.get("status") != "awaiting_approval":
        raise HTTPException(status_code=400, detail="Run is not awaiting approval.")

    if checkpoint_values(review.run_id) is None:
        payload = save_review_without_checkpoint(record, review)
        return to_status_response(payload)

    GRAPH.update_state(
        thread_config(review.run_id),
        {
            "soap_draft": review.edited_soap.strip(),
            "reviewer_name": review.reviewer_name.strip() or "Clinician",
            "review_notes": review.review_notes.strip(),
            "audit_log": review_event(review.reviewer_name, review.approve),
        },
        as_node="soap_drafter",
    )

    if not review.approve:
        payload = save_checkpoint_record(review.run_id, "awaiting_approval")
        return to_status_response(payload)

    GRAPH.invoke(None, config=thread_config(review.run_id))
    payload = save_checkpoint_record(review.run_id, "completed")
    return to_status_response(payload)


@app.get("/status/{run_id}", response_model=StatusResponse)
def get_status(run_id: str) -> StatusResponse:
    payload = checkpoint_values(run_id)
    if payload is not None:
        stored = save_checkpoint_record(run_id, "awaiting_approval")
        return to_status_response(stored)

    record = load_record(run_id)
    if not record:
        raise HTTPException(status_code=404, detail="Run ID not found")
    return to_status_response(record)
