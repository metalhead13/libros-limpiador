"""Clean book files in a folder: strip pirate-site filename tags and file metadata."""

from __future__ import annotations

import argparse
import logging
import os
import re
import sys
import tempfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable
from xml.etree import ElementTree as ET

from pypdf import PdfReader, PdfWriter

LOGGER = logging.getLogger("book_cleaner_agent")

NAME_FRAGMENTS = (
    "z-library.sk",
    "1lib.sk",
    "z-lib.sk",
)
FRAGMENT_RE = re.compile(
    "|".join(re.escape(fragment) for fragment in NAME_FRAGMENTS),
    re.IGNORECASE,
)
EMPTY_PARENS_RE = re.compile(r"[\(\[\{][\s,;:/\\\-_.]*[\)\]\}]")
DANGLING_COMMA_RE = re.compile(r"\s*,\s*,+")
SPACE_BEFORE_COMMA_RE = re.compile(r"\s+,")
MULTI_SPACE_RE = re.compile(r"\s{2,}")
INCOMPLETE_SUFFIXES = {".crdownload", ".tmp", ".part", ".download"}
METADATA_EXTENSIONS = {".pdf", ".epub"}
KEEP_DC_TAGS = {"title", "language", "identifier", "creator"}
OPF_NS = "http://www.idpf.org/2007/opf"
DC_NS = "http://purl.org/dc/elements/1.1/"

ET.register_namespace("", OPF_NS)
ET.register_namespace("dc", DC_NS)
ET.register_namespace("opf", OPF_NS)


@dataclass
class CleanResult:
    path: Path
    renamed_to: Path | None = None
    metadata_cleaned: bool = False
    skipped: bool = False
    reason: str = ""
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors and not self.skipped


class BookCleanerAgent:
    """Reads files in a folder, cleans metadata, and strips known tags from names."""

    def __init__(self, folder: str | Path, dry_run: bool = False) -> None:
        self.folder = Path(folder).expanduser().resolve()
        self.dry_run = dry_run

    def run(self) -> list[CleanResult]:
        if not self.folder.is_dir():
            raise FileNotFoundError(f"Folder not found: {self.folder}")

        results: list[CleanResult] = []
        for path in sorted(self.folder.iterdir(), key=lambda item: item.name.lower()):
            if path.is_file():
                results.append(self.process_file(path))
        return results

    def process_file(self, path: Path) -> CleanResult:
        result = CleanResult(path=path)
        suffix = path.suffix.lower()

        if suffix in INCOMPLETE_SUFFIXES:
            result.skipped = True
            result.reason = "incomplete download"
            LOGGER.info("Skipping incomplete file: %s", path.name)
            return result

        try:
            if suffix in METADATA_EXTENSIONS:
                if not self.dry_run:
                    if suffix == ".pdf":
                        self._clean_pdf_metadata(path)
                    else:
                        self._clean_epub_metadata(path)
                result.metadata_cleaned = True
                if self.dry_run:
                    result.reason = "dry-run"
        except Exception as exc:  # noqa: BLE001 - keep processing remaining files
            result.errors.append(f"metadata: {exc}")
            LOGGER.exception("Failed to clean metadata for %s", path.name)

        new_name = clean_filename(path.name)
        if new_name != path.name:
            target = unique_target(path.with_name(new_name))
            result.renamed_to = target
            if not self.dry_run:
                try:
                    path.rename(target)
                except OSError as exc:
                    result.errors.append(f"rename: {exc}")
                    result.renamed_to = None
                    LOGGER.exception("Failed to rename %s", path.name)
        return result

    def _clean_pdf_metadata(self, path: Path) -> None:
        def _write(tmp: Path) -> None:
            reader = PdfReader(str(path))
            writer = PdfWriter()
            writer.append_pages_from_reader(reader)
            writer.metadata = {}
            with tmp.open("wb") as handle:
                writer.write(handle)

        _replace_with_temp(path, _write)

    def _clean_epub_metadata(self, path: Path) -> None:
        def _write(tmp: Path) -> None:
            with zipfile.ZipFile(path, "r") as zin, zipfile.ZipFile(tmp, "w") as zout:
                names = zin.namelist()
                if "mimetype" in names:
                    zout.writestr(
                        zipfile.ZipInfo("mimetype"),
                        zin.read("mimetype"),
                        compress_type=zipfile.ZIP_STORED,
                    )
                for info in zin.infolist():
                    if info.filename == "mimetype":
                        continue
                    data = zin.read(info.filename)
                    if _is_opf(info.filename, data):
                        data = clean_opf_bytes(data)
                    new_info = zipfile.ZipInfo(filename=info.filename, date_time=info.date_time)
                    new_info.compress_type = info.compress_type
                    new_info.external_attr = info.external_attr
                    zout.writestr(new_info, data)

        _replace_with_temp(path, _write)


def clean_filename(name: str) -> str:
    """Remove known pirate-site fragments from a file name, keeping the extension."""
    path = Path(name)
    stem = clean_stem(path.stem)
    if not stem:
        return name
    return f"{stem}{path.suffix}"


def clean_stem(stem: str) -> str:
    text = FRAGMENT_RE.sub("", stem)
    previous = None
    while previous != text:
        previous = text
        text = EMPTY_PARENS_RE.sub("", text)
        text = DANGLING_COMMA_RE.sub(",", text)
        text = SPACE_BEFORE_COMMA_RE.sub(",", text)
        text = MULTI_SPACE_RE.sub(" ", text)
        text = text.strip(" -_,;.")
    return text


def unique_target(path: Path) -> Path:
    if not path.exists():
        return path
    stem, suffix = path.stem, path.suffix
    index = 2
    while True:
        candidate = path.with_name(f"{stem} ({index}){suffix}")
        if not candidate.exists():
            return candidate
        index += 1


def clean_opf_bytes(data: bytes) -> bytes:
    xml = data.decode("utf-8", errors="replace")
    xml = re.sub(r"<!--.*?-->", "", xml, flags=re.DOTALL)
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        return FRAGMENT_RE.sub("", xml).encode("utf-8")

    metadata = root.find(f"{{{OPF_NS}}}metadata")
    if metadata is None:
        metadata = root.find("metadata")
    if metadata is not None:
        _trim_epub_metadata(metadata)
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def _trim_epub_metadata(metadata: ET.Element) -> None:
    keep: list[ET.Element] = []
    for child in list(metadata):
        tag = _local_name(child.tag)
        namespaced_dc = child.tag.startswith("{") and DC_NS in child.tag
        if namespaced_dc and tag in KEEP_DC_TAGS:
            if child.text:
                child.text = clean_stem(child.text) or child.text
            keep.append(child)
        elif tag == "meta" and (child.get("name") or "").lower() == "cover":
            keep.append(child)
    metadata.clear()
    metadata.set("xmlns:dc", DC_NS)
    metadata.set("xmlns:opf", OPF_NS)
    for child in keep:
        metadata.append(child)


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _is_opf(filename: str, data: bytes) -> bool:
    if filename.lower().endswith(".opf"):
        return True
    head = data[:200].lstrip().lower()
    return head.startswith(b"<?xml") and b"<package" in data[:2000].lower()


def _replace_with_temp(path: Path, write_fn: Callable[[Path], None]) -> None:
    prefix = (path.stem[:24] or "book") + "_"
    fd, tmp_name = tempfile.mkstemp(prefix=prefix, suffix=path.suffix, dir=path.parent)
    os.close(fd)
    tmp_path = Path(tmp_name)
    try:
        write_fn(tmp_path)
        tmp_path.replace(path)
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise


def format_report(results: list[CleanResult]) -> str:
    lines = ["Book cleaner report", "==================="]
    if not results:
        lines.append("No files found.")
        return "\n".join(lines)
    for item in results:
        target = item.renamed_to.name if item.renamed_to else item.path.name
        status = "SKIP" if item.skipped else ("ERROR" if item.errors else "OK")
        detail = item.reason or "; ".join(item.errors)
        rename = f" -> {target}" if item.renamed_to and item.renamed_to.name != item.path.name else ""
        meta = " metadata-cleaned" if item.metadata_cleaned else ""
        extra = f" ({detail})" if detail else ""
        lines.append(f"[{status}] {item.path.name}{rename}{meta}{extra}")
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Clean metadata from books and remove z-library.sk / 1lib.sk / z-lib.sk "
            "from file names."
        )
    )
    parser.add_argument(
        "folder",
        nargs="?",
        default="books",
        help="Folder to process (default: books)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would change without writing files",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Enable debug logging",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s: %(message)s",
    )
    agent = BookCleanerAgent(args.folder, dry_run=args.dry_run)
    results = agent.run()
    print(format_report(results))
    return 1 if any(item.errors for item in results) else 0


if __name__ == "__main__":
    sys.exit(main())
