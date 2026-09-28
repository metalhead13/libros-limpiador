"""Tests for book_cleaner_agent filename and metadata cleaning."""

from __future__ import annotations

import tempfile
import unittest
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

from pypdf import PdfReader, PdfWriter

from book_cleaner_agent import (
    BookCleanerAgent,
    clean_filename,
    clean_opf_bytes,
    clean_stem,
)


class FilenameCleaningTests(unittest.TestCase):
    def test_strips_all_known_fragments(self) -> None:
        name = "Grokking AI Applications (Andrea De Mauro) (z-library.sk, 1lib.sk, z-lib.sk).pdf"
        self.assertEqual(
            clean_filename(name),
            "Grokking AI Applications (Andrea De Mauro).pdf",
        )

    def test_is_case_insensitive(self) -> None:
        self.assertEqual(
            clean_filename("Title (Z-Library.SK).epub"),
            "Title.epub",
        )

    def test_leaves_unrelated_names_alone(self) -> None:
        self.assertEqual(clean_filename("Clean Book.pdf"), "Clean Book.pdf")

    def test_clean_stem_collapses_empty_parentheses(self) -> None:
        self.assertEqual(clean_stem("Book (z-lib.sk)"), "Book")

    def test_preserves_commas_in_title(self) -> None:
        name = (
            "Machine Learning for Time-Series with Python Forecast, Predict, "
            "and Detect Anomalies (Ben Auffarth) (z-library.sk, 1lib.sk, z-lib.sk).epub"
        )
        self.assertEqual(
            clean_filename(name),
            "Machine Learning for Time-Series with Python Forecast, Predict, "
            "and Detect Anomalies (Ben Auffarth).epub",
        )


class MetadataCleaningTests(unittest.TestCase):
    def test_pdf_metadata_is_cleared(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            pdf_path = folder / "Sample (z-library.sk, 1lib.sk, z-lib.sk).pdf"
            writer = PdfWriter()
            writer.add_blank_page(width=72, height=72)
            writer.add_metadata({"/Title": "Secret Title", "/Author": "Hidden Author"})
            with pdf_path.open("wb") as handle:
                writer.write(handle)

            results = BookCleanerAgent(folder).run()
            self.assertEqual(len(results), 1)
            self.assertTrue(results[0].ok)
            cleaned = results[0].renamed_to
            self.assertIsNotNone(cleaned)
            assert cleaned is not None
            self.assertEqual(cleaned.name, "Sample.pdf")

            meta = PdfReader(str(cleaned)).metadata
            values = [] if meta is None else [str(value) for value in meta.values() if value]
            self.assertNotIn("Secret Title", values)
            self.assertNotIn("Hidden Author", values)

    def test_epub_extra_metadata_is_removed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            epub_path = folder / "Time Series (z-lib.sk).epub"
            _write_sample_epub(epub_path)

            results = BookCleanerAgent(folder).run()
            self.assertEqual(len(results), 1)
            self.assertTrue(results[0].ok)
            cleaned = results[0].renamed_to
            self.assertIsNotNone(cleaned)
            assert cleaned is not None
            self.assertEqual(cleaned.name, "Time Series.epub")

            with zipfile.ZipFile(cleaned) as archive:
                opf = archive.read("OEBPS/content.opf").decode("utf-8")
            self.assertNotIn("Watermark", opf)
            self.assertNotIn("UNKNOWN", opf)
            self.assertIn("Time Series Book", opf)

    def test_opf_bytes_keep_title_and_cover(self) -> None:
        original = b"""<?xml version="1.0" encoding="utf-8"?>
<package version="2.0" xmlns="http://www.idpf.org/2007/opf" unique-identifier="uid">
<metadata xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:opf="http://www.idpf.org/2007/opf">
<dc:title>Demo</dc:title>
<dc:language>en</dc:language>
<dc:identifier id="uid">123</dc:identifier>
<dc:publisher>UNKNOWN</dc:publisher>
<meta name="cover" content="cover-image" />
<meta name="Watermark_(hex)" content="abc" />
</metadata>
</package>
"""
        cleaned = clean_opf_bytes(original).decode("utf-8")
        root = ET.fromstring(cleaned)
        tags = [child.tag.rsplit("}", 1)[-1] for child in root.find("{http://www.idpf.org/2007/opf}metadata")]
        self.assertIn("title", tags)
        self.assertIn("meta", tags)
        self.assertNotIn("publisher", tags)
        self.assertNotIn("Watermark_(hex)", cleaned)


def _write_sample_epub(path: Path) -> None:
    opf = """<?xml version="1.0" encoding="utf-8"?>
<package version="2.0" xmlns="http://www.idpf.org/2007/opf" unique-identifier="uid">
<metadata xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:opf="http://www.idpf.org/2007/opf">
<dc:title>Time Series Book</dc:title>
<dc:language>en</dc:language>
<dc:identifier id="uid">123</dc:identifier>
<dc:creator>Author</dc:creator>
<dc:publisher>UNKNOWN</dc:publisher>
<meta name="cover" content="cover-image" />
<meta name="Watermark_(hex)" content="abc123" />
</metadata>
<manifest>
<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>
</manifest>
<spine toc="ncx"></spine>
</package>
"""
    container = """<?xml version="1.0" encoding="UTF-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
<rootfiles>
<rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>
</rootfiles>
</container>
"""
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        archive.writestr("META-INF/container.xml", container)
        archive.writestr("OEBPS/content.opf", opf)


if __name__ == "__main__":
    unittest.main()
