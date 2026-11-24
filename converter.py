"""Motor de conversión de presentaciones públicas de Genially a PDF."""

from __future__ import annotations

import asyncio
import io
import os
import re
import time
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Awaitable, Callable, Optional
from urllib.parse import urlparse

import pymupdf
from PIL import Image
from playwright.async_api import Browser, Page, async_playwright

CHROME_HIDE_CSS = """
[data-cy="nextSlideButton"],
[data-cy="previousSlideButton"],
[data-cy="fullScreenButton"],
[data-cy="viewOptionsButton"],
[data-cy="showIndicatorsButton"],
[data-cy="eduWatermark"],
[role="tooltip"],
[data-tippy-root],
.tippy-box,
#onetrust-consent-sdk,
[class*="CookieBanner"],
[aria-label="Página siguiente"],
[aria-label="Página anterior"],
[aria-label="Next page"],
[aria-label="Previous page"],
[aria-label="Mostrar elementos interactivos"],
[aria-label="Show interactive elements"],
[aria-label="Pantalla completa"],
[aria-label="Full screen"],
[aria-label="Opciones del genially"],
[aria-label="Genially options"] {
    opacity: 0 !important;
    visibility: hidden !important;
    transition: none !important;
}
"""

ALLOWED_HOST_SUFFIXES = ("genially.com", "genial.ly")


@dataclass
class ConversionProgress:
    stage: str
    message: str
    current_slide: int = 0
    total_slides: Optional[int] = None
    percent: int = 0
    title: Optional[str] = None
    thumb_bytes: Optional[bytes] = None


@dataclass
class ConversionResult:
    pdf_path: Path
    filename: str
    title: str
    page_count: int
    size_bytes: int
    duration_seconds: float
    slide_width: int
    slide_height: int
    source_url: str


def validate_genially_url(raw_url: str) -> str:
    """Valida y normaliza un enlace de Genially."""
    url = (raw_url or "").strip()
    if not url:
        raise ValueError("Por favor pega un enlace de Genially.")
    if not re.match(r"^https?://", url, re.IGNORECASE):
        url = "https://" + url

    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if parsed.scheme not in ("http", "https") or not host:
        raise ValueError("El enlace ingresado no es una URL válida.")
    if not any(host == suffix or host.endswith("." + suffix) for suffix in ALLOWED_HOST_SUFFIXES):
        raise ValueError(
            "El enlace debe pertenecer a Genially (por ejemplo, https://view.genially.com/... o https://view.genial.ly/...)."
        )
    if not parsed.path or parsed.path == "/":
        raise ValueError("El enlace no apunta a una presentación específica de Genially.")
    return url


def sanitize_filename(title: str) -> str:
    """Convierte el título de la presentación en un nombre de archivo limpio."""
    cleaned = re.sub(r"\s*\|\s*Genially\s*$", "", title or "", flags=re.IGNORECASE).strip()
    if not cleaned:
        cleaned = "Presentacion_Genially"
    normalized = unicodedata.normalize("NFKD", cleaned)
    ascii_safe = normalized.encode("ascii", "ignore").decode("ascii")
    ascii_safe = re.sub(r"[^\w\s-]", " ", ascii_safe)
    ascii_safe = re.sub(r"[-\s]+", "_", ascii_safe).strip("_")
    return (ascii_safe[:80] or "Presentacion_Genially") + ".pdf"


async def _launch_browser(playwright) -> Browser:
    """Inicia Chrome o Edge del sistema sin requerir descargas adicionales de navegadores."""
    launch_args = [
        "--disable-blink-features=AutomationControlled",
        "--no-sandbox",
    ]
    for channel in ("chrome", "msedge", None):
        try:
            if channel:
                return await playwright.chromium.launch(
                    channel=channel, headless=True, args=launch_args
                )
            return await playwright.chromium.launch(headless=True, args=launch_args)
        except Exception:
            continue
    raise RuntimeError(
        "No se pudo iniciar Google Chrome, Microsoft Edge ni Chromium. "
        "Asegúrate de tener Chrome o Edge instalado (o ejecuta: playwright install chromium)."
    )


async def _wait_slide_settled(
    page: Page, min_wait_ms: int = 1100, max_wait_ms: int = 4200
) -> None:
    """Espera a que terminen la transición de diapositiva, imágenes y animaciones JS/CSS."""
    start = time.monotonic()
    await page.wait_for_timeout(min_wait_ms)
    prev_sig: Optional[str] = None
    stable_count = 0

    while (time.monotonic() - start) * 1000 < max_wait_ms:
        state = await page.evaluate(
            """() => {
            const finiteAnims = document.getAnimations().filter(a => {
                if (a.playState !== 'running') return false;
                const timing = a.effect?.getTiming?.() || {};
                return timing.iterations !== Infinity;
            }).length;

            const slides = Array.from(document.querySelectorAll('.genially-view-slide'));
            const activeSlide = slides.find(s =>
                !s.querySelector('[data-cy="slide-transversal-id"]') &&
                s.getAttribute('data-cy') !== 'slide-transversal-id'
            ) || slides[0];
            if (!activeSlide) return { ready: false };

            const imgs = Array.from(activeSlide.querySelectorAll('img'));
            const pendingImgs = imgs.filter(img => !img.complete || img.naturalWidth === 0).length;

            const wrappers = Array.from(activeSlide.querySelectorAll('.genially-animated-wrapper'));
            const sig = wrappers.map(w => {
                const cs = getComputedStyle(w);
                return `${Number(cs.opacity).toFixed(2)}|${cs.visibility}`;
            }).join(';');

            const midFade = wrappers.some(w => {
                const op = parseFloat(getComputedStyle(w).opacity);
                return op > 0.01 && op < 0.99;
            });

            return { ready: true, finiteAnims, pendingImgs, midFade, sig };
        }"""
        )

        if (
            state.get("ready")
            and state["finiteAnims"] == 0
            and state["pendingImgs"] == 0
            and not state["midFade"]
        ):
            if state["sig"] == prev_sig:
                stable_count += 1
                if stable_count >= 2:
                    break
            else:
                stable_count = 0
            prev_sig = state["sig"]
        else:
            stable_count = 0
            prev_sig = state.get("sig")

        await page.wait_for_timeout(180)


async def _get_slide_signature(page: Page) -> tuple[str, ...]:
    """Devuelve identificadores de las diapositivas activas para detectar cambios reales."""
    ids = await page.evaluate(
        """() => {
        const byCy = Array.from(document.querySelectorAll('[data-cy^="slide-"]'))
            .map(el => el.getAttribute('data-cy'))
            .filter(cy => cy && cy !== 'slide-transversal-id');
        if (byCy.length > 0) return byCy;
        const slide = document.querySelector('.genially-view-slide');
        return slide ? [(slide.innerText || '').slice(0, 120)] : [];
    }"""
    )
    return tuple(ids)


async def _detect_total_pages(page: Page, slide_w: int, slide_h: int) -> Optional[int]:
    """Intenta leer el total de páginas desde el panel de opciones de Genially."""
    try:
        await page.mouse.move(slide_w // 2, slide_h // 2)
        await page.wait_for_timeout(200)
        opt_btn = page.locator('[data-cy="viewOptionsButton"]')
        if await opt_btn.count() == 0:
            return None

        is_expanded = await opt_btn.get_attribute("aria-expanded")
        if is_expanded != "true":
            await page.evaluate(
                '() => document.querySelector(\'[data-cy="viewOptionsButton"]\')?.click()'
            )
            await page.wait_for_timeout(350)

        total_pages: Optional[int] = None
        inp = page.locator('[data-cy="slidePaginationInput"]')
        if await inp.count() > 0:
            aria = await inp.get_attribute("aria-label") or ""
            row = page.locator('[data-cy="pageNumberRow"]')
            row_text = await row.inner_text() if await row.count() > 0 else ""
            match = re.search(r"(\d+)\s*$", row_text.strip()) or re.search(
                r"(\d+)\s*$", aria.strip()
            )
            if match:
                total_pages = int(match.group(1))

        if total_pages is None:
            pages_btn = page.locator('[data-cy="pagesNavigationButton"]')
            if await pages_btn.count() > 0:
                await pages_btn.click(force=True)
                await page.wait_for_timeout(350)
                count = await page.locator('[role="dialog"] button').count()
                if count > 0:
                    total_pages = count
                await page.keyboard.press("Escape")
                await page.wait_for_timeout(200)

        if await opt_btn.get_attribute("aria-expanded") == "true":
            await page.evaluate(
                '() => document.querySelector(\'[data-cy="viewOptionsButton"]\')?.click()'
            )
            await page.wait_for_timeout(250)
        await page.evaluate("() => { if (document.activeElement) document.activeElement.blur(); }")
        return total_pages
    except Exception:
        return None


async def _advance_to_next_slide(
    page: Page, target_index: int, prev_sig: tuple[str, ...]
) -> bool:
    """Avanza a la siguiente diapositiva usando botón lateral, menú o teclado."""

    async def wait_for_change(timeout_ms: int = 2400) -> bool:
        steps = max(1, timeout_ms // 100)
        for _ in range(steps):
            await page.wait_for_timeout(100)
            new_sig = await _get_slide_signature(page)
            if new_sig and new_sig != prev_sig:
                return True
        return False

    # 1. Botón siguiente estándar en el borde derecho (clic vía DOM para no activar tooltips)
    has_next = await page.evaluate(
        '() => Boolean(document.querySelector(\'[data-cy="nextSlideButton"]\'))'
    )
    if has_next:
        await page.evaluate(
            '() => document.querySelector(\'[data-cy="nextSlideButton"]\')?.click()'
        )
        if await wait_for_change(2500):
            return True

    # 2. Navegación por panel de opciones / input numérico (útil si las flechas están ocultas)
    has_options = await page.evaluate(
        '() => Boolean(document.querySelector(\'[data-cy="viewOptionsButton"]\'))'
    )
    if has_options:
        await page.evaluate(
            '() => document.querySelector(\'[data-cy="viewOptionsButton"]\')?.click()'
        )
        await page.wait_for_timeout(250)
        inp = page.locator('[data-cy="slidePaginationInput"]')
        if await inp.count() > 0:
            await inp.fill(str(target_index))
            await inp.press("Enter")
            await page.wait_for_timeout(250)
        else:
            await page.evaluate(
                '() => document.querySelector(\'[data-cy="nextPageButton"]\')?.click()'
            )
        await page.evaluate(
            '() => document.querySelector(\'[data-cy="viewOptionsButton"]\')?.click()'
        )
        await page.evaluate("() => { if (document.activeElement) document.activeElement.blur(); }")
        if await wait_for_change(2200):
            return True

    # 3. Tecla ArrowRight como último respaldo
    await page.keyboard.press("ArrowRight")
    return await wait_for_change(2000)


async def _extract_text_boxes(page: Page) -> list[dict]:
    """Extrae cajas de texto visibles para incrustar una capa buscable (Ctrl+F) en el PDF."""
    return await page.evaluate(
        """() => {
        const slides = Array.from(document.querySelectorAll('.genially-view-slide'));
        const activeSlide = slides.find(s =>
            !s.querySelector('[data-cy="slide-transversal-id"]') &&
            s.getAttribute('data-cy') !== 'slide-transversal-id'
        ) || slides[0];
        if (!activeSlide) return [];

        const items = [];
        const walker = document.createTreeWalker(activeSlide, NodeFilter.SHOW_TEXT, null);
        let node;
        while ((node = walker.nextNode())) {
            const txt = (node.nodeValue || '').replace(/\\s+/g, ' ').trim();
            if (!txt) continue;
            const parent = node.parentElement;
            if (!parent) continue;
            const cs = getComputedStyle(parent);
            if (cs.visibility === 'hidden' || parseFloat(cs.opacity) < 0.1 || cs.display === 'none') {
                continue;
            }
            const range = document.createRange();
            range.selectNodeContents(node);
            const rect = range.getBoundingClientRect();
            if (rect.width <= 0 || rect.height <= 0) continue;
            items.push({
                text: txt,
                x: rect.left,
                y: rect.top,
                w: rect.width,
                h: rect.height,
                fontSize: parseFloat(cs.fontSize) || 14
            });
        }
        return items;
    }"""
    )


async def convert_genially_to_pdf(
    url: str,
    output_dir: Path,
    *,
    scale_factor: int = 2,
    jpeg_quality: int = 92,
    searchable_text: bool = True,
    max_slides_limit: int = 120,
    on_progress: Optional[Callable[[ConversionProgress], Awaitable[None]]] = None,
    cancel_event: Optional[asyncio.Event] = None,
) -> ConversionResult:
    """Convierte una presentación pública de Genially en un archivo PDF multipágina."""
    clean_url = validate_genially_url(url)
    output_dir.mkdir(parents=True, exist_ok=True)
    started_at = time.monotonic()

    async def report(
        stage: str,
        message: str,
        current: int = 0,
        total: Optional[int] = None,
        percent: int = 0,
        title: Optional[str] = None,
        thumb_bytes: Optional[bytes] = None,
    ) -> None:
        if on_progress:
            await on_progress(
                ConversionProgress(
                    stage=stage,
                    message=message,
                    current_slide=current,
                    total_slides=total,
                    percent=max(0, min(100, percent)),
                    title=title,
                    thumb_bytes=thumb_bytes,
                )
            )

    def check_cancelled() -> None:
        if cancel_event and cancel_event.is_set():
            raise asyncio.CancelledError("Conversión cancelada por el usuario.")

    await report("starting", "Abriendo navegador sin cabeza...", percent=3)

    async with async_playwright() as p:
        browser = await _launch_browser(p)
        try:
            context = await browser.new_context(
                viewport={"width": 1280, "height": 720},
                device_scale_factor=max(1, min(3, scale_factor)),
                locale="es-ES",
            )
            page = await context.new_page()

            await report("loading", "Cargando presentación de Genially...", percent=8)
            resp = await page.goto(clean_url, wait_until="domcontentloaded", timeout=60000)
            if resp and resp.status >= 400:
                raise RuntimeError(
                    f"Genially devolvió un error HTTP {resp.status}. Verifica que el enlace sea público y correcto."
                )

            try:
                await page.wait_for_selector(".genially-view-slide", timeout=35000)
            except Exception as exc:
                page_text = (await page.evaluate("() => document.body?.innerText || ''"))[:300]
                if "privad" in page_text.lower() or "password" in page_text.lower() or "contraseña" in page_text.lower():
                    raise RuntimeError(
                        "Esta presentación parece privada o protegida con contraseña y no se puede convertir."
                    ) from exc
                raise RuntimeError(
                    "No se encontró el lienzo de la presentación Genially. Verifica que el enlace sea válido y público."
                ) from exc

            check_cancelled()
            await page.evaluate("() => document.fonts.ready")
            await _wait_slide_settled(page, min_wait_ms=1400)

            meta = await page.evaluate(
                """() => {
                const slide = document.querySelector('.genially-view-slide');
                return {
                    w: slide ? slide.offsetWidth : 1200,
                    h: slide ? slide.offsetHeight : 675,
                    title: document.title || 'Presentación Genially'
                };
            }"""
            )
            raw_title = meta["title"]
            clean_title = re.sub(r"\s*\|\s*Genially\s*$", "", raw_title, flags=re.IGNORECASE).strip() or "Presentación Genially"
            slide_w = max(320, min(2400, int(meta["w"])))
            slide_h = max(240, min(2400, int(meta["h"])))

            await page.set_viewport_size({"width": slide_w, "height": slide_h})
            await page.wait_for_timeout(350)

            total_pages = await _detect_total_pages(page, slide_w, slide_h)
            target_total = min(total_pages, max_slides_limit) if total_pages else max_slides_limit

            await page.add_style_tag(content=CHROME_HIDE_CSS)
            await page.mouse.move(0, 0)

            await report(
                "capturing",
                f"Detectadas {total_pages or '?'} diapositivas ({slide_w}×{slide_h}px). Iniciando captura...",
                current=0,
                total=total_pages,
                percent=14,
                title=clean_title,
            )

            pdf = pymupdf.open()
            toc: list = []
            seen_signatures: set[tuple[str, ...]] = set()
            pt_w = slide_w * 0.75
            pt_h = slide_h * 0.75

            for idx in range(1, target_total + 1):
                check_cancelled()
                await _wait_slide_settled(page, min_wait_ms=1150 if idx > 1 else 500)

                sig = await _get_slide_signature(page)
                if total_pages is None and sig in seen_signatures and idx > 1:
                    break
                seen_signatures.add(sig)

                png_bytes = await page.screenshot(type="png")
                im = Image.open(io.BytesIO(png_bytes)).convert("RGB")

                # Miniatura ligera para mostrar progreso en vivo en la UI
                thumb_im = im.copy()
                thumb_im.thumbnail((480, 320))
                thumb_buf = io.BytesIO()
                thumb_im.save(thumb_buf, format="JPEG", quality=75)

                jpg_buf = io.BytesIO()
                im.save(jpg_buf, format="JPEG", quality=jpeg_quality, optimize=True)

                pdf_page = pdf.new_page(width=pt_w, height=pt_h)
                pdf_page.insert_image(pymupdf.Rect(0, 0, pt_w, pt_h), stream=jpg_buf.getvalue())

                text_boxes = await _extract_text_boxes(page) if searchable_text else []
                for box in text_boxes:
                    bx = box["x"] * 0.75
                    by = (box["y"] + box["h"] * 0.8) * 0.75
                    fs = max(4.0, min(72.0, box["fontSize"] * 0.75))
                    if 0 <= bx <= pt_w and 0 <= by <= pt_h:
                        try:
                            pdf_page.insert_text(
                                pymupdf.Point(bx, by),
                                box["text"],
                                fontsize=fs,
                                fontname="helv",
                                render_mode=3,
                            )
                        except Exception:
                            pass

                snippet = " ".join(b["text"] for b in text_boxes)[:55].strip()
                toc.append([1, f"{idx}. {snippet or f'Diapositiva {idx}'}", idx])

                progress_pct = 15 + int((idx / (total_pages or max(idx + 3, 10))) * 78)
                await report(
                    "capturing",
                    f"Capturada diapositiva {idx}" + (f" de {total_pages}" if total_pages else ""),
                    current=idx,
                    total=total_pages,
                    percent=min(93, progress_pct),
                    title=clean_title,
                    thumb_bytes=thumb_buf.getvalue(),
                )

                if total_pages and idx >= total_pages:
                    break

                advanced = await _advance_to_next_slide(page, idx + 1, sig)
                if not advanced and total_pages is None:
                    break

            check_cancelled()
            await report(
                "building",
                "Ensamblando y optimizando el documento PDF...",
                current=len(pdf),
                total=len(pdf),
                percent=96,
                title=clean_title,
            )

            if len(pdf) == 0:
                pdf.close()
                raise RuntimeError("No se pudo capturar ninguna diapositiva del enlace proporcionado.")

            if toc:
                pdf.set_toc(toc)
            pdf.set_metadata(
                {
                    "title": clean_title,
                    "subject": clean_url,
                    "creator": "Genially2PDF Microapp",
                }
            )

            base_filename = sanitize_filename(clean_title)
            stem = Path(base_filename).stem
            unique_tag = time.strftime("%Y%m%d_%H%M%S")
            out_path = output_dir / f"{stem}_{unique_tag}.pdf"
            pdf.save(out_path, deflate=True, garbage=3)
            page_count = len(pdf)
            pdf.close()

            size_bytes = os.path.getsize(out_path)
            duration = round(time.monotonic() - started_at, 1)

            await report(
                "done",
                f"¡PDF listo! {page_count} páginas generadas en {duration}s.",
                current=page_count,
                total=page_count,
                percent=100,
                title=clean_title,
            )

            return ConversionResult(
                pdf_path=out_path,
                filename=base_filename,
                title=clean_title,
                page_count=page_count,
                size_bytes=size_bytes,
                duration_seconds=duration,
                slide_width=slide_w,
                slide_height=slide_h,
                source_url=clean_url,
            )
        finally:
            await browser.close()
