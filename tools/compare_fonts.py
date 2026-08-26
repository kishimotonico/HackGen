#!/usr/bin/env python3
import argparse
import hashlib
import json
from pathlib import Path

from fontTools.pens.recordingPen import RecordingPen
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


def digest(data):
    return hashlib.sha256(data).hexdigest()


def normalize_recording(commands):
    normalized = []
    for op, args in commands:
        normalized.append([op, json.loads(json.dumps(args))])
    return normalized


def glyph_signature(font, glyph_name):
    pen = RecordingPen()
    font.getGlyphSet()[glyph_name].draw(pen)
    payload = json.dumps(normalize_recording(pen.value), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return digest(payload.encode("utf-8"))


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
    outline_diffs = []

    gen_hmtx = generated["hmtx"].metrics if "hmtx" in generated else {}
    ref_hmtx = reference["hmtx"].metrics if "hmtx" in reference else {}

    for codepoint in common_codes:
        gen_name = gen_cmap[codepoint]
        ref_name = ref_cmap[codepoint]
        if gen_name != ref_name:
            mapping_diffs.append([codepoint, gen_name, ref_name])

        gen_metric = gen_hmtx.get(gen_name)
        ref_metric = ref_hmtx.get(ref_name)
        if gen_metric != ref_metric:
            metric_diffs.append([codepoint, gen_metric, ref_metric])

        try:
            gen_sig = glyph_signature(generated, gen_name)
            ref_sig = glyph_signature(reference, ref_name)
            if gen_sig != ref_sig:
                outline_diffs.append(codepoint)
        except Exception as exc:
            outline_diffs.append([codepoint, "ERROR: %s" % exc])

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
        "outlines": {
            "diff_count": len(outline_diffs),
            "diff_sample": outline_diffs[:50],
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


def format_codepoints(values, limit=20):
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
        "# HackGen v2.10.0 semantic comparison",
        "",
        "Generated fonts: %d / reference fonts: %d" % (len(generated_fonts), len(reference_fonts)),
        "",
    ]
    if missing_generated:
        lines.append("Missing from generated: `%s`" % "`, `".join(missing_generated))
    if missing_reference:
        lines.append("Missing from reference: `%s`" % "`, `".join(missing_reference))
    if missing_generated or missing_reference:
        lines.append("")

    lines.extend([
        "| Font | cmap Δ | metrics Δ | outline Δ | scalar tables Δ | name Δ | raw hint/layout Δ |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ])
    for item in comparisons:
        cmap_delta = len(item["cmap"]["missing_codepoints"]) + len(item["cmap"]["extra_codepoints"]) + item["cmap"]["mapping_diff_count"]
        lines.append(
            "| %s | %d | %d | %d | %d | %d | %d |" % (
                item["file"], cmap_delta, item["metrics"]["diff_count"], item["outlines"]["diff_count"],
                len(item["scalar_table_diffs"]), len(item["name_diffs"]), len(item["raw_table_hash_diffs"]),
            )
        )

    for item in comparisons:
        lines.extend(["", "## %s" % item["file"], ""])
        lines.append("- Binary-identical: `%s`" % item["same_binary_sha256"])
        lines.append("- Glyph count: generated `%d`, reference `%d`" % (item["glyph_count"]["generated"], item["glyph_count"]["reference"]))
        lines.append("- cmap: generated `%d`, reference `%d`" % (item["cmap"]["generated_count"], item["cmap"]["reference_count"]))
        lines.append("- Missing codepoints: %s" % format_codepoints(item["cmap"]["missing_codepoints"]))
        lines.append("- Extra codepoints: %s" % format_codepoints(item["cmap"]["extra_codepoints"]))
        lines.append("- cmap mapping diffs: `%d`" % item["cmap"]["mapping_diff_count"])
        lines.append("- hmtx diffs by Unicode mapping: `%d`" % item["metrics"]["diff_count"])
        lines.append("- glyph outline diffs by Unicode mapping: `%d`" % item["outlines"]["diff_count"])
        lines.append("- scalar table groups changed: `%s`" % (", ".join(item["scalar_table_diffs"]) or "none"))
        lines.append("- name IDs changed: `%s`" % (", ".join(item["name_diffs"]) or "none"))
        lines.append("- raw hint/layout tables changed: `%s`" % (", ".join(item["raw_table_hash_diffs"]) or "none"))

    Path(args.markdown).write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines[:20]))

    if missing_generated or missing_reference:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
