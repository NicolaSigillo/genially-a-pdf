"""Servidor web local para la microapp Genially a PDF."""

from __future__ import annotations

import asyncio
import json
import os
import socket
import sys
import threading
import time
import urllib.request
import uuid
import webbrowser
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Optional
from urllib.parse import quote

import uvicorn
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse, Response, StreamingResponse
from pydantic import BaseModel

from converter import ConversionProgress, ConversionResult, convert_genially_to_pdf, validate_genially_url

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

BASE_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = BASE_DIR / "output"
INDEX_FILE = OUTPUT_DIR / "history.json"
STATIC_DIR = BASE_DIR / "static"

OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="Genially a PDF", version="1.1.0")


class ConvertRequest(BaseModel):
    url: str
    scale_factor: int = 2
    searchable_text: bool = True
    include_interactive: bool = True


@dataclass
class JobState:
    job_id: str
    url: str
    status: str = "queued"
    stage: str = "queued"
    message: str = "En cola..."
    current_slide: int = 0
    total_slides: Optional[int] = None
    percent: int = 0
    title: Optional[str] = None
    error: Optional[str] = None
    result: Optional[ConversionResult] = None
    thumb_bytes: Optional[bytes] = None
    thumb_version: int = 0
    created_at: float = field(default_factory=time.time)
    cancel_event: asyncio.Event = field(default_factory=asyncio.Event)
    subscribers: list[asyncio.Queue] = field(default_factory=list)


JOBS: Dict[str, JobState] = {}
HISTORY_LOCK = threading.Lock()


def _load_history() -> list[dict]:
    with HISTORY_LOCK:
        if not INDEX_FILE.exists():
            return []
        try:
            data = json.loads(INDEX_FILE.read_text(encoding="utf-8"))
            return [item for item in data if (OUTPUT_DIR / item.get("stored_name", "")).exists()]
        except Exception:
            return []


def _save_history_entry(job_id: str, res: ConversionResult) -> dict:
    entry = {
        "job_id": job_id,
        "title": res.title,
        "filename": res.filename,
        "stored_name": res.pdf_path.name,
        "markdown_filename": res.markdown_filename,
        "markdown_stored_name": res.markdown_path.name,
        "slide_count": res.slide_count,
        "page_count": res.page_count,
        "interactive_count": res.interactive_count,
        "revealed_count": res.revealed_count,
        "size_bytes": res.size_bytes,
        "duration_seconds": res.duration_seconds,
        "slide_width": res.slide_width,
        "slide_height": res.slide_height,
        "source_url": res.source_url,
        "created_at": time.time(),
    }
    with HISTORY_LOCK:
        items = []
        if INDEX_FILE.exists():
            try:
                items = json.loads(INDEX_FILE.read_text(encoding="utf-8"))
            except Exception:
                items = []
        items = [
            x
            for x in items
            if x.get("job_id") != job_id and (OUTPUT_DIR / x.get("stored_name", "")).exists()
        ]
        items.insert(0, entry)
        items = items[:25]
        INDEX_FILE.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
    return entry


def _delete_history_entry(job_id: str) -> bool:
    with HISTORY_LOCK:
        if not INDEX_FILE.exists():
            return False
        try:
            items = json.loads(INDEX_FILE.read_text(encoding="utf-8"))
        except Exception:
            return False
        removed = [x for x in items if x.get("job_id") == job_id]
        kept = [x for x in items if x.get("job_id") != job_id]
        for item in removed:
            for key in ("stored_name", "markdown_stored_name"):
                fname = item.get(key, "")
                if fname:
                    (OUTPUT_DIR / fname).unlink(missing_ok=True)
        INDEX_FILE.write_text(json.dumps(kept, ensure_ascii=False, indent=2), encoding="utf-8")
        return len(removed) > 0


def _result_urls(job_id: str, has_markdown: bool) -> dict:
    urls = {
        "pdf_url": f"/api/jobs/{job_id}/pdf",
        "download_url": f"/api/jobs/{job_id}/pdf?download=1",
    }
    if has_markdown:
        urls["markdown_url"] = f"/api/jobs/{job_id}/markdown"
        urls["markdown_download_url"] = f"/api/jobs/{job_id}/markdown?download=1"
    return urls


def _job_to_dict(job: JobState) -> dict:
    payload = {
        "job_id": job.job_id,
        "url": job.url,
        "status": job.status,
        "stage": job.stage,
        "message": job.message,
        "current_slide": job.current_slide,
        "total_slides": job.total_slides,
        "percent": job.percent,
        "title": job.title,
        "error": job.error,
        "thumb_version": job.thumb_version,
    }
    if job.result:
        payload["result"] = {
            "title": job.result.title,
            "filename": job.result.filename,
            "markdown_filename": job.result.markdown_filename,
            "slide_count": job.result.slide_count,
            "page_count": job.result.page_count,
            "interactive_count": job.result.interactive_count,
            "revealed_count": job.result.revealed_count,
            "size_bytes": job.result.size_bytes,
            "duration_seconds": job.result.duration_seconds,
            "slide_width": job.result.slide_width,
            "slide_height": job.result.slide_height,
            "source_url": job.result.source_url,
            **_result_urls(job.job_id, job.result.markdown_path.exists()),
        }
    return payload


def _notify_subscribers(job: JobState) -> None:
    snap = _job_to_dict(job)
    for queue in list(job.subscribers):
        try:
            queue.put_nowait(snap)
        except Exception:
            pass


async def _run_job(
    job: JobState, scale_factor: int, searchable_text: bool, include_interactive: bool
) -> None:
    job.status = "running"
    _notify_subscribers(job)

    async def on_progress(prog: ConversionProgress) -> None:
        job.stage = prog.stage
        job.message = prog.message
        job.current_slide = prog.current_slide
        job.total_slides = prog.total_slides
        job.percent = prog.percent
        if prog.title:
            job.title = prog.title
        if prog.thumb_bytes:
            job.thumb_bytes = prog.thumb_bytes
            job.thumb_version += 1
        _notify_subscribers(job)

    try:
        res = await convert_genially_to_pdf(
            job.url,
            OUTPUT_DIR,
            scale_factor=scale_factor,
            searchable_text=searchable_text,
            include_interactive=include_interactive,
            on_progress=on_progress,
            cancel_event=job.cancel_event,
        )
        job.result = res
        job.status = "done"
        job.stage = "done"
        job.percent = 100
        job.title = res.title
        _save_history_entry(job.job_id, res)
        _notify_subscribers(job)
    except asyncio.CancelledError:
        job.status = "cancelled"
        job.stage = "cancelled"
        job.message = "Conversión cancelada."
        _notify_subscribers(job)
    except Exception as exc:
        job.status = "error"
        job.stage = "error"
        job.error = str(exc) or "Ocurrió un error inesperado al convertir la presentación."
        job.message = job.error
        _notify_subscribers(job)


@app.get("/", response_class=HTMLResponse)
async def home() -> HTMLResponse:
    html_path = STATIC_DIR / "index.html"
    return HTMLResponse(html_path.read_text(encoding="utf-8"))


@app.get("/api/health")
async def health() -> dict:
    return {"status": "ok"}


@app.post("/api/convert")
async def start_conversion(req: ConvertRequest) -> dict:
    try:
        clean_url = validate_genially_url(req.url)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    job_id = uuid.uuid4().hex[:12]
    job = JobState(job_id=job_id, url=clean_url)
    JOBS[job_id] = job
    scale = 2 if req.scale_factor not in (1, 2, 3) else req.scale_factor
    asyncio.create_task(
        _run_job(
            job,
            scale_factor=scale,
            searchable_text=req.searchable_text,
            include_interactive=req.include_interactive,
        )
    )
    return _job_to_dict(job)


@app.get("/api/jobs/{job_id}")
async def get_job(job_id: str) -> dict:
    job = JOBS.get(job_id)
    if job:
        return _job_to_dict(job)
    for item in _load_history():
        if item.get("job_id") == job_id:
            has_md = bool(
                item.get("markdown_stored_name")
                and (OUTPUT_DIR / item["markdown_stored_name"]).exists()
            )
            return {
                "job_id": job_id,
                "url": item["source_url"],
                "status": "done",
                "stage": "done",
                "percent": 100,
                "title": item["title"],
                "message": "PDF listo.",
                "result": {**item, **_result_urls(job_id, has_md)},
            }
    raise HTTPException(status_code=404, detail="Trabajo no encontrado.")


@app.post("/api/jobs/{job_id}/cancel")
async def cancel_job(job_id: str) -> dict:
    job = JOBS.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Trabajo no encontrado.")
    job.cancel_event.set()
    return {"status": "cancelling"}


@app.get("/api/jobs/{job_id}/events")
async def job_events(job_id: str) -> StreamingResponse:
    job = JOBS.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Trabajo no encontrado.")

    queue: asyncio.Queue = asyncio.Queue()
    job.subscribers.append(queue)

    async def event_stream():
        try:
            yield f"data: {json.dumps(_job_to_dict(job), ensure_ascii=False)}\n\n"
            while job.status in ("queued", "running"):
                try:
                    data = await asyncio.wait_for(queue.get(), timeout=15.0)
                    yield f"data: {json.dumps(data, ensure_ascii=False)}\n\n"
                    if data.get("status") not in ("queued", "running"):
                        break
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
        finally:
            if queue in job.subscribers:
                job.subscribers.remove(queue)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/api/jobs/{job_id}/thumb")
async def job_thumb(job_id: str) -> Response:
    job = JOBS.get(job_id)
    if not job or not job.thumb_bytes:
        raise HTTPException(status_code=404, detail="Miniatura no disponible.")
    return Response(
        content=job.thumb_bytes,
        media_type="image/jpeg",
        headers={"Cache-Control": "no-store"},
    )


def _resolve_file_for_job(job_id: str, kind: str) -> tuple[Path, str]:
    job = JOBS.get(job_id)
    if job and job.result:
        path = job.result.pdf_path if kind == "pdf" else job.result.markdown_path
        name = job.result.filename if kind == "pdf" else job.result.markdown_filename
        if path.exists():
            return path, name

    for item in _load_history():
        if item.get("job_id") == job_id:
            stored_key = "stored_name" if kind == "pdf" else "markdown_stored_name"
            name_key = "filename" if kind == "pdf" else "markdown_filename"
            stored_name = item.get(stored_key, "")
            if stored_name:
                candidate = OUTPUT_DIR / stored_name
                if candidate.exists():
                    return candidate, item.get(name_key, candidate.name)

    raise HTTPException(status_code=404, detail="Archivo no encontrado.")


def _content_disposition(disposition: str, download_name: str, fallback: str) -> str:
    ascii_name = download_name.encode("ascii", "ignore").decode("ascii") or fallback
    encoded_name = quote(download_name)
    return f"{disposition}; filename=\"{ascii_name}\"; filename*=UTF-8''{encoded_name}"


@app.get("/api/jobs/{job_id}/pdf")
async def serve_pdf(job_id: str, download: int = Query(0)) -> FileResponse:
    pdf_path, download_name = _resolve_file_for_job(job_id, "pdf")
    disposition = "attachment" if download else "inline"
    return FileResponse(
        path=pdf_path,
        media_type="application/pdf",
        headers={
            "Content-Disposition": _content_disposition(disposition, download_name, "presentacion.pdf"),
            "Cache-Control": "no-store",
        },
    )


@app.get("/api/jobs/{job_id}/markdown")
async def serve_markdown(job_id: str, download: int = Query(0)) -> Response:
    md_path, download_name = _resolve_file_for_job(job_id, "markdown")
    text = md_path.read_text(encoding="utf-8")
    if download:
        return Response(
            content=text.encode("utf-8"),
            media_type="text/markdown; charset=utf-8",
            headers={
                "Content-Disposition": _content_disposition("attachment", download_name, "contexto_IA.md"),
                "Cache-Control": "no-store",
            },
        )
    return PlainTextResponse(content=text, headers={"Cache-Control": "no-store"})


@app.get("/api/history")
async def get_history() -> list[dict]:
    items = _load_history()
    out = []
    for item in items:
        has_md = bool(
            item.get("markdown_stored_name")
            and (OUTPUT_DIR / item["markdown_stored_name"]).exists()
        )
        out.append({**item, **_result_urls(item["job_id"], has_md)})
    return out


@app.delete("/api/history/{job_id}")
async def remove_history(job_id: str) -> dict:
    deleted = _delete_history_entry(job_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Elemento no encontrado.")
    return {"deleted": True}


def _is_our_server_running(port: int) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=1.2) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return resp.status == 200 and data.get("status") == "ok"
    except Exception:
        return False


def _pick_available_port(preferred: int) -> int:
    for candidate in range(preferred, preferred + 20):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            try:
                sock.bind(("127.0.0.1", candidate))
                return candidate
            except OSError:
                continue
    return preferred


def main() -> None:
    preferred_port = int(os.environ.get("PORT", "8765"))
    if _is_our_server_running(preferred_port):
        url = f"http://127.0.0.1:{preferred_port}"
        print(f"\n  Genially -> PDF ya estaba corriendo en: {url}\n")
        if os.environ.get("NO_BROWSER") != "1":
            webbrowser.open(url)
        return

    port = preferred_port if "PORT" in os.environ else _pick_available_port(preferred_port)
    url = f"http://127.0.0.1:{port}"
    print(f"\n  Genially -> PDF corriendo en: {url}\n  Carpeta de PDFs: {OUTPUT_DIR}\n")

    if os.environ.get("NO_BROWSER") != "1":
        threading.Timer(1.2, lambda: webbrowser.open(url)).start()

    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")


if __name__ == "__main__":
    main()
