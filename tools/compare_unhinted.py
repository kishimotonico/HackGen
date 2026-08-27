#!/usr/bin/env python3
import argparse
import json
from pathlib import Path

import freetype
from fontTools.ttLib import TTFont

SIZE = 24
LOAD_FLAGS = freetype.FT_LOAD_NO_HINTING | freetype.FT_LOAD_NO_AUTOHINT


def find_fonts(root):
    return {p.name: p for p in Path(root).rglob("HackGen*.ttf")}


def glyph_bytes(font, name):
    return font["glyf"][name].compile(font["glyf"])


def render(face, cp):
    face.set_pixel_sizes(0, SIZE)
    idx = face.get_char_index(cp)
    if not idx:
        return None
    face.load_glyph(idx, LOAD_FLAGS)
    advance = face.glyph.advance.x
    face.glyph.render(freetype.FT_RENDER_MODE_NORMAL)
    slot = face.glyph
    bmp = slot.bitmap
    pixels = {}
    buf = bytes(bmp.buffer)
    pitch = bmp.pitch
    for row in range(bmp.rows):
        src = row if pitch >= 0 else bmp.rows - 1 - row
        base = src * abs(pitch)
        y = slot.bitmap_top - row - 1
        for col in range(bmp.width):
            v = buf[base + col]
            if v:
                pixels[(slot.bitmap_left + col, y)] = v
    return pixels, advance


def compare(a, b):
    if a is None or b is None:
        return {"exact": a is b, "missing": True}
    pa, aa = a
    pb, ab = b
    keys = set(pa) | set(pb)
    total = sum(abs(pa.get(k, 0) - pb.get(k, 0)) for k in keys)
    changed = sum(pa.get(k, 0) != pb.get(k, 0) for k in keys)
    return {
        "exact": changed == 0 and aa == ab,
        "missing": False,
        "changed_pixels": changed,
        "union_pixels": len(keys),
        "changed_fraction": changed / len(keys) if keys else 0.0,
        "mean_abs_gray_delta": total / len(keys) if keys else 0.0,
        "advance_same": aa == ab,
    }


def compare_font(gp, rp):
    gf = TTFont(str(gp), lazy=False)
    rf = TTFont(str(rp), lazy=False)
    gc = gf.getBestCmap() or {}
    rc = rf.getBestCmap() or {}
    common = sorted(set(gc) & set(rc))
    candidates = []
    for cp in common:
        gn, rn = gc[cp], rc[cp]
        try:
            raw_diff = glyph_bytes(gf, gn) != glyph_bytes(rf, rn)
        except Exception:
            raw_diff = True
        metric_diff = gf["hmtx"].metrics.get(gn) != rf["hmtx"].metrics.get(rn)
        if raw_diff or metric_diff or gn != rn:
            candidates.append(cp)
    gf.close(); rf.close()

    ga = freetype.Face(str(gp)); ra = freetype.Face(str(rp))
    diffs = []
    for cp in candidates:
        d = compare(render(ga, cp), render(ra, cp))
        if not d["exact"]:
            diffs.append({"codepoint": cp, **d})
    diffs.sort(key=lambda x: (x.get("mean_abs_gray_delta", 0), x.get("changed_fraction", 0)), reverse=True)
    return {
        "file": gp.name,
        "mapped": len(common),
        "candidates": len(candidates),
        "unhinted_diff": len(diffs),
        "unhinted_exact_all_fraction": (len(common) - len(diffs)) / len(common) if common else 1.0,
        "mean_gray_ge_1": sum(d.get("mean_abs_gray_delta", 0) >= 1 for d in diffs),
        "mean_gray_ge_5": sum(d.get("mean_abs_gray_delta", 0) >= 5 for d in diffs),
        "advance_diff": sum(not d.get("advance_same", True) for d in diffs),
        "worst": diffs[:25],
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--generated", required=True)
    ap.add_argument("--reference", required=True)
    ap.add_argument("--json", default="unhinted-comparison.json")
    ap.add_argument("--markdown", default="unhinted-comparison.md")
    a = ap.parse_args()
    gen, ref = find_fonts(a.generated), find_fonts(a.reference)
    results = [compare_font(gen[n], ref[n]) for n in sorted(set(gen) & set(ref))]
    Path(a.json).write_text(json.dumps({"size": SIZE, "results": results}, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = ["# Unhinted 24px comparison", "", "| Font | candidates | unhinted diff | exact all | mean gray >=1 | >=5 | advance Δ |", "| --- | ---: | ---: | ---: | ---: | ---: | ---: |"]
    for r in results:
        lines.append("| %s | %d | %d | %.3f%% | %d | %d | %d |" % (r["file"], r["candidates"], r["unhinted_diff"], r["unhinted_exact_all_fraction"]*100, r["mean_gray_ge_1"], r["mean_gray_ge_5"], r["advance_diff"]))
    lines.append("")
    for r in results:
        lines += ["## %s" % r["file"], ""]
        for d in r["worst"][:10]:
            lines.append("- U+%04X: changed %.2f%%, mean-gray %.3f/255, advance-same=%s" % (d["codepoint"], d.get("changed_fraction",0)*100, d.get("mean_abs_gray_delta",0), d.get("advance_same")))
        lines.append("")
    Path(a.markdown).write_text("\n".join(lines)+"\n", encoding="utf-8")
    print("\n".join(lines[:30]))

if __name__ == "__main__":
    main()
