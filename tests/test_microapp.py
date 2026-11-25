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
from interactive import (
    InteractiveCluster,
    InteractiveNote,
    SlideContextRecord,
    build_ai_markdown_context,
    extract_from_html,
    extract_slide_interactions,
)

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
        self.assertEqual(
            sanitize_filename("Clase UML | Genially", "_contexto_IA.md"),
            "Clase_UML_contexto_IA.md",
        )

    def test_extract_from_html_and_clusters(self):
        raw = '<p><strong>Hola</strong> mundo</p><ul><li>Uno</li><li>Dos</li></ul><img src="https://img.test/a.png"/><a href="https://example.org/doc">Doc</a>'
        txt, imgs, links = extract_from_html(raw)
        self.assertIn("Hola mundo", txt)
        self.assertIn("- Uno", txt)
        self.assertEqual(imgs, ["https://img.test/a.png"])
        self.assertEqual(links, ["https://example.org/doc"])

        payload = {
            "Slides": [{"Id": "s1", "Order": 0}],
            "interactivityActions": {
                "a1": {"type": "htmlTooltip", "html": "<p>Definición A</p>"},
                "a2": {"type": "htmlTooltip", "html": "<p>Definición B</p>"},
                "a3": {"type": "showElements"},
                "a4": {"type": "goToSlide", "target": {"smartLink": "nextPage"}},
            },
            "Images": [
                {
                    "Id": "i1",
                    "IdSlide": "s1",
                    "Name": "Imagen",
                    "Position": {"PositionLeft": "100px", "PositionTop": "200px"},
                    "Size": {"Width": "60px", "Height": "60px"},
                    "interactivities": {"hover": "a1"},
                }
            ],
            "Texts": [
                {
                    "Id": "t1",
                    "IdSlide": "s1",
                    "TextMessage": "+Info",
                    "Position": {"PositionLeft": "100px", "PositionTop": "220px"},
                    "Size": {"Width": "60px", "Height": "20px"},
                    "interactivities": {"hover": "a2"},
                },
                {
                    "Id": "t2",
                    "IdSlide": "s1",
                    "TextMessage": "Revelar",
                    "Position": {"PositionLeft": "300px", "PositionTop": "220px"},
                    "Size": {"Width": "80px", "Height": "30px"},
                    "interactivities": {"click": "a3"},
                },
                {
                    "Id": "t3",
                    "IdSlide": "s1",
                    "TextMessage": "Siguiente",
                    "Position": {"PositionLeft": "400px", "PositionTop": "220px"},
                    "Size": {"Width": "80px", "Height": "30px"},
                    "interactivities": {"click": "a4"},
                },
            ],
        }
        clusters, reveals = extract_slide_interactions(payload, "s1", 1200, 675)
        self.assertEqual(len(clusters), 1)
        self.assertEqual(len(clusters[0].notes), 2)
        self.assertEqual(len(reveals), 1)

        md = build_ai_markdown_context(
            "Clase Test",
            SAMPLE_URL,
            3,
            1200,
            675,
            [
                SlideContextRecord(
                    slide_number=1,
                    slide_uuid="s1",
                    slide_title="Intro",
                    pdf_page_number=1,
                    visible_text="Texto base",
                    image_count=2,
                    revealed_texts=["Texto revelado"],
                    revealed_pdf_pages=[2],
                    clusters=[
                        InteractiveCluster(
                            number=1,
                            x=100,
                            y=200,
                            width=60,
                            height=60,
                            label="+Info",
                            notes=[
                                InteractiveNote(
                                    action_id="a1",
                                    trigger="hover",
                                    kind="tooltip",
                                    kind_label="Nota / Tooltip",
                                    item_label="+Info",
                                    html_body="<p>Definición A</p>",
                                    plain_text="Definición A",
                                )
                            ],
                        )
                    ],
                    detail_pdf_pages=[3],
                )
            ],
        )
        self.assertIn("Clase Test", md)
        self.assertIn("Definición A", md)
        self.assertIn("Texto revelado", md)


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
            self.assertIn("Incluir contenido interactivo", html)

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
                {
                    "url": SAMPLE_URL,
                    "scale_factor": 2,
                    "searchable_text": True,
                    "include_interactive": True,
                }
            ).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            self.assertEqual(resp.status, 200)
            job = json.loads(resp.read().decode("utf-8"))
        job_id = job["job_id"]

        deadline = time.time() + 180
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
        self.assertEqual(res["slide_count"], 22)
        self.assertGreater(res["page_count"], 22)
        self.assertGreater(res["interactive_count"], 10)
        self.assertEqual(res["revealed_count"], 1)
        self.assertEqual(res["slide_width"], 1200)
        self.assertEqual(res["slide_height"], 675)

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

        with urllib.request.urlopen(f"{self.base_url}{res['markdown_url']}", timeout=10) as resp:
            self.assertEqual(resp.status, 200)
            md_text = resp.read().decode("utf-8")
            self.assertIn("Diapositiva 5", md_text)
            self.assertIn("LIFELINE", md_text)
            self.assertIn("Esto es diseñar !", md_text)

        with urllib.request.urlopen(f"{self.base_url}{res['markdown_download_url']}", timeout=10) as resp:
            self.assertEqual(resp.status, 200)
            self.assertTrue(resp.headers.get("Content-Disposition", "").startswith("attachment;"))

        doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
        self.assertEqual(len(doc), res["page_count"])
        full_pdf_text = "\n".join(page.get_text() for page in doc)
        self.assertIn("DISEÑO II", full_pdf_text)
        self.assertIn("LIFELINE", full_pdf_text)
        self.assertIn("Esto es diseñar !", full_pdf_text)
        self.assertGreater(len(doc.get_toc()), 22)
        doc.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
