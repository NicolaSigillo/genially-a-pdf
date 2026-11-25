"""Extracción de interactividad de Genially, fichas vectoriales HTML->PDF y contexto Markdown para IA."""

from __future__ import annotations

import html
import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Any, Optional


@dataclass
class InteractiveNote:
    action_id: str
    trigger: str  # "click" | "hover" | "media"
    kind: str  # "tooltip" | "window" | "link" | "reveal" | "video" | "audio" | "goto"
    kind_label: str
    item_label: str
    html_body: str
    plain_text: str
    image_urls: list[str] = field(default_factory=list)
    link_urls: list[str] = field(default_factory=list)
    target_slide_order: Optional[int] = None


@dataclass
class InteractiveCluster:
    number: int
    x: float
    y: float
    width: float
    height: float
    label: str
    notes: list[InteractiveNote] = field(default_factory=list)


@dataclass
class SlideContextRecord:
    slide_number: int
    slide_uuid: str
    slide_title: str
    pdf_page_number: int
    visible_text: str
    image_count: int
    revealed_texts: list[str] = field(default_factory=list)
    revealed_pdf_pages: list[int] = field(default_factory=list)
    clusters: list[InteractiveCluster] = field(default_factory=list)
    detail_pdf_pages: list[int] = field(default_factory=list)


class _RichHtmlExtractor(HTMLParser):
    """Extrae texto legible, imágenes y enlaces de fragmentos HTML de Genially."""

    BLOCK_TAGS = {"p", "div", "li", "h1", "h2", "h3", "h4", "tr", "blockquote"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.images: list[str] = []
        self.links: list[str] = []
        self._in_style_or_script = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, Optional[str]]]) -> None:
        tag_l = tag.lower()
        attr_map = dict(attrs)
        if tag_l in ("style", "script", "head"):
            self._in_style_or_script = True
            return
        if tag_l == "br":
            self.parts.append("\n")
        elif tag_l == "li":
            self.parts.append("\n- ")
        elif tag_l in self.BLOCK_TAGS:
            self.parts.append("\n")
        elif tag_l == "img":
            src = (attr_map.get("src") or "").strip()
            if src and src not in self.images:
                self.images.append(src)
        elif tag_l == "a":
            href = (attr_map.get("href") or "").strip()
            if href and href.startswith(("http://", "https://")) and href not in self.links:
                self.links.append(href)

    def handle_endtag(self, tag: str) -> None:
        tag_l = tag.lower()
        if tag_l in ("style", "script", "head"):
            self._in_style_or_script = False
        elif tag_l in self.BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._in_style_or_script:
            return
        cleaned = data.replace("\xa0", " ")
        self.parts.append(cleaned)


def extract_from_html(raw_html: str) -> tuple[str, list[str], list[str]]:
    """Devuelve (texto_limpio, urls_imagenes, urls_enlaces) desde un HTML de Genially."""
    if not raw_html:
        return "", [], []
    parser = _RichHtmlExtractor()
    parser.feed(raw_html)
    parser.close()

    raw_text = "".join(parser.parts)
    lines = [re.sub(r"[ \t]+", " ", ln).strip() for ln in raw_text.splitlines()]
    compact_lines: list[str] = []
    for ln in lines:
        if ln or (compact_lines and compact_lines[-1] != ""):
            compact_lines.append(ln)
    text = "\n".join(compact_lines).strip()
    return text, parser.images, parser.links


def sanitize_tooltip_html(raw_html: str) -> str:
    """Limpia etiquetas <head>/<body>/scripts de Genially para renderizado seguro en ficha HTML."""
    if not raw_html:
        return ""
    body_match = re.search(r"<body[^>]*>(.*)</body>", raw_html, flags=re.IGNORECASE | re.DOTALL)
    fragment = body_match.group(1) if body_match else raw_html
    fragment = re.sub(r"<(script|style|meta|head)[^>]*>.*?</\1>", "", fragment, flags=re.IGNORECASE | re.DOTALL)
    fragment = re.sub(r"<(meta|link)\b[^>]*>", "", fragment, flags=re.IGNORECASE)
    fragment = re.sub(r'\s+on[a-z]+\s*=\s*(?:"[^"]*"|\'[^\']*\'|[^\s>]+)', "", fragment, flags=re.IGNORECASE)
    # Normaliza fuentes exageradas y quita fondos oscuros heredados de copiado/pegado
    fragment = re.sub(r"font-size\s*:\s*\d+(?:\.\d+)?px", "font-size: 15px", fragment, flags=re.IGNORECASE)
    fragment = re.sub(r"background(?:-color)?\s*:\s*[^;\"']*;?", "", fragment, flags=re.IGNORECASE)
    fragment = re.sub(r"color\s*:\s*rgb\(255,\s*255,\s*255\)", "color: #1f2937", fragment, flags=re.IGNORECASE)
    return fragment.strip()


def _parse_px(val: Any, default: float = 0.0) -> float:
    if isinstance(val, (int, float)):
        return float(val)
    if isinstance(val, str):
        m = re.search(r"-?\d+(?:\.\d+)?", val)
        if m:
            return float(m.group(0))
    return default


def _item_label(col: str, item: dict) -> str:
    raw = item.get("TextMessage") or item.get("Name") or item.get("LayerTitle") or ""
    if not raw and isinstance(item.get("Content"), dict):
        # Rich TipTap doc
        texts: list[str] = []

        def walk(node: Any) -> None:
            if isinstance(node, dict):
                if isinstance(node.get("text"), str):
                    texts.append(node["text"])
                for child in node.get("content") or []:
                    walk(child)
            elif isinstance(node, list):
                for child in node:
                    walk(child)

        walk(item["Content"])
        raw = " ".join(texts)

    clean, _, _ = extract_from_html(str(raw)) if "<" in str(raw) else (str(raw), [], [])
    clean = re.sub(r"\s+", " ", clean).strip()
    if clean and clean.lower() not in ("imagen", "image", "svg", "area", "grupo", "group"):
        return clean[:48]
    fallback = {
        "Texts": "Texto interactivo",
        "Images": "Botón / imagen interactiva",
        "Svgs": "Ícono interactivo",
        "Areas": "Área interactiva",
        "Pins": "Marcador interactivo",
        "Groups": "Elemento agrupado",
        "Videos": "Video embebido",
        "Audios": "Audio embebido",
    }
    return fallback.get(col, "Elemento interactivo")


def _extract_action_note(
    action_id: str,
    trigger: str,
    action: dict,
    item_label: str,
    slides_order_by_id: dict[str, int],
) -> Optional[InteractiveNote]:
    atype = str(action.get("type") or "").strip()
    atype_l = atype.lower()

    if atype_l in ("htmltooltip", "tooltip"):
        raw_html = str(action.get("html") or action.get("text") or "")
        text, imgs, links = extract_from_html(raw_html)
        if not text and not imgs:
            return None
        return InteractiveNote(
            action_id=action_id,
            trigger=trigger,
            kind="tooltip",
            kind_label="Nota / Tooltip",
            item_label=item_label,
            html_body=sanitize_tooltip_html(raw_html),
            plain_text=text,
            image_urls=imgs,
            link_urls=links,
        )

    if any(k in atype_l for k in ("window", "modal", "popup", "richcontent")):
        raw_html = str(action.get("html") or action.get("content") or action.get("text") or "")
        title = str(action.get("title") or "").strip()
        text, imgs, links = extract_from_html(raw_html)
        full_text = f"{title}: {text}".strip(": ") if title else text
        if not full_text and not imgs:
            return None
        title_html = f"<p><strong>{html.escape(title)}</strong></p>" if title else ""
        return InteractiveNote(
            action_id=action_id,
            trigger=trigger,
            kind="window",
            kind_label="Ventana emergente",
            item_label=item_label,
            html_body=title_html + sanitize_tooltip_html(raw_html),
            plain_text=full_text,
            image_urls=imgs,
            link_urls=links,
        )

    if "link" in atype_l or "url" in atype_l:
        target = action.get("target") if isinstance(action.get("target"), dict) else {}
        url = str(action.get("url") or action.get("href") or target.get("url") or "").strip()
        if not url:
            return None
        escaped = html.escape(url)
        return InteractiveNote(
            action_id=action_id,
            trigger=trigger,
            kind="link",
            kind_label="Enlace externo",
            item_label=item_label,
            html_body=f'<p><a href="{escaped}">{escaped}</a></p>',
            plain_text=url,
            link_urls=[url],
        )

    if "showelements" in atype_l or "stagedanimation" in atype_l:
        return InteractiveNote(
            action_id=action_id,
            trigger=trigger,
            kind="reveal",
            kind_label="Revelar elementos ocultos",
            item_label=item_label,
            html_body="<p>Muestra contenido oculto directamente sobre la diapositiva (capturado en la lámina siguiente).</p>",
            plain_text="Muestra contenido oculto sobre la diapositiva (ver lámina revelada siguiente).",
        )

    if "gotoslide" in atype_l or "gotopage" in atype_l:
        target = action.get("target") if isinstance(action.get("target"), dict) else {}
        slide_id = target.get("slideId") or target.get("idSlide") or action.get("slideId")
        target_order = slides_order_by_id.get(slide_id) if slide_id else None
        smart = target.get("smartLink")
        desc = f"Diapositiva {target_order}" if target_order else (str(smart) if smart else "otra diapositiva")
        return InteractiveNote(
            action_id=action_id,
            trigger=trigger,
            kind="goto",
            kind_label="Navegación interna",
            item_label=item_label,
            html_body=f"<p>Salta a: {html.escape(desc)}</p>",
            plain_text=f"Salta a: {desc}",
            target_slide_order=target_order,
        )

    if "audio" in atype_l or "video" in atype_l:
        url = str(action.get("url") or action.get("src") or "").strip()
        if url:
            escaped = html.escape(url)
            return InteractiveNote(
                action_id=action_id,
                trigger=trigger,
                kind="video" if "video" in atype_l else "audio",
                kind_label="Multimedia",
                item_label=item_label,
                html_body=f'<p><a href="{escaped}">{escaped}</a></p>',
                plain_text=url,
                link_urls=[url],
            )
    return None


def extract_slide_interactions(
    view_payload: dict,
    slide_uuid: str,
    slide_w: int,
    slide_h: int,
) -> tuple[list[InteractiveCluster], list[tuple[float, float]]]:
    """
    Extrae las interacciones agrupadas por posición para una diapositiva específica.
    Devuelve (clusters_con_contenido, puntos_click_showElements).
    """
    if not view_payload or not slide_uuid:
        return [], []

    actions = view_payload.get("interactivityActions") or {}
    if not isinstance(actions, dict):
        return [], []

    slides_sorted = sorted(view_payload.get("Slides") or [], key=lambda s: s.get("Order", 0))
    slides_order_by_id = {
        s.get("Id"): idx for idx, s in enumerate(slides_sorted, start=1) if s.get("Id")
    }
    groups_by_id = {
        g.get("Id"): g for g in (view_payload.get("Groups") or []) if isinstance(g, dict) and g.get("Id")
    }

    raw_entries: list[tuple[float, float, float, float, InteractiveNote]] = []
    reveal_points: list[tuple[float, float]] = []

    collections = (
        "Texts",
        "Images",
        "Svgs",
        "Areas",
        "Pins",
        "Groups",
        "ContainerBoxes",
        "RichContents",
    )

    for col in collections:
        for item in view_payload.get(col) or []:
            if not isinstance(item, dict) or item.get("IdSlide") != slide_uuid:
                continue
            inter = item.get("interactivities")
            if not isinstance(inter, dict) or not inter:
                continue

            pos = item.get("Position") or {}
            size = item.get("Size") or {}
            x = _parse_px(pos.get("PositionLeft"))
            y = _parse_px(pos.get("PositionTop"))
            w = max(16.0, _parse_px(size.get("Width"), 40.0))
            h = max(16.0, _parse_px(size.get("Height"), 30.0))

            # Si pertenece a un grupo y las coordenadas parecen relativas
            grp_id = item.get("IdGroup")
            if grp_id and grp_id in groups_by_id:
                grp = groups_by_id[grp_id]
                gpos = grp.get("Position") or {}
                gx = _parse_px(gpos.get("PositionLeft"))
                gy = _parse_px(gpos.get("PositionTop"))
                gsize = grp.get("Size") or {}
                gw = _parse_px(gsize.get("Width"), slide_w)
                gh = _parse_px(gsize.get("Height"), slide_h)
                if 0 <= x <= gw and 0 <= y <= gh and (gx > 0 or gy > 0):
                    x += gx
                    y += gy

            x = max(0.0, min(float(slide_w - 8), x))
            y = max(0.0, min(float(slide_h - 8), y))
            label = _item_label(col, item)

            for trigger in ("click", "hover"):
                action_id = inter.get(trigger)
                if not action_id or action_id not in actions:
                    continue
                note = _extract_action_note(
                    action_id, trigger, actions[action_id], label, slides_order_by_id
                )
                if not note:
                    continue
                if note.kind == "reveal":
                    reveal_points.append((x + w / 2.0, y + h / 2.0))
                elif note.kind == "goto":
                    # Los saltos simples a otra página no necesitan ficha aparte
                    continue
                else:
                    raw_entries.append((x, y, w, h, note))

    # Agrega videos o audios embebidos en la lámina si tienen fuente externa
    for col, kind, klabel in (("Videos", "video", "Video embebido"), ("Audios", "audio", "Audio embebido")):
        for item in view_payload.get(col) or []:
            if not isinstance(item, dict) or item.get("IdSlide") != slide_uuid:
                continue
            src = str(item.get("Source") or item.get("Url") or item.get("Link") or "").strip()
            if not src:
                continue
            pos = item.get("Position") or {}
            size = item.get("Size") or {}
            x = max(0.0, min(float(slide_w - 8), _parse_px(pos.get("PositionLeft"))))
            y = max(0.0, min(float(slide_h - 8), _parse_px(pos.get("PositionTop"))))
            w = max(24.0, _parse_px(size.get("Width"), 60.0))
            h = max(24.0, _parse_px(size.get("Height"), 40.0))
            escaped = html.escape(src)
            note = InteractiveNote(
                action_id=f"{col}-{item.get('Id', '')}",
                trigger="media",
                kind=kind,
                kind_label=klabel,
                item_label=_item_label(col, item),
                html_body=f'<p><a href="{escaped}">{escaped}</a></p>',
                plain_text=src,
                link_urls=[src],
            )
            raw_entries.append((x, y, w, h, note))

    # Orden visual arriba->abajo, izquierda->derecha y fusión de elementos superpuestos
    raw_entries.sort(key=lambda e: (round(e[1] / 25.0), e[0]))

    clusters: list[InteractiveCluster] = []
    for x, y, w, h, note in raw_entries:
        cx, cy = x + w / 2.0, y + h / 2.0
        matched: Optional[InteractiveCluster] = None
        for cluster in clusters:
            ccx = cluster.x + cluster.width / 2.0
            ccy = cluster.y + cluster.height / 2.0
            if abs(cx - ccx) <= 38.0 and abs(cy - ccy) <= 38.0:
                matched = cluster
                break
        if matched:
            if not any(n.action_id == note.action_id or (n.plain_text and n.plain_text == note.plain_text) for n in matched.notes):
                matched.notes.append(note)
            if len(note.item_label) < len(matched.label) and "+info" in note.item_label.lower():
                matched.label = note.item_label
        else:
            clusters.append(
                InteractiveCluster(
                    number=len(clusters) + 1,
                    x=x,
                    y=y,
                    width=w,
                    height=h,
                    label=note.item_label,
                    notes=[note],
                )
            )

    return clusters, reveal_points


def build_detail_card_html(
    slide_number: int,
    slide_title: str,
    clusters: list[InteractiveCluster],
    slide_w: int,
    slide_h: int,
) -> str:
    """Genera un documento HTML limpio para renderizar la ficha interactiva vectorial en PDF."""
    cards_html: list[str] = []
    for cluster in clusters:
        notes_blocks: list[str] = []
        for note in cluster.notes:
            body = note.html_body or f"<p>{html.escape(note.plain_text)}</p>"
            notes_blocks.append(
                f"""
                <div class="note-block">
                  <div class="note-meta">{html.escape(note.kind_label)} · <span>{html.escape(note.trigger)}</span></div>
                  <div class="note-body">{body}</div>
                </div>
                """
            )
        cards_html.append(
            f"""
            <article class="card">
              <div class="card-head">
                <span class="badge">{cluster.number}</span>
                <span class="trigger-name">{html.escape(cluster.label)}</span>
              </div>
              {''.join(notes_blocks)}
            </article>
            """
        )

    title_clean = html.escape(slide_title or f"Diapositiva {slide_number}")
    return f"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8" />
<style>
  @page {{
    size: {slide_w * 0.75}pt {slide_h * 0.75}pt;
    margin: 22pt 26pt;
  }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0;
    font-family: "Segoe UI", Arial, sans-serif;
    color: #18181b;
    background: #ffffff;
    font-size: 11.5pt;
    line-height: 1.45;
  }}
  .page-header {{
    display: flex;
    justify-content: space-between;
    align-items: baseline;
    border-bottom: 2px solid #3d35d8;
    padding-bottom: 8pt;
    margin-bottom: 12pt;
  }}
  .kicker {{
    font-size: 9pt;
    font-weight: 700;
    letter-spacing: 0.04em;
    text-transform: uppercase;
    color: #3d35d8;
  }}
  .slide-title {{
    font-size: 14pt;
    font-weight: 750;
    margin: 2pt 0 0;
    color: #111827;
  }}
  .grid {{
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(260pt, 1fr));
    gap: 10pt;
  }}
  .card {{
    border: 1px solid #d4d4d8;
    border-radius: 8pt;
    padding: 10pt 12pt;
    background: #fafafa;
    break-inside: avoid;
    page-break-inside: avoid;
  }}
  .card-head {{
    display: flex;
    align-items: center;
    gap: 8pt;
    margin-bottom: 6pt;
    padding-bottom: 5pt;
    border-bottom: 1px solid #e4e4e7;
  }}
  .badge {{
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 18pt;
    height: 18pt;
    border-radius: 999px;
    background: #3d35d8;
    color: #ffffff;
    font-weight: 700;
    font-size: 10pt;
    flex-shrink: 0;
  }}
  .trigger-name {{
    font-weight: 700;
    font-size: 11pt;
    color: #1e1b4b;
  }}
  .note-block + .note-block {{
    margin-top: 7pt;
    padding-top: 6pt;
    border-top: 1px dashed #d4d4d8;
  }}
  .note-meta {{
    font-size: 8.5pt;
    font-weight: 600;
    color: #52525b;
    margin-bottom: 3pt;
  }}
  .note-body p {{ margin: 0 0 5pt; }}
  .note-body p:last-child {{ margin-bottom: 0; }}
  .note-body ul, .note-body ol {{ margin: 4pt 0; padding-left: 16pt; }}
  .note-body img {{
    max-width: 100%;
    max-height: 290pt;
    height: auto;
    border-radius: 4pt;
    border: 1px solid #e4e4e7;
    margin-top: 4pt;
    display: block;
  }}
  a {{ color: #3d35d8; word-break: break-all; }}
</style>
</head>
<body>
  <header class="page-header">
    <div>
      <div class="kicker">Contenido interactivo · Diapositiva {slide_number}</div>
      <div class="slide-title">{title_clean}</div>
    </div>
  </header>
  <section class="grid">
    {''.join(cards_html)}
  </section>
</body>
</html>"""


def build_ai_markdown_context(
    title: str,
    source_url: str,
    total_pdf_pages: int,
    slide_w: int,
    slide_h: int,
    records: list[SlideContextRecord],
) -> str:
    """Construye un documento Markdown estructurado listo para enviar a una IA junto al PDF."""
    lines: list[str] = [
        f"# {title}",
        "",
        f"- **Fuente Genially:** {source_url}",
        f"- **Diapositivas originales:** {len(records)}",
        f"- **Páginas totales del PDF:** {total_pdf_pages} (incluye diapositivas, contenido revelado y fichas interactivas)",
        f"- **Formato nativo:** {slide_w}×{slide_h}px",
        "",
        "> **Nota para la IA:** Este resumen acompaña al PDF adjunto de la clase. Cada diapositiva indica su número de página en el PDF (especialmente útil cuando contiene diagramas o esquemas visuales) junto con el texto visible, los elementos ocultos revelados (`+ Info`) y todas las notas/tooltips interactivos.",
        "",
        "---",
        "",
    ]

    for rec in records:
        heading = rec.slide_title or f"Diapositiva {rec.slide_number}"
        lines.append(f"## Diapositiva {rec.slide_number}: {heading}")
        lines.append(f"- **Página en el PDF:** {rec.pdf_page_number}")
        if rec.image_count > 0:
            lines.append(
                f"- **Elementos visuales / diagramas:** {rec.image_count} imagen(es) en esta lámina (revisar página {rec.pdf_page_number} del PDF para ver la notación gráfica exacta)."
            )

        lines.append("")
        if rec.visible_text.strip():
            lines.append("**Texto visible en la diapositiva:**")
            lines.append(rec.visible_text.strip())
        else:
            lines.append("*Diapositiva principalmente visual / diagrama sin texto HTML superpuesto.*")

        if rec.revealed_texts:
            pages_str = ", ".join(str(p) for p in rec.revealed_pdf_pages)
            lines.append("")
            lines.append(f"**Contenido oculto revelado al hacer clic (PDF pág. {pages_str}):**")
            for idx, rtext in enumerate(rec.revealed_texts, start=1):
                if rtext.strip():
                    lines.append(f"- Estado revelado {idx}: {rtext.strip()}")

        if rec.clusters:
            detail_str = ", ".join(str(p) for p in rec.detail_pdf_pages)
            lines.append("")
            meta = f" (PDF pág. {detail_str})" if detail_str else ""
            lines.append(f"**Notas interactivas / tooltips{meta}:**")
            for cluster in rec.clusters:
                for note in cluster.notes:
                    txt = note.plain_text.strip() or "(Contenido visual)"
                    lines.append(f"- **[{cluster.number}] {cluster.label}** ({note.kind_label}): {txt}")
                    for img in note.image_urls:
                        lines.append(f"  - Imagen en nota: {img}")
                    for lnk in note.link_urls:
                        if lnk not in txt:
                            lines.append(f"  - Enlace: {lnk}")

        lines.append("")
        lines.append("---")
        lines.append("")

    return "\n".join(lines).strip() + "\n"
