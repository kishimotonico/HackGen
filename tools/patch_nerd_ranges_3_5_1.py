#!/usr/bin/env python3
from pathlib import Path

path = Path("hackgen_generator.sh")
text = path.read_text()

replacements = {
    "SelectMore(0uE5FA, 0uE6B7)": "SelectMore(0uE5FA, 0uE6BB)",
    "SelectMore(0uE700, 0uE8E3)": "SelectMore(0uE700, 0uE958)",
    "SelectMore(0uF300, 0uF381)": "SelectMore(0uF300, 0uF385)",
    "SelectMore(0uEA60, 0uEC1E)": "SelectMore(0uEA60, 0uEC84)",
}

for old, new in replacements.items():
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"expected exactly one occurrence of {old!r}, found {count}")
    text = text.replace(old, new)

path.write_text(text)
print("Updated Nerd Fonts extraction ranges for v3.5.1")
