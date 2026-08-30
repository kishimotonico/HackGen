#!/usr/bin/env python3
import argparse
import hashlib
import json
import math
from collections import Counter
from pathlib import Path

from fontTools.ttLib import TTFont


NAME_IDS = (1, 2, 4, 5, 6, 16, 17)
SCALAR_FIELDS = {
    "head": ("unitsPerEm", "macStyle"),
    "hhea": ("ascent", "descent", "lineGap", "advanceWidthMax", "minLeftSideBearing", "minRightSideBearing", "xMaxExtent"),
    "maxp": ("numGlyphs",),
    "OS/2": (
        "version", "xAvgCharWidth", "usWeightClass", "usWidthClass", "fsType",
        "ySubscriptXSize", "ySubscriptYSize", "ySubscriptXOffset", "ySubscriptYOffset",
        "ySuperscriptXSize", "ySuperscriptYSize", "ySuperscriptXOffset", "ySuperscriptYOffset",
        "yStrikeoutSize", "yStrikeoutPosition", "sFamilyClass", "fsSelection",
        "sTypoAscender", "sTypoDescender", "sTypoLineGap", "usWinAscent", "usWinDescent",
    ),
    "post": ("formatType", "italicAngle", "underlinePosition", "underlineThickness", "isFixedPitch"),
}
RAW_TABLES = ("cvt ", "fpgm", "prep", "kern", "GPOS", "GSUB")
PIXEL_SIZES = (12, 16, 24, 32, 48)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def best_names(font):
    result = {}
    if "name" not in font:
        return result
    for name_id in NAME_IDS:
        values = []
        for rec in font["name"].names:
            if rec.nameID != name_id:
                continue
            try:
                text = rec.toUnicode()
            except Exception:
                text = repr(rec.string)
            values.append((rec.platformID, rec.platEncID, rec.langID, text))
        result[str(name_id)] = sorted(set(values))
    return result


def scalar_summary(font):
    result = {}
    for table_tag, fields in SCALAR_FIELDS.items():
        if table_tag not in font:
            result[table_tag] = None
            continue
        table = font[table_tag]
        result[table_tag] = {field: getattr(table, field, None) for field in fields}
    return result


def raw_table_hashes(font):
    result = {}
    for tag in RAW_TABLES:
        if tag in font:
            try:
                result[tag] = digest(font.getTableData(tag))
            except Exception as exc:
                result[tag] = "ERROR: %s" % exc
        else:
            result[tag] = None
    return result


def instruction_bytes(glyph):
    program = getattr(glyph, "program", None)
    if program is None:
        return b""
    try:
        return bytes(program.getBytecode())
    except Exception:
        return b""


def component_signature(glyph):
    if not glyph.isComposite():
        return None
    result = []
    for component in glyph.components:
        try:
            name, transform = component.getComponentInfo()
            result.append([name, list(transform)])
        except Exception:
            result.append([getattr(component, "glyphName", None), repr(component)])
    return result


def glyph_geometry(font, glyph_name):
    glyf = font["glyf"]
    glyph = glyf[glyph_name]
    coordinates, end_pts, flags = glyph.getCoordinates(glyf)
    coords = [(int(x), int(y)) for x, y in coordinates]
    return {
        "coords": coords,
        "end_pts": list(end_pts),
        "flags": list(flags),
        "components": component_signature(glyph),
        "instructions": instruction_bytes(glyph),
    }


def geometry_diff(font_a, name_a, font_b, name_b):
    a = glyph_geometry(font_a, name_a)
    b = glyph_geometry(font_b, name_b)

    structural_reasons = []
    if len(a["coords"]) != len(b["coords"]):
        structural_reasons.append("point-count")
    if a["end_pts"] != b["end_pts"]:
        structural_reasons.append("contours")
    if a["components"] != b["components"]:
        structural_reasons.append("components")

    instruction_same = a["instructions"] == b["instructions"]

    if structural_reasons or len(a["coords"]) != len(b["coords"]):
        return {
            "structural_reasons": structural_reasons,
            "instruction_same": instruction_same,
            "point_count": [len(a["coords"]), len(b["coords"])],
            "max_axis_delta": None,
            "max_euclidean_delta": None,
            "mean_euclidean_delta": None,
            "changed_point_count": None,
        }

    max_axis = 0
    max_euclid = 0.0
    total_euclid = 0.0
    changed = 0
    for (ax, ay), (bx, by) in zip(a["coords"], b["coords"]):
        dx = ax - bx
        dy = ay - by
        axis = max(abs(dx), abs(dy))
        euclid = math.hypot(dx, dy)
        max_axis = max(max_axis, axis)
        max_euclid = max(max_euclid, euclid)
        total_euclid += euclid
        if dx or dy:
            changed += 1

    return {
        "structural_reasons": [],
        "instruction_same": instruction_same,
        "point_count": [len(a["coords"]), len(b["coords"])],
        "max_axis_delta": max_axis,
        "max_euclidean_delta": max_euclid,
        "mean_euclidean_delta": total_euclid / len(a["coords"]) if a["coords"] else 0.0,
        "changed_point_count": changed,
    }


def delta_bucket(value):
    if value == 0:
        return "0"
    if value <= 1:
        return "<=1"
    if value <= 2:
        return "<=2"
    if value <= 4:
        return "<=4"
    if value <= 8:
        return "<=8"
    if value <= 16:
        return "<=16"
    return ">16"


def compare_font(generated_path, reference_path):
    generated = TTFont(str(generated_path), lazy=False)
    reference = TTFont(str(reference_path), lazy=False)

    gen_cmap = generated.getBestCmap() or {}
    ref_cmap = reference.getBestCmap() or {}
    gen_codes = set(gen_cmap)
    ref_codes = set(ref_cmap)
    common_codes = sorted(gen_codes & ref_codes)

    cmap_missing = sorted(ref_codes - gen_codes)
    cmap_extra = sorted(gen_codes - ref_codes)
    mapping_diffs = []
    metric_diffs = []

    gen_hmtx = generated["hmtx"].metrics if "hmtx" in generated else {}
    ref_hmtx = reference["hmtx"].metrics if "hmtx" in reference else {}

    glyph_to_codepoints = {}
    for codepoint in common_codes:
        gen_name = gen_cmap[codepoint]
        ref_name = ref_cmap[codepoint]
        if gen_name != ref_name:
            mapping_diffs.append([codepoint, gen_name, ref_name])
        gen_metric = gen_hmtx.get(gen_name)
        ref_metric = ref_hmtx.get(ref_name)
        if gen_metric != ref_metric:
            metric_diffs.append([codepoint, gen_metric, ref_metric])
        glyph_to_codepoints.setdefault((gen_name, ref_name), []).append(codepoint)

    geometry_diffs = []
    structural_count = 0
    instruction_diff_count = 0
    max_axis_overall = 0
    max_euclid_overall = 0.0
    weighted_euclid_sum = 0.0
    weighted_point_count = 0
    bucket_counts = Counter()

    for (gen_name, ref_name), codepoints in glyph_to_codepoints.items():
        try:
            diff = geometry_diff(generated, gen_name, reference, ref_name)
        except Exception as exc:
            diff = {
                "structural_reasons": ["error: %s" % exc],
                "instruction_same": False,
                "point_count": [None, None],
                "max_axis_delta": None,
                "max_euclidean_delta": None,
                "mean_euclidean_delta": None,
                "changed_point_count": None,
            }

        if diff["structural_reasons"]:
            structural_count += 1
            bucket_counts["structural"] += 1
        else:
            bucket_counts[delta_bucket(diff["max_axis_delta"])] += 1
            max_axis_overall = max(max_axis_overall, diff["max_axis_delta"])
            max_euclid_overall = max(max_euclid_overall, diff["max_euclidean_delta"])
            points = diff["point_count"][0] or 0
            weighted_euclid_sum += diff["mean_euclidean_delta"] * points
            weighted_point_count += points

        if not diff["instruction_same"]:
            instruction_diff_count += 1

        if diff["structural_reasons"] or diff["max_axis_delta"] or not diff["instruction_same"]:
            geometry_diffs.append({
                "glyph": gen_name,
                "reference_glyph": ref_name,
                "codepoints": codepoints,
                **diff,
            })

    geometry_diffs.sort(
        key=lambda item: (
            bool(item["structural_reasons"]),
            item["max_axis_delta"] if item["max_axis_delta"] is not None else 10**9,
            item["max_euclidean_delta"] if item["max_euclidean_delta"] is not None else 10**9,
        ),
        reverse=True,
    )

    units_per_em = generated["head"].unitsPerEm
    pixel_equivalents = {
        str(px): max_axis_overall * px / units_per_em for px in PIXEL_SIZES
    }

    gen_scalars = scalar_summary(generated)
    ref_scalars = scalar_summary(reference)
    scalar_diffs = {}
    for table_tag in SCALAR_FIELDS:
        if gen_scalars.get(table_tag) != ref_scalars.get(table_tag):
            scalar_diffs[table_tag] = {
                "generated": gen_scalars.get(table_tag),
                "reference": ref_scalars.get(table_tag),
            }

    gen_names = best_names(generated)
    ref_names = best_names(reference)
    name_diffs = {}
    for name_id in map(str, NAME_IDS):
        if gen_names.get(name_id) != ref_names.get(name_id):
            name_diffs[name_id] = {
                "generated": gen_names.get(name_id),
                "reference": ref_names.get(name_id),
            }

    gen_raw = raw_table_hashes(generated)
    ref_raw = raw_table_hashes(reference)
    raw_table_diffs = {
        tag: {"generated": gen_raw[tag], "reference": ref_raw[tag]}
        for tag in RAW_TABLES
        if gen_raw[tag] != ref_raw[tag]
    }

    result = {
        "file": generated_path.name,
        "generated_size": generated_path.stat().st_size,
        "reference_size": reference_path.stat().st_size,
        "same_binary_sha256": digest(generated_path.read_bytes()) == digest(reference_path.read_bytes()),
        "glyph_count": {
            "generated": len(generated.getGlyphOrder()),
            "reference": len(reference.getGlyphOrder()),
        },
        "units_per_em": units_per_em,
        "cmap": {
            "generated_count": len(gen_codes),
            "reference_count": len(ref_codes),
            "missing_codepoints": cmap_missing,
            "extra_codepoints": cmap_extra,
            "mapping_diff_count": len(mapping_diffs),
            "mapping_diff_sample": mapping_diffs[:50],
        },
        "metrics": {
            "diff_count": len(metric_diffs),
            "diff_sample": metric_diffs[:50],
        },
        "geometry": {
            "mapped_unique_glyphs": len(glyph_to_codepoints),
            "diff_glyph_count": len(geometry_diffs),
            "structural_diff_count": structural_count,
            "instruction_diff_count": instruction_diff_count,
            "max_axis_delta_units": max_axis_overall,
            "max_euclidean_delta_units": max_euclid_overall,
            "mean_point_displacement_units": weighted_euclid_sum / weighted_point_count if weighted_point_count else 0.0,
            "max_axis_delta_pixels": pixel_equivalents,
            "delta_buckets": dict(bucket_counts),
            "worst_glyphs": geometry_diffs[:100],
        },
        "scalar_table_diffs": scalar_diffs,
        "name_diffs": name_diffs,
        "raw_table_hash_diffs": raw_table_diffs,
    }
    generated.close()
    reference.close()
    return result


def find_fonts(root):
    return {path.name: path for path in Path(root).rglob("HackGen*.ttf")}


def format_codepoints(values, limit=12):
    shown = values[:limit]
    text = ", ".join("U+%04X" % value for value in shown)
    if len(values) > limit:
        text += ", …"
    return text or "none"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--generated", required=True)
    parser.add_argument("--reference", required=True)
    parser.add_argument("--json", default="font-comparison.json")
    parser.add_argument("--markdown", default="font-comparison.md")
    args = parser.parse_args()

    generated_fonts = find_fonts(args.generated)
    reference_fonts = find_fonts(args.reference)
    names = sorted(set(generated_fonts) | set(reference_fonts))

    missing_generated = sorted(set(reference_fonts) - set(generated_fonts))
    missing_reference = sorted(set(generated_fonts) - set(reference_fonts))
    comparisons = []

    for name in names:
        if name not in generated_fonts or name not in reference_fonts:
            continue
        print("Comparing %s" % name, flush=True)
        comparisons.append(compare_font(generated_fonts[name], reference_fonts[name]))

    report = {
        "missing_generated": missing_generated,
        "missing_reference": missing_reference,
        "comparisons": comparisons,
    }
    Path(args.json).write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

    lines = [
        "# HackGen v2.10.0 detailed semantic comparison",
        "",
        "Generated fonts: %d / reference fonts: %d" % (len(generated_fonts), len(reference_fonts)),
        "",
        "Geometry deltas are measured in font units. Pixel equivalents are linear pre-rasterization estimates; hinting can alter final pixel coverage.",
        "",
    ]
    if missing_generated:
        lines.append("Missing from generated: `%s`" % "`, `".join(missing_generated))
    if missing_reference:
        lines.append("Missing from reference: `%s`" % "`, `".join(missing_reference))
    if missing_generated or missing_reference:
        lines.append("")

    lines.extend([
        "| Font | cmap Δ | hmtx Δ | geometry Δ | structural Δ | instr Δ | max axis Δ | @16px | @32px |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ])
    for item in comparisons:
        cmap_delta = len(item["cmap"]["missing_codepoints"]) + len(item["cmap"]["extra_codepoints"]) + item["cmap"]["mapping_diff_count"]
        g = item["geometry"]
        lines.append(
            "| %s | %d | %d | %d | %d | %d | %d u | %.4f px | %.4f px |" % (
                item["file"], cmap_delta, item["metrics"]["diff_count"], g["diff_glyph_count"],
                g["structural_diff_count"], g["instruction_diff_count"], g["max_axis_delta_units"],
                g["max_axis_delta_pixels"]["16"], g["max_axis_delta_pixels"]["32"],
            )
        )

    for item in comparisons:
        g = item["geometry"]
        lines.extend(["", "## %s" % item["file"], ""])
        lines.append("- Binary-identical: `%s`" % item["same_binary_sha256"])
        lines.append("- Glyph count: generated `%d`, reference `%d`" % (item["glyph_count"]["generated"], item["glyph_count"]["reference"]))
        lines.append("- unitsPerEm: `%d`" % item["units_per_em"])
        lines.append("- cmap differences: missing `%d`, extra `%d`, mapping `%d`" % (len(item["cmap"]["missing_codepoints"]), len(item["cmap"]["extra_codepoints"]), item["cmap"]["mapping_diff_count"]))
        lines.append("- hmtx differences by Unicode mapping: `%d`" % item["metrics"]["diff_count"])
        lines.append("- geometry-different mapped glyphs: `%d / %d`" % (g["diff_glyph_count"], g["mapped_unique_glyphs"]))
        lines.append("- structural geometry differences: `%d`" % g["structural_diff_count"])
        lines.append("- per-glyph instruction differences: `%d`" % g["instruction_diff_count"])
        lines.append("- max axis displacement: `%d` font units" % g["max_axis_delta_units"])
        lines.append("- max Euclidean point displacement: `%.3f` font units" % g["max_euclidean_delta_units"])
        lines.append("- mean point displacement over comparable glyphs: `%.4f` font units" % g["mean_point_displacement_units"])
        lines.append("- max displacement equivalent: " + ", ".join("%spx=`%.4fpx`" % (px, g["max_axis_delta_pixels"][str(px)]) for px in PIXEL_SIZES))
        lines.append("- max-axis delta buckets: `%s`" % json.dumps(g["delta_buckets"], sort_keys=True))
        lines.append("- scalar table groups changed: `%s`" % (", ".join(item["scalar_table_diffs"]) or "none"))
        lines.append("- name IDs changed: `%s`" % (", ".join(item["name_diffs"]) or "none"))
        lines.append("- raw hint/layout tables changed: `%s`" % (", ".join(item["raw_table_hash_diffs"]) or "none"))
        if g["worst_glyphs"]:
            lines.append("- worst glyphs:")
            for diff in g["worst_glyphs"][:10]:
                cps = format_codepoints(diff["codepoints"])
                if diff["structural_reasons"]:
                    detail = "structural=%s" % ",".join(diff["structural_reasons"])
                else:
                    detail = "max=%su, mean=%.3fu, changed-points=%s/%s" % (
                        diff["max_axis_delta"], diff["mean_euclidean_delta"], diff["changed_point_count"], diff["point_count"][0]
                    )
                lines.append("  - `%s` (%s): %s; instruction-same=`%s`" % (diff["glyph"], cps, detail, diff["instruction_same"]))

    Path(args.markdown).write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines[:28]))

    if missing_generated or missing_reference:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
