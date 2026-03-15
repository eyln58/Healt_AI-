from __future__ import annotations

import asyncio
import importlib.util
import io
import os
import tempfile
import unittest
import uuid
from pathlib import Path

from starlette.datastructures import UploadFile


API_MAIN = Path(__file__).resolve().parents[1] / "api" / "main.py"


def load_api_module(data_dir: str):
    os.environ["DATA_DIR"] = data_dir
    os.environ.pop("GROQ_API_KEY", None)
    module_name = f"project03_api_{uuid.uuid4().hex}"
    spec = importlib.util.spec_from_file_location(module_name, API_MAIN)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def make_upload(filename: str, content: str) -> UploadFile:
    return UploadFile(file=io.BytesIO(content.encode("utf-8")), filename=filename)


class Project03ApiTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.api = load_api_module(self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_process_runs_all_nodes_then_pauses_for_review(self):
        payload = asyncio.run(
            self.api.process_case(
                text=(
                    "Patient has HTN and diabetes mellitus. "
                    "Current medications include metformin 500 mg oral and aspirin 81 mg oral."
                ),
                files=None,
            )
        ).model_dump()

        self.assertEqual(payload["status"], "awaiting_approval")
        self.assertIn("hypertension", payload["conditions"])
        self.assertIn("diabetes", payload["conditions"])
        self.assertEqual([entry["node"] for entry in payload["audit_log"]], [
            "condition_extractor",
            "medication_extractor",
            "condition_coder",
            "medication_coder",
            "soap_drafter",
        ])
        self.assertEqual(payload["audit_log"][-1]["status"], "paused")
        self.assertTrue(payload["soap_draft"].startswith("S:"))

    def test_review_keeps_edits_and_finalizes(self):
        initial = asyncio.run(
            self.api.process_case(text="Stroke history. Lisinopril 10 mg oral.", files=None)
        ).model_dump()
        edited = initial["soap_draft"] + "\nP: Follow-up in cardiology clinic."

        payload = self.api.review_case(
            self.api.ReviewRequest(
                run_id=initial["run_id"],
                edited_soap=edited,
                approve=True,
                reviewer_name="Dr. Kaya",
                review_notes="Reviewed and approved.",
            )
        ).model_dump()

        self.assertEqual(payload["status"], "completed")
        self.assertIn("Dr. Kaya", payload["final_note"])
        self.assertIn("Follow-up in cardiology clinic.", payload["final_note"])
        self.assertEqual(payload["audit_log"][-2]["node"], "clinician_review")
        self.assertEqual(payload["audit_log"][-1]["node"], "finalize_note")

    def test_review_can_resume_from_persisted_disk_record_after_reload(self):
        initial = asyncio.run(
            self.api.process_case(text="Asthma treated with albuterol 2 puffs inhaled.", files=None)
        ).model_dump()
        run_id = initial["run_id"]

        reloaded_api = load_api_module(self.temp_dir.name)
        payload = reloaded_api.review_case(
            reloaded_api.ReviewRequest(
                run_id=run_id,
                edited_soap=initial["soap_draft"] + "\nA: Stable respiratory status.",
                approve=True,
                reviewer_name="Dr. Yilmaz",
                review_notes="Approved after restart.",
            )
        ).model_dump()

        self.assertEqual(payload["status"], "completed")
        self.assertIn("Stable respiratory status.", payload["final_note"])
        self.assertIn("Approved after restart.", payload["final_note"])

    def test_upload_then_process_storage_merges_multiple_files(self):
        upload_payload = asyncio.run(
            self.api.upload_documents(
                files=[
                    make_upload("conditions.txt", "Hypertension and CKD history"),
                    make_upload("meds.txt", "Metformin 500 mg oral"),
                ]
            )
        ).model_dump()

        self.assertEqual(upload_payload["status"], "stored")
        self.assertEqual(len(upload_payload["stored_files"]), 2)

        payload = self.api.process_storage(
            self.api.ProcessStorageRequest(
                run_id=upload_payload["run_id"],
                text="Recent stroke symptoms documented in clinic note.",
            )
        ).model_dump()

        self.assertEqual(payload["status"], "awaiting_approval")
        self.assertCountEqual(payload["source_files"], ["conditions.txt", "meds.txt"])
        self.assertIn("hypertension", payload["conditions"])
        self.assertIn("chronic kidney disease", payload["conditions"])
        self.assertIn("stroke", payload["conditions"])
        self.assertTrue(any(item["drug"] == "metformin" for item in payload["medications"]))


if __name__ == "__main__":
    unittest.main()
