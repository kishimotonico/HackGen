#!/usr/bin/env python3
import argparse
import json
from pathlib import Path

from fontTools.ttLib import TTFont


SPECIALS = {0x23FB, 0x23FC, 0x23FD, 0x23FE, 0x2630, 0x2665, 0x26A1, 0x2B58}


def is_nerd_codepoint(cp):
    return (
        0xE000 <= cp <= 0xF8FF
        or 0xF0000 <= cp <= 0xFFFFD
        or cp in SPECIALS
    )


def glyph_signature(font, name):
    glyf = font['glyf']
    glyph = glyf[name]
    coords, ends, flags = glyph.getCoordinates(glyf)
    components = []
    if glyph.isComposite():
        for component in glyph.components:
            try:
                comp_name, transform = component.getComponentInfo()
                components.append((comp_name, tuple(transform)))
            except Exception:
                components.append((getattr(component, 'glyphName', None), repr(component)))
    return (
        tuple((int(x), int(y)) for x, y in coords),
        tuple(ends),
        tuple(flags),
        tuple(components),
    )


def compare_pair(old_path, new_path):
    old = TTFont(str(old_path), lazy=False)
    new = TTFont(str(new_path), lazy=False)
    old_cmap = old.getBestCmap() or {}
    new_cmap = new.getBestCmap() or {}

    old_non = {cp for cp in old_cmap if not is_nerd_codepoint(cp)}
    new_non = {cp for cp in new_cmap if not is_nerd_codepoint(cp)}
    missing_non = sorted(old_non - new_non)
    extra_non = sorted(new_non - old_non)

    mapping_changes = []
    metric_changes = []
    geometry_changes = []
    old_hmtx = old['hmtx'].metrics
    new_hmtx = new['hmtx'].metrics

    for cp in sorted(old_non & new_non):
        old_name = old_cmap[cp]
        new_name = new_cmap[cp]
        if old_name != new_name:
            mapping_changes.append(cp)
            continue
        if old_hmtx.get(old_name) != new_hmtx.get(new_name):
            metric_changes.append(cp)
        if glyph_signature(old, old_name) != glyph_signature(new, new_name):
            geometry_changes.append(cp)

    old_nerd = {cp for cp in old_cmap if is_nerd_codepoint(cp)}
    new_nerd = {cp for cp in new_cmap if is_nerd_codepoint(cp)}
    nerd_added = sorted(new_nerd - old_nerd)
    nerd_removed = sorted(old_nerd - new_nerd)

    result = {
        'font': new_path.name,
        'non_nerd': {
            'missing': missing_non,
            'extra': extra_non,
            'mapping_changes': mapping_changes,
            'metric_changes': metric_changes,
            'geometry_changes': geometry_changes,
        },
        'nerd': {
            'old_count': len(old_nerd),
            'new_count': len(new_nerd),
            'added': nerd_added,
            'removed': nerd_removed,
        },
    }
    old.close()
    new.close()
    return result


def find_fonts(root):
    return {p.name: p for p in Path(root).glob('HackGen*.ttf')}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--old', required=True)
    parser.add_argument('--new', required=True)
    parser.add_argument('--json', default='nerd-update-comparison.json')
    args = parser.parse_args()

    old_fonts = find_fonts(args.old)
    new_fonts = find_fonts(args.new)
    names = sorted(set(old_fonts) | set(new_fonts))
    if set(old_fonts) != set(new_fonts):
        raise SystemExit('font file set changed: old=%s new=%s' % (sorted(old_fonts), sorted(new_fonts)))

    results = []
    failed = False
    for name in names:
        item = compare_pair(old_fonts[name], new_fonts[name])
        results.append(item)
        non = item['non_nerd']
        nerd = item['nerd']
        print('%s: non-Nerd missing=%d extra=%d mapping=%d metrics=%d geometry=%d; Nerd %d -> %d (+%d -%d)' % (
            name,
            len(non['missing']), len(non['extra']), len(non['mapping_changes']),
            len(non['metric_changes']), len(non['geometry_changes']),
            nerd['old_count'], nerd['new_count'], len(nerd['added']), len(nerd['removed'])
        ))
        if any(non[key] for key in ('missing', 'extra', 'mapping_changes', 'metric_changes', 'geometry_changes')):
            failed = True
        if nerd['removed']:
            print('  removed Nerd codepoints:', ' '.join('U+%04X' % cp for cp in nerd['removed'][:40]))
            failed = True

    Path(args.json).write_text(json.dumps({'comparisons': results}, indent=2), encoding='utf-8')
    if failed:
        raise SystemExit('Unexpected regression outside Nerd glyph additions/updates')


if __name__ == '__main__':
    main()
