#!/usr/bin/env python3
import argparse
import json
from pathlib import Path

import freetype
from fontTools.ttLib import TTFont

SIZES = (12, 16, 24, 32, 48)
MODES = {
    "hinted": freetype.FT_LOAD_DEFAULT | freetype.FT_LOAD_TARGET_NORMAL,
    "unhinted": freetype.FT_LOAD_NO_HINTING | freetype.FT_LOAD_NO_AUTOHINT,
}
THRESHOLDS = (0.5, 1.0, 2.0, 5.0, 10.0)


def find_fonts(root):
    return {p.name: p for p in Path(root).rglob("HackGen*.ttf")}


def glyph_bytes(font, name):
    glyf = font["glyf"]
    return glyf[name].compile(glyf)


def render(face, codepoint, size, load_flags):
    face.set_pixel_sizes(0, size)
    index = face.get_char_index(codepoint)
    if not index:
        return None
    face.load_glyph(index, load_flags)
    advance = face.glyph.advance.x
    face.glyph.render(freetype.FT_RENDER_MODE_NORMAL)
    slot = face.glyph
    bmp = slot.bitmap
    width, rows, pitch = bmp.width, bmp.rows, bmp.pitch
    buf = bytes(bmp.buffer)
    pixels = {}
    if width and rows:
        abs_pitch = abs(pitch)
        for row in range(rows):
            src_row = row if pitch >= 0 else rows - 1 - row
            base = src_row * abs_pitch
            y = slot.bitmap_top - row - 1
            for col in range(width):
                value = buf[base + col]
                if value:
                    pixels[(slot.bitmap_left + col, y)] = value
    bbox = None
    if pixels:
        xs = [p[0] for p in pixels]
        ys = [p[1] for p in pixels]
        bbox = [min(xs), min(ys), max(xs) + 1, max(ys) + 1]
    return {"pixels": pixels, "bbox": bbox, "advance_26_6": advance}


def compare_render(a, b):
    if a is None or b is None:
        return {"missing": True, "exact": a is b}
    keys = set(a["pixels"]) | set(b["pixels"])
    changed = 0
    total_abs = 0
    max_abs = 0
    for key in keys:
        d = abs(a["pixels"].get(key, 0) - b["pixels"].get(key, 0))
        if d:
            changed += 1
            total_abs += d
            max_abs = max(max_abs, d)
    union = len(keys)
    return {
        "missing": False,
        "exact": changed == 0 and a["bbox"] == b["bbox"] and a["advance_26_6"] == b["advance_26_6"],
        "bitmap_exact": changed == 0,
        "changed_pixels": changed,
        "union_pixels": union,
        "changed_fraction": changed / union if union else 0.0,
        "mean_abs_gray_delta": total_abs / union if union else 0.0,
        "max_abs_gray_delta": max_abs,
        "bbox_same": a["bbox"] == b["bbox"],
        "advance_same": a["advance_26_6"] == b["advance_26_6"],
        "generated_bbox": a["bbox"],
        "reference_bbox": b["bbox"],
        "generated_advance_26_6": a["advance_26_6"],
        "reference_advance_26_6": b["advance_26_6"],
    }


def compare_mode(face_a, face_b, candidates, total_mapped, size, load_flags):
    differing = []
    exact = 0
    bitmap_exact = 0
    bbox_diff = 0
    advance_diff = 0
    changed_fraction_sum = 0.0
    mean_gray_sum = 0.0
    max_changed_fraction = 0.0
    max_mean_gray = 0.0
    max_abs_gray = 0
    thresholds = {str(t): 0 for t in THRESHOLDS}

    for cp in candidates:
        diff = compare_render(render(face_a, cp, size, load_flags), render(face_b, cp, size, load_flags))
        if diff.get("exact"):
            exact += 1
        if diff.get("bitmap_exact"):
            bitmap_exact += 1
        if diff.get("missing"):
            differing.append({"codepoint": cp, **diff})
            continue
        if not diff["bbox_same"]:
            bbox_diff += 1
        if not diff["advance_same"]:
            advance_diff += 1
        if diff["changed_pixels"]:
            differing.append({"codepoint": cp, **diff})
            changed_fraction_sum += diff["changed_fraction"]
            mean_gray_sum += diff["mean_abs_gray_delta"]
            max_changed_fraction = max(max_changed_fraction, diff["changed_fraction"])
            max_mean_gray = max(max_mean_gray, diff["mean_abs_gray_delta"])
            max_abs_gray = max(max_abs_gray, diff["max_abs_gray_delta"])
            for threshold in THRESHOLDS:
                if diff["mean_abs_gray_delta"] >= threshold:
                    thresholds[str(threshold)] += 1

    differing.sort(
        key=lambda x: (x.get("mean_abs_gray_delta", 0), x.get("changed_fraction", 0), x.get("max_abs_gray_delta", 0)),
        reverse=True,
    )
    diff_count = len(differing)
    exact_total = total_mapped - len(candidates) + exact
    bitmap_exact_total = total_mapped - len(candidates) + bitmap_exact
    return {
        "mapped_glyphs": total_mapped,
        "candidate_glyphs": len(candidates),
        "exact_render_glyphs": exact_total,
        "exact_render_fraction": exact_total / total_mapped if total_mapped else 1.0,
        "bitmap_exact_glyphs": bitmap_exact_total,
        "bitmap_exact_fraction": bitmap_exact_total / total_mapped if total_mapped else 1.0,
        "bitmap_diff_glyphs": diff_count,
        "bbox_diff_candidates": bbox_diff,
        "advance_diff_candidates": advance_diff,
        "mean_changed_fraction_among_diff": changed_fraction_sum / diff_count if diff_count else 0.0,
        "mean_gray_delta_among_diff": mean_gray_sum / diff_count if diff_count else 0.0,
        "max_changed_fraction": max_changed_fraction,
        "max_mean_abs_gray_delta": max_mean_gray,
        "max_abs_gray_delta": max_abs_gray,
        "mean_gray_threshold_counts": thresholds,
        "worst_glyphs": differing[:50],
    }


def compare_font(generated_path, reference_path):
    ga = TTFont(str(generated_path), lazy=False)
    rb = TTFont(str(reference_path), lazy=False)
    cmap_a = ga.getBestCmap() or {}
    cmap_b = rb.getBestCmap() or {}
    common = sorted(set(cmap_a) & set(cmap_b))

    candidates = []
    for cp in common:
        na, nb = cmap_a[cp], cmap_b[cp]
        try:
            raw_diff = glyph_bytes(ga, na) != glyph_bytes(rb, nb)
        except Exception:
            raw_diff = True
        metric_diff = ga["hmtx"].metrics.get(na) != rb["hmtx"].metrics.get(nb)
        if raw_diff or metric_diff or na != nb:
            candidates.append(cp)

    ga.close()
    rb.close()

    face_a = freetype.Face(str(generated_path))
    face_b = freetype.Face(str(reference_path))
    modes = {}
    for mode_name, load_flags in MODES.items():
        by_size = {}
        for size in SIZES:
            by_size[str(size)] = compare_mode(face_a, face_b, candidates, len(common), size, load_flags)
        modes[mode_name] = by_size

    return {
        "file": generated_path.name,
        "candidate_codepoints": candidates,
        "modes": modes,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--generated", required=True)
    ap.add_argument("--reference", required=True)
    ap.add_argument("--json", default="render-comparison.json")
    ap.add_argument("--markdown", default="render-comparison.md")
    args = ap.parse_args()

    gen = find_fonts(args.generated)
    ref = find_fonts(args.reference)
    names = sorted(set(gen) & set(ref))
    results = []
    for name in names:
        print("Raster comparing %s" % name, flush=True)
        results.append(compare_font(gen[name], ref[name]))

    report = {
        "freetype_version": list(freetype.version()),
        "sizes": list(SIZES),
        "thresholds": list(THRESHOLDS),
        "results": results,
    }
    Path(args.json).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = [
        "# HackGen v2.10.0 raster comparison",
        "",
        "FreeType version: `%s`" % ".".join(map(str, freetype.version())),
        "",
        "Hinted mode uses normal FreeType hinting. Unhinted mode disables both native and auto hinting, helping distinguish base-outline changes from ttfautohint-version effects.",
        "",
    ]
    for mode_name in MODES:
        lines.extend(["# %s" % mode_name.capitalize(), ""])
        for size in SIZES:
            lines.extend([
                "## %d px" % size,
                "",
                "| Font | candidate | bitmap diff | exact all | bbox Δ | advance Δ | mean gray Δ | >=1/255 | >=5/255 | max mean gray Δ |",
                "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
            ])
            for item in results:
                s = item["modes"][mode_name][str(size)]
                lines.append(
                    "| %s | %d | %d | %.3f%% | %d | %d | %.3f/255 | %d | %d | %.3f/255 |" % (
                        item["file"], len(item["candidate_codepoints"]), s["bitmap_diff_glyphs"],
                        s["bitmap_exact_fraction"] * 100, s["bbox_diff_candidates"], s["advance_diff_candidates"],
                        s["mean_gray_delta_among_diff"], s["mean_gray_threshold_counts"]["1.0"],
                        s["mean_gray_threshold_counts"]["5.0"], s["max_mean_abs_gray_delta"],
                    )
                )
            lines.append("")

    lines.extend(["# Worst hinted raster differences", ""])
    for item in results:
        lines.append("## %s" % item["file"])
        for size in (16, 24, 32):
            lines.append("\n**%d px**" % size)
            worst = item["modes"]["hinted"][str(size)]["worst_glyphs"][:10]
            if not worst:
                lines.append("- none")
                continue
            for d in worst:
                cp = d["codepoint"]
                lines.append(
                    "- U+%04X: changed=`%.2f%%`, mean-gray=`%.2f/255`, max-gray=`%d/255`, bbox-same=`%s`, advance-same=`%s`" % (
                        cp, d.get("changed_fraction", 0) * 100, d.get("mean_abs_gray_delta", 0), d.get("max_abs_gray_delta", 0),
                        d.get("bbox_same"), d.get("advance_same"),
                    )
                )
        lines.append("")

    Path(args.markdown).write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines[:40]))


if __name__ == "__main__":
    main()
