# debug_regions.py
# Run: python debug_regions.py ./build/main.pdf
# Or import and run from Jupyter.

import sys, re, json
import fitz  # PyMuPDF
from pathlib import Path

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
                    regions[bid] = {"page": pno, "rect": fitz.Rect(r), "text": txt.strip(), "type": k, "ordinal": int(n)}
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
                        if (em.group(1)+":"+em.group(2))==bid:
                            txt = page.get_textbox(union) or ""
                            txt = BEGIN_RE.sub("", txt)
                            txt = END_RE.sub("", txt)
                            k,n = bid.split(":")
                            regions[bid] = {"page": pno, "rect": fitz.Rect(union), "text": txt.strip(), "type": k, "ordinal": int(n)}
                            order.append(bid)
                            found = True
                            break
                    if found: break
                    j += 1
            i += 1
    return regions, order

def export_debug_png(doc, regions, page_index=0, zoom=2.0, out_path=None):
    """
    If out_path is None, write next to the PDF:
      build/debug_overlay_page{page}.png
    """
    # decide output
    if out_path is None:
        out_file = PDF_PATH_DEFAULT.parent / f"debug_overlay_page{page_index+1}.png"
    else:
        out_file = Path(out_path)
    out_file.parent.mkdir(parents=True, exist_ok=True)

    # render one-page temp and draw boxes
    mat = fitz.Matrix(zoom, zoom)
    tmp = fitz.open()
    tmp.insert_pdf(doc, from_page=page_index, to_page=page_index)
    p = tmp[0]

    for bid, info in regions.items():
        if info["page"] != page_index:
            continue
        r = info["rect"]
        a = p.add_rect_annot(r)
        a.set_colors(stroke=(1, 0, 0))
        a.set_border(width=1)
        a.update()

    pix = p.get_pixmap(matrix=mat, alpha=False)
    pix.save(str(out_file))
    tmp.close()
    print(f"Wrote {out_file}")

if __name__ == "__main__":
    if not PDF_PATH_DEFAULT.exists():
        raise FileNotFoundError(f"PDF not found: {PDF_PATH_DEFAULT}")

    doc = fitz.open(str(PDF_PATH_DEFAULT))
    regions, order = parse_regions(doc)

    print(f"Found {len(regions)} regions across {len(doc)} pages.")
    for bid in sorted(order, key=lambda x: (x.split(':')[0], int(x.split(':')[1]))):
        info = regions[bid]
        r = info["rect"]
        print(f"{bid:>6}  page={info['page']+1}  rect=({r.x0:.1f},{r.y0:.1f},{r.x1:.1f},{r.y1:.1f})  text_len={len(info['text'])}")

    # Make a PNG for page 1 in build/
    export_debug_png(doc, regions, page_index=0, zoom=2.0)

