"""Prévia interativa: mostra o layout final e permite arrastar legenda, logo e divisória."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import (
    QColor, QFontMetricsF, QImage, QPainter, QPainterPath, QPen,
)
from PySide6.QtWidgets import QSizePolicy, QWidget

from ..core.ass import clean_text, word_timings
from ..core.ffmpeg import layout_regions
from ..core.models import Cut, Project, SubtitleLine
from .fonts import subtitle_font

PLACEHOLDER = "Sua legenda aparece aqui"


class PreviewWidget(QWidget):
    subtitleMoved = Signal(float, float)   # pos_x, pos_y (0..1)
    logoMoved = Signal(int, int)           # x, y (px)
    splitMoved = Signal(float)             # 0..1
    realPreviewCleared = Signal()

    SNAP = 14  # px na tela

    def __init__(self, parent=None):
        super().__init__(parent)
        self.project: Project | None = None
        self.cut: Cut | None = None
        self.time = 0.0
        self.frames: dict[int, QImage | None] = {}
        self.real_frame: QImage | None = None
        self._logo = ("", None)
        self._drag = None
        self._hover = None
        self._guides: list[tuple[str, float]] = []
        self._sub_rect = QRectF()
        self._logo_rect = QRectF()
        self.setMouseTracking(True)
        self.setMinimumSize(220, 260)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

    # ---- estado ---------------------------------------------------------
    def set_project(self, project: Project):
        self.project = project
        self.frames = {}
        self.clear_real()

    def set_cut(self, cut: Cut | None):
        self.cut = cut
        self.time = 0.0
        self.clear_real()

    def set_time(self, t: float):
        self.time = t
        self.clear_real()

    def set_frames(self, frames: dict):
        self.frames = frames
        self.update()

    def set_real_frame(self, img: QImage | None):
        self.real_frame = img
        self.update()

    def clear_real(self):
        if self.real_frame is not None:
            self.real_frame = None
            self.realPreviewCleared.emit()
        self.update()

    # ---- geometria ------------------------------------------------------
    def _out_size(self) -> tuple[int, int]:
        return self.project.layout.size()

    def _canvas(self) -> tuple[QRectF, float]:
        W, H = self._out_size()
        avail = QRectF(self.rect()).adjusted(10, 10, -10, -10)
        s = max(0.01, min(avail.width() / W, avail.height() / H))
        w, h = W * s, H * s
        return QRectF(avail.center().x() - w / 2, avail.center().y() - h / 2, w, h), s

    def _to_out(self, pos: QPointF) -> QPointF:
        c, s = self._canvas()
        return QPointF((pos.x() - c.x()) / s, (pos.y() - c.y()) / s)

    def _regions(self):
        return layout_regions(self.project.layout, bool(self.project.cam2))

    def _seam(self):
        """('h', y) para empilhado, ('v', x) para lado a lado, ou None."""
        r = self._regions()
        if len(r) != 2:
            return None
        return ("h", r[1][2]) if r[1][2] > 0 else ("v", r[1][1])

    # ---- desenho --------------------------------------------------------
    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        p.fillRect(self.rect(), QColor("#1c1c20"))
        if not self.project:
            return
        canvas, s = self._canvas()
        W, H = self._out_size()

        if self.real_frame is not None and not self.real_frame.isNull():
            p.drawImage(canvas, self.real_frame)
            self._badge(p, canvas, "PRÉVIA REAL (ffmpeg) — clique para voltar à edição")
            return

        p.save()
        p.translate(canvas.topLeft())
        p.scale(s, s)
        p.setClipRect(QRectF(0, 0, W, H))
        p.fillRect(QRectF(0, 0, W, H), Qt.black)
        for cam, x, y, w, h in self._regions():
            self._draw_cover(p, self.frames.get(cam), QRectF(x, y, w, h), cam)
        self._draw_logo(p)
        if self.project.subtitle.enabled:
            self._draw_subtitle(p)
        else:
            self._sub_rect = QRectF()

        seam = self._seam()
        if seam and (self._hover == "split" or (self._drag and self._drag[0] == "split")):
            p.setPen(QPen(QColor(0, 200, 255), 3 / s))
            if seam[0] == "h":
                p.drawLine(QPointF(0, seam[1]), QPointF(W, seam[1]))
            else:
                p.drawLine(QPointF(seam[1], 0), QPointF(seam[1], H))
        if self._guides:
            pen = QPen(QColor(255, 60, 160), 2 / s, Qt.DashLine)
            p.setPen(pen)
            for kind, v in self._guides:
                if kind == "v":
                    p.drawLine(QPointF(v, 0), QPointF(v, H))
                else:
                    p.drawLine(QPointF(0, v), QPointF(W, v))
        if self._hover == "sub" or (self._drag and self._drag[0] == "sub"):
            p.setPen(QPen(QColor(255, 255, 255, 140), 1.5 / s, Qt.DashLine))
            p.setBrush(Qt.NoBrush)
            p.drawRect(self._sub_rect)
        if self._hover == "logo" or (self._drag and self._drag[0] == "logo"):
            p.setPen(QPen(QColor(255, 255, 255, 140), 1.5 / s, Qt.DashLine))
            p.setBrush(Qt.NoBrush)
            p.drawRect(self._logo_rect)
        p.restore()

        p.setPen(QPen(QColor(255, 255, 255, 50), 1))
        p.setBrush(Qt.NoBrush)
        p.drawRect(canvas)
        p.setPen(QColor(200, 200, 200, 160))
        p.drawText(QRectF(canvas.x(), canvas.bottom() - 18, canvas.width() - 6, 16),
                   Qt.AlignRight | Qt.AlignVCenter, f"{W}×{H}")

    def _badge(self, p: QPainter, canvas: QRectF, text: str):
        fm = p.fontMetrics()
        r = QRectF(canvas.x() + 8, canvas.y() + 8, fm.horizontalAdvance(text) + 16, fm.height() + 8)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(0, 0, 0, 170))
        p.drawRoundedRect(r, 6, 6)
        p.setPen(QColor(255, 255, 255))
        p.drawText(r, Qt.AlignCenter, text)

    def _draw_cover(self, p: QPainter, img: QImage | None, rect: QRectF, cam: int):
        if img is None or img.isNull():
            p.fillRect(rect, QColor("#2b2b33") if cam == 1 else QColor("#33302b"))
            f = p.font()
            f.setPixelSize(max(12, int(min(rect.width(), rect.height()) * 0.08)))
            p.setFont(f)
            p.setPen(QColor(150, 150, 160))
            p.drawText(rect, Qt.AlignCenter, f"Cam {cam}")
            return
        k = max(rect.width() / img.width(), rect.height() / img.height())
        sw, sh = rect.width() / k, rect.height() / k
        src = QRectF((img.width() - sw) / 2, (img.height() - sh) / 2, sw, sh)
        p.drawImage(rect, img, src)

    def _logo_image(self) -> QImage | None:
        path = self.project.logo.path
        if self._logo[0] != path:
            img = QImage(path) if path and Path(path).is_file() else None
            self._logo = (path, img if img is not None and not img.isNull() else None)
        return self._logo[1]

    def _draw_logo(self, p: QPainter):
        lg = self.project.logo
        img = self._logo_image()
        if not lg.enabled or img is None:
            self._logo_rect = QRectF()
            return
        h = lg.width * img.height() / img.width()
        self._logo_rect = QRectF(lg.x, lg.y, lg.width, h)
        p.save()
        p.setOpacity(max(0.0, min(1.0, lg.opacity)))
        p.drawImage(self._logo_rect, img)
        p.restore()

    def _active_line(self) -> SubtitleLine | None:
        if not self.cut:
            return None
        for line in self.cut.subtitles:
            if line.start <= self.time < line.end:
                return line
        return None

    def _subtitle_layout(self):
        st = self.project.subtitle
        W, H = self._out_size()
        line = self._active_line()
        placeholder = line is None
        text = clean_text(line.text if line else PLACEHOLDER, st)
        timings = None
        if line:
            timings = word_timings(SubtitleLine(line.start, line.end, text, line.words))
        font = subtitle_font(st)
        fm = QFontMetricsF(font)
        space = fm.horizontalAdvance(" ")
        maxw = st.max_width * W
        rows: list[list[tuple[str, float]]] = [[]]
        widths = [0.0]
        for w in text.split():
            ww = fm.horizontalAdvance(w)
            if rows[-1] and widths[-1] + space + ww > maxw:
                rows.append([])
                widths.append(0.0)
            if rows[-1]:
                widths[-1] += space
            rows[-1].append((w, widths[-1]))
            widths[-1] += ww
        lh = fm.ascent() + fm.descent()
        bw, bh = max(widths), lh * len(rows)
        col = (st.anchor - 1) % 3          # 0 esq, 1 centro, 2 dir
        row = (st.anchor - 1) // 3         # 0 base, 1 meio, 2 topo
        ax, ay = st.pos_x * W, st.pos_y * H
        left = ax - bw * (0, 0.5, 1)[col]
        top = ay - bh * (1, 0.5, 0)[row]
        paths = []
        for i, words in enumerate(rows):
            lx = left + (bw - widths[i]) * (0, 0.5, 1)[col]
            base = top + i * lh + fm.ascent()
            for w, x in words:
                path = QPainterPath()
                path.addText(lx + x, base, font, w)
                paths.append(path)
        pad = st.outline + 4
        return paths, QRectF(left, top, bw, bh).adjusted(-pad, -pad, pad, pad), timings, placeholder

    def _fill_fraction(self, i: int, timings, placeholder: bool, n: int) -> float:
        if placeholder:
            return 1.0 if i < max(1, n // 2) else 0.0
        if not timings or i >= len(timings):
            return 0.0
        ws, we, _ = timings[i]
        if self.time >= we:
            return 1.0
        if self.time <= ws:
            return 0.0
        return (self.time - ws) / max(1e-6, we - ws)

    def _draw_subtitle(self, p: QPainter):
        st = self.project.subtitle
        paths, rect, timings, placeholder = self._subtitle_layout()
        self._sub_rect = rect
        p.save()
        if placeholder:
            p.setOpacity(0.55)
        outline_c = QColor(st.outline_color)
        if st.shadow > 0:
            for path in paths:
                sp = path.translated(st.shadow, st.shadow)
                if st.outline > 0:
                    p.strokePath(sp, QPen(outline_c, st.outline * 2, Qt.SolidLine, Qt.RoundCap,
                                          Qt.RoundJoin))
                p.fillPath(sp, outline_c)
        if st.outline > 0:
            pen = QPen(outline_c, st.outline * 2, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
            for path in paths:
                p.strokePath(path, pen)
        text_c, hl_c = QColor(st.text_color), QColor(st.highlight_color)
        for i, path in enumerate(paths):
            p.fillPath(path, text_c)
            if st.karaoke:
                frac = self._fill_fraction(i, timings, placeholder, len(paths))
                if frac > 0:
                    br = path.boundingRect()
                    p.save()
                    p.setClipRect(QRectF(br.x() - 2, br.y() - 2, (br.width() + 4) * frac,
                                         br.height() + 4), Qt.IntersectClip)
                    p.fillPath(path, hl_c)
                    p.restore()
        p.restore()

    # ---- interação ------------------------------------------------------
    def _hit(self, pt: QPointF) -> str | None:
        if self.project.subtitle.enabled and self._sub_rect.contains(pt):
            return "sub"
        if not self._logo_rect.isNull() and self._logo_rect.contains(pt):
            return "logo"
        seam = self._seam()
        if seam:
            _, s = self._canvas()
            v = pt.y() if seam[0] == "h" else pt.x()
            if abs(v - seam[1]) < 8 / s:
                return "split"
        return None

    def mousePressEvent(self, e):
        if not self.project or e.button() != Qt.LeftButton:
            return
        if self.real_frame is not None:
            self.clear_real()
            return
        pt = self._to_out(e.position())
        hit = self._hit(pt)
        if hit == "sub":
            W, H = self._out_size()
            st = self.project.subtitle
            self._drag = ("sub", pt.x() - st.pos_x * W, pt.y() - st.pos_y * H)
        elif hit == "logo":
            self._drag = ("logo", pt.x() - self.project.logo.x, pt.y() - self.project.logo.y)
        elif hit == "split":
            self._drag = ("split",)
        if self._drag:
            self.setCursor(Qt.ClosedHandCursor if hit != "split" else self.cursor())
            self.update()

    def _snap(self, value: float, targets: list[float], kind: str) -> float:
        _, s = self._canvas()
        for t in targets:
            if abs(value - t) < self.SNAP / s:
                self._guides.append((kind, t))
                return t
        return value

    def mouseMoveEvent(self, e):
        if not self.project:
            return
        pt = self._to_out(e.position())
        W, H = self._out_size()
        if not self._drag:
            hit = self._hit(pt) if self.real_frame is None else None
            if hit != self._hover:
                self._hover = hit
                seam = self._seam()
                cursors = {"sub": Qt.OpenHandCursor, "logo": Qt.OpenHandCursor,
                           "split": Qt.SplitVCursor if seam and seam[0] == "h" else Qt.SplitHCursor}
                self.setCursor(cursors.get(hit, Qt.ArrowCursor))
                self.update()
            return
        self._guides = []
        kind = self._drag[0]
        if kind == "sub":
            x = self._snap(pt.x() - self._drag[1], [W / 2], "v")
            seam = self._seam()
            ys = [H / 2] + ([seam[1]] if seam and seam[0] == "h" else [])
            y = self._snap(pt.y() - self._drag[2], ys, "h")
            self.subtitleMoved.emit(min(1.0, max(0.0, x / W)), min(1.0, max(0.0, y / H)))
        elif kind == "logo":
            x = round(pt.x() - self._drag[1])
            y = round(pt.y() - self._drag[2])
            self.logoMoved.emit(x, y)
        elif kind == "split":
            seam = self._seam()
            if seam:
                frac = pt.y() / H if seam[0] == "h" else pt.x() / W
                if abs(frac - 0.5) < 0.015:
                    frac = 0.5
                    self._guides.append((seam[0], (H if seam[0] == "h" else W) / 2))
                self.splitMoved.emit(round(min(0.85, max(0.15, frac)), 3))
        self.update()

    def mouseReleaseEvent(self, _e):
        if self._drag:
            self._drag = None
            self._guides = []
            self._hover = None
            self.setCursor(Qt.ArrowCursor)
            self.update()

    def leaveEvent(self, _e):
        if not self._drag and self._hover:
            self._hover = None
            self.update()
