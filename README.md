# 🏥 ClinicalAI — Stateful Clinical Pipeline

An AI-powered clinical documentation assistant built with a **FastAPI backend**, a **React (Vite) frontend**, and **LangGraph** for stateful multi-agent orchestration. This project corresponds to **Project 03 - Advanced Agent** in the Agentic AI progression.

## 🌟 Overview

The system ingests clinical documents (PDF, TXT, CSV), processes them through a sequential pipeline of AI agents, and drafts a complete medical SOAP note. Crucially, it includes a **Human-in-the-Loop** checkpoint, allowing a physician to review and edit the draft before permanently signing off.

### Core Features
- **Sequential LangGraph Pipeline:** 5-node architecture routing state between extractors, coders, and drafters.
- **Entity Extraction:** Identifies conditions and medications (including drug, dosage, and route).
- **Medical Coding:** Maps conditions to **ICD-10-CM** codes and medications to **RxNorm** codes using fallback maps.
- **Human-in-the-Loop (HITL):** Workflow pauses via LangGraph's `interrupt_after` to await clinician review.
- **Disk Persistence:** `MemorySaver` saves graph state to disk, allowing the workflow to survive server reboots.
- **File Ingestion:** Extracts text from uploaded files to build a comprehensive patient context bundle.
- **Premium UI:** A beautifully designed dark-mode React application featuring a step-wizard flow and glassmorphism UI elements.

---

## 🏗️ Architecture Flow

```text
Document(s) + Text Input
       │
       ▼
condition_extractor   → Extracts medical diagnoses
       │
       ▼
medication_extractor  → Extracts drug, dosage, route
       │
       ▼
condition_coder       → Assigns ICD-10-CM codes
       │
       ▼
medication_coder      → Assigns RxNorm codes
       │
       ▼
soap_drafter          → Drafts S/O/A/P sections
       │
  ───────────────────────────────────────
  ✍️ PHYSICIAN REVIEW GATE (interrupt_after)
  ───────────────────────────────────────
       │
       ▼
finalize_note         → Applies approval and timestamps
       │
       ▼
      END  → Final signed SOAP note
```

---

## 🚀 Getting Started

### Prerequisites
- Docker & Docker Compose
- Or: Python 3.12+ & Node.js 20+

### 🐳 Running via Docker (Recommended)

1. Ensure the `data` directory exists with write permissions:
   ```bash
   mkdir data
   ```
2. Build and run via Docker Compose:
   ```bash
   docker-compose up --build
   ```
3. Access the UI at `http://localhost:8501`

### 💻 Running Locally

#### 1. Backend (FastAPI)
```bash
cd api
python -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate
pip install -r requirements.txt
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

#### 2. Frontend (React)
Open a new terminal:
```bash
cd ui
npm install
npm run dev -- --host 0.0.0.0 --port 8501
```

---

## 🔄 Environment Variables

| Variable | Target | Description | Default |
|----------|--------|-------------|---------|
| `GROQ_API_KEY` | Backend | API Key for LiteLLM/Groq. | *(Optional - falls back to deterministic logic if empty)* |
| `MODEL_NAME` | Backend | The LLM model to use. | `groq/llama-3.1-8b-instant` |
| `DATA_DIR` | Backend | Path to save persistent checkpoints and files. | `./data` |
| `VITE_API_URL` | Frontend | URL of the FastAPI backend. | `http://localhost:8000` |

---

## 🩺 Usage Guide

1. **Patient Intake:** Upload PDF/TXT clinical files or manually type symptoms and current medications.
2. **AI Analysis:** Start the pipeline. The UI will show a live status animation as agents process the bundle.
3. **Physician Review:** Review the extracted findings, assigned medical codes, and the AI-generated SOAP note.
4. **Sign & Approve:** Edit the SOAP draft if necessary, enter your name, and click "Sign & Approve Note".
5. **Signed Note:** View the finalized, immutable clinical report.

---

*Built with ❤️ utilizing FastAPI, React.js, LangChain, and LangGraph.*
