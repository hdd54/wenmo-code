# -*- coding: utf-8 -*-
"""Extract content from a pptx file: list slides, extract text, dump media images."""
import zipfile
import re
import os
import sys
import shutil

def main():
    # find the pptx on desktop (avoid encoding issues by scanning)
    candidates = []
    desktop = os.path.join(os.path.expanduser("~"), "Desktop")
    if os.path.isdir(desktop):
        for f in os.listdir(desktop):
            if f.lower().endswith(".pptx"):
                candidates.append(os.path.join(desktop, f))
    print("FOUND PPTX FILES:")
    for c in candidates:
        print("  " + c)

    if not candidates:
        print("NO PPTX FOUND")
        return

    p = candidates[0]
    print("\nUSING: " + p)
    print("SIZE:", os.path.getsize(p))

    z = zipfile.ZipFile(p)
    names = z.namelist()
    print("\nTOTAL ENTRIES:", len(names))

    media = [n for n in names if n.startswith("ppt/media")]
    print("MEDIA COUNT:", len(media))
    outdir = r"C:\Users\Public\pptx_media"
    if os.path.isdir(outdir):
        shutil.rmtree(outdir)
    os.makedirs(outdir, exist_ok=True)
    for m in media:
        info = z.getinfo(m)
        data = z.read(m)
        fname = os.path.basename(m)
        with open(os.path.join(outdir, fname), "wb") as f:
            f.write(data)
        print("  EXTRACTED:", fname, info.file_size)

    print("\nSLIDE TEXT:")
    for n in sorted(names):
        m = re.match(r"ppt/slides/slide(\d+)\.xml$", n)
        if m:
            xml = z.read(n).decode("utf-8", "ignore")
            texts = re.findall(r"<a:t>([^<]*)</a:t>", xml)
            txts = [t.strip() for t in texts if t.strip()]
            rels_match = re.findall(r'<a:blip r:embed="([^"]+)"', xml)
            has_pic = bool(re.search(r"<pic:pic|<a:blip", xml))
            print("slide %s: text=%s pic=%s rels=%s" % (m.group(1), txts[:15] if txts else "NONE", has_pic, rels_match))

    # notes
    notes = [n for n in names if "notesSlide" in n]
    print("\nNOTES:", len(notes))

    print("\nDONE")

if __name__ == "__main__":
    main()
