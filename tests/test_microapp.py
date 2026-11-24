"""Pruebas unitarias y de integración de extremo a extremo para la microapp."""

import json
import os
import subprocess
import sys
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

import pymupdf

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

from converter import sanitize_filename, validate_genially_url

SAMPLE_URL = "https://view.genially.com/67c88cee24ddcabc9eff7571"


class UnitTests(unittest.TestCase):
    def test_validate_genially_url(self):
        self.assertEqual(
            validate_genially_url("https://view.genially.com/67c88cee24ddcabc9eff7571"),
            "https://view.genially.com/67c88cee24ddcabc9eff7571",
        )
        self.assertEqual(
            validate_genially_url("view.genial.ly/abc123"),
            "https://view.genial.ly/abc123",
        )
        with self.assertRaises(ValueError):
            validate_genially_url("")
        with self.assertRaises(ValueError):
            validate_genially_url("https://example.com/presentation")
        with self.assertRaises(ValueError):
            validate_genially_url("https://view.genially.com/")

    def test_sanitize_filename(self):
        self.assertEqual(
            sanitize_filename("Diseño II:de Clases y de Secuencia | Genially"),
            "Diseno_II_de_Clases_y_de_Secuencia.pdf",
        )
        self.assertEqual(sanitize_filename(" | Genially"), "Presentacion_Genially.pdf")


class IntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.port = 8769
        cls.base_url = f"http://127.0.0.1:{cls.port}"
        env = os.environ.copy()
        env["PORT"] = str(cls.port)
        env["NO_BROWSER"] = "1"
        cls.proc = subprocess.Popen(
            [sys.executable, str(ROOT_DIR / "app.py")],
            cwd=str(ROOT_DIR),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        deadline = time.time() + 15
        while time.time() < deadline:
            try:
                with urllib.request.urlopen(f"{cls.base_url}/api/health", timeout=2) as resp:
                    if resp.status == 200:
                        return
            except Exception:
                time.sleep(0.3)
        cls.tearDownClass()
        raise RuntimeError("El servidor FastAPI no arrancó a tiempo.")

    @classmethod
    def tearDownClass(cls):
        if getattr(cls, "proc", None) and cls.proc.poll() is None:
            cls.proc.terminate()
            try:
                cls.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                cls.proc.kill()

    def test_homepage_and_invalid_url_rejected(self):
        with urllib.request.urlopen(f"{self.base_url}/", timeout=5) as resp:
            self.assertEqual(resp.status, 200)
            html = resp.read().decode("utf-8")
            self.assertIn("Genially a PDF", html)

        req = urllib.request.Request(
            f"{self.base_url}/api/convert",
            data=json.dumps({"url": "https://google.com/foo"}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(req, timeout=5)
        self.assertEqual(ctx.exception.code, 400)

    def test_end_to_end_sample_genially_conversion(self):
        req = urllib.request.Request(
            f"{self.base_url}/api/convert",
            data=json.dumps(
                {"url": SAMPLE_URL, "scale_factor": 2, "searchable_text": True}
            ).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            self.assertEqual(resp.status, 200)
            job = json.loads(resp.read().decode("utf-8"))
        job_id = job["job_id"]

        deadline = time.time() + 150
        final_job = None
        while time.time() < deadline:
            with urllib.request.urlopen(f"{self.base_url}/api/jobs/{job_id}", timeout=5) as resp:
                state = json.loads(resp.read().decode("utf-8"))
            if state["status"] in ("done", "error", "cancelled"):
                final_job = state
                break
            time.sleep(1.5)

        self.assertIsNotNone(final_job, "La conversión excedió el tiempo límite.")
        self.assertEqual(final_job["status"], "done", f"Fallo en conversión: {final_job}")
        res = final_job["result"]
        self.assertEqual(res["page_count"], 22)
        self.assertEqual(res["slide_width"], 1200)
        self.assertEqual(res["slide_height"], 675)

        # Verificar PDF visible (inline) y descargable (attachment)
        with urllib.request.urlopen(f"{self.base_url}{res['pdf_url']}", timeout=10) as resp:
            self.assertEqual(resp.status, 200)
            self.assertIn("application/pdf", resp.headers.get("Content-Type", ""))
            self.assertTrue(resp.headers.get("Content-Disposition", "").startswith("inline;"))
            pdf_bytes = resp.read()

        with urllib.request.urlopen(f"{self.base_url}{res['download_url']}", timeout=10) as resp:
            self.assertEqual(resp.status, 200)
            self.assertTrue(
                resp.headers.get("Content-Disposition", "").startswith("attachment;")
            )

        doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
        self.assertEqual(len(doc), 22)
        self.assertIn("DISEÑO II", doc[0].get_text())
        self.assertIn("Diagrama de Secuencia", doc[4].get_text())
        self.assertGreater(len(doc.get_toc()), 15)
        doc.close()

        # Verificar que aparezca en historial
        with urllib.request.urlopen(f"{self.base_url}/api/history", timeout=5) as resp:
            history = json.loads(resp.read().decode("utf-8"))
        self.assertTrue(any(item["job_id"] == job_id for item in history))


if __name__ == "__main__":
    unittest.main(verbosity=2)
