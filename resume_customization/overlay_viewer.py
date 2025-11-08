# overlay_viewer.py — robust clicks + fit modes, resize-aware

import sys, re, json
import fitz  # PyMuPDF
from pathlib import Path

from PySide6.QtWidgets import (
    QApplication, QWidget, QLabel, QVBoxLayout, QFileDialog, QMessageBox, QComboBox, QHBoxLayout
)
from PySide6.QtGui import (
    QImage, QPixmap, QPainter, QPen, QColor, QAction, QKeySequence, QCursor
)
from PySide6.QtCore import Qt, QRectF, QEvent, QSize

BASE_DIR = Path(__file__).resolve().parent
PDF_PATH_DEFAULT = BASE_DIR / "build" / "main.pdf"

BEGIN_RE = re.compile(r"\[BEGIN\s+(exp|pr|sk):(\d+)\]")
END_RE   = re.compile(r"\[END\s+(exp|pr|sk):(\d+)\]")

def get_blocks(page):
    out = []
    for x0,y0,x1,y1,txt,*_ in page.get_text("blocks"):
        if txt and txt.strip():
            out.append((fitz.Rect(x0,y0,x1,y1), txt.strip()))
    return out

def parse_regions(doc):
    regions, order = {}, []
    for pno in range(len(doc)):
        page = doc[pno]
        blocks = get_blocks(page)
        i = 0
        while i < len(blocks):
            r, t = blocks[i]
            begins = list(BEGIN_RE.finditer(t))
            ends   = list(END_RE.finditer(t))

            # same-block pairs
            if begins and ends:
                b_ids = {m.group(1)+":"+m.group(2) for m in begins}
                e_ids = {m.group(1)+":"+m.group(2) for m in ends}
                for bid in b_ids & e_ids:
                    txt = page.get_textbox(r) or ""
                    txt = BEGIN_RE.sub("", txt)
                    txt = END_RE.sub("", txt)
                    k,n = bid.split(":")
                    regions[bid] = {
                        "page": pno, "rect": fitz.Rect(r), "text": txt.strip(),
                        "type": k, "ordinal": int(n)
                    }
                    order.append(bid)

            # cross-block pairs
            for bm in begins:
                bid = bm.group(1)+":"+bm.group(2)
                if any((em.group(1)+":"+em.group(2))==bid for em in ends):
                    continue
                union = fitz.Rect(r)
                j, found = i+1, False
                while j < len(blocks):
                    r2, t2 = blocks[j]
                    union |= r2
                    for em in END_RE.finditer(t2):
                        if (em.group(1)+":"+em.group(2)) == bid:
                            txt = page.get_textbox(union) or ""
                            txt = BEGIN_RE.sub("", txt)
                            txt = END_RE.sub("", txt)
                            k,n = bid.split(":")
                            regions[bid] = {
                                "page": pno, "rect": fitz.Rect(union), "text": txt.strip(),
                                "type": k, "ordinal": int(n)
                            }
                            order.append(bid)
                            found = True
                            break
                    if found: break
                    j += 1
            i += 1
    return regions, order

class PDFOverlay(QWidget):
    def __init__(self, pdf_path=PDF_PATH_DEFAULT, fit_mode="fit_height"):
        super().__init__()

        pdf_path = Path(pdf_path)
        if not pdf_path.exists():
            QMessageBox.critical(self, "Missing PDF", f"PDF not found:\n{pdf_path}")
            raise SystemExit(1)

        self.pdf_path = str(pdf_path)
        self.doc = fitz.open(self.pdf_path)
        self.regions, self.order = parse_regions(self.doc)
        self.page_index = 0
        self.fit_mode = fit_mode  # "natural" | "fit_width" | "fit_height"

        # UI
        self.image_label = QLabel()
        self.image_label.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        self.image_label.setMouseTracking(True)
        self.image_label.installEventFilter(self)
        # make sure label can grow and we can compute available area
        self.image_label.setMinimumSize(QSize(100, 100))

        self.mode_box = QComboBox()
        self.mode_box.addItems(["natural", "fit_width", "fit_height"])
        self.mode_box.setCurrentText(self.fit_mode)
        self.mode_box.currentTextChanged.connect(self._change_mode)

        top = QHBoxLayout()
        top.addWidget(QLabel("View:"))
        top.addWidget(self.mode_box)
        top.addStretch()

        lay = QVBoxLayout(self)
        lay.setContentsMargins(6,6,6,6)
        lay.addLayout(top)
        lay.addWidget(self.image_label)

        # shortcuts
        self._add_action("Next page", QKeySequence.MoveToNextPage, self.next_page)
        self._add_action("Prev page", QKeySequence.MoveToPreviousPage, self.prev_page)
        self._add_action("Open PDF…", QKeySequence.Open, self.open_pdf)
        self._add_action("Export regions JSON", QKeySequence.Save, self.export_regions)

        if not self.regions:
            print("No regions found. Make sure [BEGIN ...]/[END ...] markers exist.")

        self.render_page()

    def _add_action(self, text, shortcut, handler):
        act = QAction(text, self)
        act.setShortcut(shortcut)
        act.triggered.connect(handler)
        self.addAction(act)

    # ----- rendering helpers -----
    def _scale_for_mode(self, page):
        rect = page.rect  # PDF points (72 dpi)
        # available area inside the label (or window if label not yet sized)
        avail_w = max(50, self.image_label.width())
        avail_h = max(50, self.image_label.height())
        # If label hasn't been laid out yet, fall back to a portion of screen
        if avail_w <= 50 or avail_h <= 50:
            screen = QApplication.primaryScreen().availableGeometry()
            avail_w = int(screen.width() * 0.9)
            avail_h = int(screen.height() * 0.9)

        if self.fit_mode == "fit_width":
            s = avail_w / rect.width
        elif self.fit_mode == "fit_height":
            s = avail_h / rect.height
        else:
            s = 1.5  # natural: ~108 dpi
        # Don’t let scale fall below something too tiny:
        s = max(0.2, s)
        return fitz.Matrix(s, s), s

    def render_page(self):
        page = self.doc[self.page_index]
        mat, s = self._scale_for_mode(page)
        pix = page.get_pixmap(matrix=mat, alpha=False)

        # QImage must own its buffer; .copy() ensures lifetime
        fmt = QImage.Format_RGB888
        img = QImage(pix.samples, pix.width, pix.height, pix.stride, fmt).copy()
        pm = QPixmap.fromImage(img)
        pm.setDevicePixelRatio(1.0)  # keep coordinate system 1:1 with our math

        # draw overlays
        base = QPixmap(pm)
        qp = QPainter(base)
        pen = QPen(QColor(0, 0, 0), max(1, int(2*s)))
        qp.setPen(pen)
        fill = QColor(0, 0, 0, 40)
        for bid, info in self.regions.items():
            if info["page"] != self.page_index:
                continue
            r = info["rect"]
            rr = QRectF(r.x0*s, r.y0*s, r.width*s, r.height*s)
            qp.fillRect(rr, fill)
            qp.drawRect(rr)
        qp.end()

        self.image_label.setPixmap(base)
        # Auto-size window to content (with some chrome space)
        self.resize(base.width() + 40, base.height() + 120)
        self.setWindowTitle(f"Resume Overlay — page {self.page_index+1}/{len(self.doc)}")

    # Re-render when the window/layout changes so fit_* stays accurate
    def resizeEvent(self, e):
        super().resizeEvent(e)
        if self.fit_mode in ("fit_width", "fit_height"):
            self.render_page()

    # ----- events -----
    def eventFilter(self, obj, event):
        if obj is self.image_label:
            if event.type() == QEvent.MouseMove:
                pos = event.position()
                self.image_label.setCursor(QCursor(Qt.PointingHandCursor if self._hit(pos) else Qt.ArrowCursor))
            elif event.type() == QEvent.MouseButtonPress and event.button() == Qt.LeftButton:
                pos = event.position()
                self._click(pos)
        return super().eventFilter(obj, event)

    def _hit(self, pos):
        page = self.doc[self.page_index]
        _, s = self._scale_for_mode(page)
        x, y = pos.x(), pos.y()
        for info in self.regions.values():
            if info["page"] != self.page_index:
                continue
            r = info["rect"]
            if (r.x0*s) <= x <= (r.x1*s) and (r.y0*s) <= y <= (r.y1*s):
                return True
        return False

    def _click(self, pos):
        page = self.doc[self.page_index]
        _, s = self._scale_for_mode(page)
        x, y = pos.x(), pos.y()
        for bid, info in self.regions.items():
            if info["page"] != self.page_index:
                continue
            r = info["rect"]
            if (r.x0*s) <= x <= (r.x1*s) and (r.y0*s) <= y <= (r.y1*s):
                txt = info["text"]
                QApplication.clipboard().setText(txt)
                print(f"\n=== CLICKED {bid} ===\n{txt}\n")
                self.setWindowTitle(f"Copied: {bid}")
                break

    # ----- ui actions -----
    def _change_mode(self, m):
        self.fit_mode = m
        self.render_page()

    def next_page(self):
        if self.page_index < len(self.doc) - 1:
            self.page_index += 1
            self.render_page()

    def prev_page(self):
        if self.page_index > 0:
            self.page_index -= 1
            self.render_page()

    def open_pdf(self):
        path, _ = QFileDialog.getOpenFileName(self, "Open PDF", str(BASE_DIR), "PDF Files (*.pdf)")
        if not path:
            return
        try:
            self.doc.close()
            self.doc = fitz.open(path)
            self.pdf_path = path
            self.regions, self.order = parse_regions(self.doc)
            self.page_index = 0
            self.render_page()
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to open PDF:\n{e}")

    def export_regions(self):
        data = {
            bid: {
                "type": v["type"], "ordinal": v["ordinal"], "page": v["page"],
                "rect": [v["rect"].x0, v["rect"].y0, v["rect"].x1, v["rect"].y1]
            }
            for bid, v in self.regions.items()
        }
        out = str(Path(self.pdf_path).with_suffix(Path(self.pdf_path).suffix + ".regions.json"))
        with open(out, "w") as f:
            json.dump(data, f, indent=2)
        self.setWindowTitle(f"Exported → {out}")
        print(f"Wrote {out}")

def main():
    app = QApplication(sys.argv)
    w = PDFOverlay(pdf_path=PDF_PATH_DEFAULT, fit_mode="fit_height")
    w.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
