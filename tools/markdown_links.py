import argparse
from collections import Counter
from pathlib import Path
import re


LINK_PATTERN = re.compile(r"(?<!!)\[[^\]]*]\(([^)\s]+)(?:\s+['\"][^)]*['\"])?\)")
HEADING_PATTERN = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
MARKUP_PATTERN = re.compile(r"[`*_~]\s*|\s*[`*_~]")
PUNCTUATION_PATTERN = re.compile(r"[^\w\-\sа-яё]", re.IGNORECASE)


def anchor(text: str) -> str:
    normalized = MARKUP_PATTERN.sub("", text).lower()
    normalized = PUNCTUATION_PATTERN.sub("", normalized)
    return re.sub(r"[-\s]+", "-", normalized).strip("-")


def anchors(path: Path) -> set[str]:
    counts: Counter[str] = Counter()
    values: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        match = HEADING_PATTERN.match(line)
        if match is None:
            continue
        value = anchor(match.group(2))
        suffix = counts[value]
        counts[value] += 1
        values.add(value if suffix == 0 else f"{value}-{suffix}")
    return values


def validate_file(path: Path) -> list[str]:
    failures: list[str] = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        for match in LINK_PATTERN.finditer(line):
            destination = match.group(1)
            if destination.startswith(("https://", "http://", "mailto:", "tel:")):
                continue
            target, separator, fragment = destination.partition("#")
            target_path = path if not target else (path.parent / target).resolve()
            if not target_path.is_file():
                failures.append(f"{path}:{number}: не найден файл {destination}")
                continue
            if separator and fragment and fragment not in anchors(target_path):
                failures.append(f"{path}:{number}: не найден якорь {destination}")
    return failures


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("files", nargs="+", type=Path)
    args = parser.parse_args()
    failures = [failure for path in args.files for failure in validate_file(path)]
    print("\n".join(failures))
    return int(bool(failures))


if __name__ == "__main__":
    raise SystemExit(main())
