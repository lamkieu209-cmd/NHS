import csv
import hashlib
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import pymupdf

from ocr_bank_reports import (
    adopt_existing_mixed_output,
    extract_pdf_mixed_ocr,
    needs_page_ocr,
    ocr_manifest,
)


class OcrBankReportsTests(unittest.TestCase):
    def test_ocr_is_selected_for_sparse_or_corrupted_text(self) -> None:
        self.assertTrue(needs_page_ocr("only a few words"))
        self.assertTrue(needs_page_ocr("garbled\x03text with enough characters to pass"))
        self.assertFalse(needs_page_ocr("Readable Vietnamese report text with figures 123"))

    def test_mixed_pdf_ocr_replaces_only_corrupted_page_text(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            source = root / "mixed.pdf"
            destination = root / "mixed.txt"
            with pymupdf.open() as document:
                document.new_page()
                document.new_page()
                document.save(source)

            ocr_marker = object()

            def mock_get_text(page, _option="text", *, textpage=None):
                if textpage is ocr_marker:
                    return "OCR corrected page content"
                return (
                    "Readable native page content with figures 123"
                    if page.number == 0
                    else "Corrupted\x03 native page content that must be replaced"
                )

            with (
                patch.object(pymupdf.Page, "get_text", mock_get_text),
                patch.object(
                    pymupdf.Page, "get_textpage_ocr", return_value=ocr_marker
                ) as ocr_page,
            ):
                result = extract_pdf_mixed_ocr(
                    source, destination, root / "tessdata", "vie+eng", 250, "mixed.pdf"
                )

            page_count, pages_ocr, pages_native, characters, page_records = result
            output = destination.read_text(encoding="utf-8")
            self.assertEqual((page_count, pages_ocr, pages_native), (2, 1, 1))
            self.assertEqual(page_records[0]["method"], "native")
            self.assertEqual(page_records[1]["method"], "ocr")
            self.assertIn("Readable native page content with figures 123", output)
            self.assertIn("OCR corrected page content", output)
            self.assertNotIn("Corrupted", output)
            ocr_page.assert_called_once()

    def test_adopts_complete_mixed_output_and_rebuilds_page_manifest(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            source = root / "mixed.pdf"
            destination = root / "mixed.txt"
            with pymupdf.open() as document:
                document.new_page()
                document.new_page()
                document.save(source)
            source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
            destination.write_text(
                "\n--- Page 1 ---\nReadable native page content 123\n"
                "\n--- Page 2 ---\nOCR corrected short page\n",
                encoding="utf-8",
            )

            def mock_get_text(page, _option="text"):
                return (
                    "Readable native page content 123"
                    if page.number == 0
                    else "Short"
                )

            audit_row = {
                "bank": "Example Bank",
                "category": "financial_statements",
                "year": "2025",
                "title": "Report",
                "source_file": "example/report.pdf",
            }
            with patch.object(pymupdf.Page, "get_text", mock_get_text):
                adopted = adopt_existing_mixed_output(
                    audit_row,
                    source,
                    destination,
                    source_hash,
                    "mixed_text/example/report.txt",
                )

            self.assertIsNotNone(adopted)
            record, page_records = adopted
            self.assertEqual(record["status"], "completed")
            self.assertEqual((record["pages_native"], record["pages_ocr"]), ("1", "1"))
            self.assertEqual(
                [row["method"] for row in page_records], ["native", "ocr"]
            )

    def test_ocr_outputs_separate_text_and_preserves_pdf(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            source_root = root / "data_3years"
            source = source_root / "example" / "financial_statements" / "report.pdf"
            source.parent.mkdir(parents=True)
            with pymupdf.open() as document:
                page = document.new_page()
                page.insert_text((72, 72), "Sample bank report 123")
                document.save(source)
            original_hash = hashlib.sha256(source.read_bytes()).hexdigest()
            manifest = source_root / "manifest.csv"
            audit = source_root / "pdf_text_audit.csv"
            with manifest.open("w", encoding="utf-8-sig", newline="") as output:
                writer = csv.DictWriter(
                    output,
                    fieldnames=("filename", "sha256"),
                )
                writer.writeheader()
                writer.writerow(
                    {
                        "filename": "example/financial_statements/report.pdf",
                        "sha256": original_hash,
                    }
                )
            with audit.open("w", encoding="utf-8-sig", newline="") as output:
                writer = csv.DictWriter(
                    output,
                    fieldnames=(
                        "bank",
                        "category",
                        "year",
                        "title",
                        "file_type",
                        "status",
                        "source_file",
                    ),
                )
                writer.writeheader()
                writer.writerow(
                    {
                        "bank": "Example Bank",
                        "category": "financial_statements",
                        "year": "2025",
                        "title": "Report",
                        "file_type": "pdf",
                        "status": "no_extractable_text",
                        "source_file": "example/financial_statements/report.pdf",
                    }
                )

            def reuse_page_text(page, **_kwargs):
                return page.get_textpage()

            with patch.object(pymupdf.Page, "get_textpage_ocr", reuse_page_text):
                result = ocr_manifest(
                    manifest,
                    audit,
                    source_root,
                    source_root / "ocr",
                    root / "tessdata",
                )

            output_text = (
                source_root / "ocr" / "text" / "example"
                / "financial_statements" / "report.txt"
            )
            with (source_root / "ocr" / "ocr_manifest.csv").open(
                encoding="utf-8-sig", newline=""
            ) as output:
                records = list(csv.DictReader(output))

            self.assertEqual(result, (1, 0, 0))
            self.assertIn("Sample bank report 123", output_text.read_text(encoding="utf-8"))
            self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(), original_hash)
            self.assertEqual(records[0]["status"], "completed")
            self.assertEqual(records[0]["source_sha256"], original_hash)
            self.assertEqual(
                records[0]["output_sha256"],
                hashlib.sha256(output_text.read_bytes()).hexdigest(),
            )

    def test_resumes_only_when_source_and_output_hashes_match(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            source_root = root / "data_3years"
            source = source_root / "example" / "report.pdf"
            source.parent.mkdir(parents=True)
            with pymupdf.open() as document:
                document.new_page()
                document.save(source)
            source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
            manifest = source_root / "manifest.csv"
            audit = source_root / "pdf_text_audit.csv"
            with manifest.open("w", encoding="utf-8-sig", newline="") as output:
                writer = csv.DictWriter(output, fieldnames=("filename", "sha256"))
                writer.writeheader()
                writer.writerow({"filename": "example/report.pdf", "sha256": source_hash})
            with audit.open("w", encoding="utf-8-sig", newline="") as output:
                writer = csv.DictWriter(
                    output,
                    fieldnames=(
                        "bank", "category", "year", "title", "file_type", "status",
                        "source_file",
                    ),
                )
                writer.writeheader()
                writer.writerow(
                    {
                        "bank": "Example Bank",
                        "category": "financial_statements",
                        "year": "2025",
                        "title": "Report",
                        "file_type": "pdf",
                        "status": "no_extractable_text",
                        "source_file": "example/report.pdf",
                    }
                )
            output_root = source_root / "ocr"
            output_file = output_root / "text" / "example" / "report.txt"
            output_file.parent.mkdir(parents=True)
            output_file.write_text("completed OCR", encoding="utf-8")
            output_hash = hashlib.sha256(output_file.read_bytes()).hexdigest()
            with (output_root / "ocr_manifest.csv").open(
                "w", encoding="utf-8-sig", newline=""
            ) as output:
                writer = csv.DictWriter(
                    output,
                    fieldnames=(
                        "bank", "category", "year", "title", "source_file",
                        "source_sha256", "pages", "pages_with_text",
                        "text_characters", "status", "output_file",
                        "output_sha256", "error",
                    ),
                )
                writer.writeheader()
                writer.writerow(
                    {
                        "bank": "Example Bank",
                        "category": "financial_statements",
                        "year": "2025",
                        "title": "Report",
                        "source_file": "example/report.pdf",
                        "source_sha256": source_hash,
                        "pages": "1",
                        "pages_with_text": "1",
                        "text_characters": "14",
                        "status": "completed",
                        "output_file": "text/example/report.txt",
                        "output_sha256": output_hash,
                        "error": "",
                    }
                )

            with patch.object(
                pymupdf.Page,
                "get_textpage_ocr",
                side_effect=AssertionError("Completed item should not be OCRed again"),
            ):
                result = ocr_manifest(
                    manifest, audit, source_root, output_root, root / "tessdata"
                )

            self.assertEqual(result, (0, 0, 1))


if __name__ == "__main__":
    unittest.main()
