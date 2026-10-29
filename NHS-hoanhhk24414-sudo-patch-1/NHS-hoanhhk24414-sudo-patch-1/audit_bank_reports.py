from __future__ import annotations

import argparse
import csv
import os
import sys
import unicodedata
from pathlib import Path

import pymupdf


AUDIT_FIELDS = (
    "bank",
    "category",
    "year",
    "title",
    "file_type",
    "status",
    "pages",
    "pages_with_text",
    "text_characters",
    "text_file",
    "source_file",
    "source_url",
    "parser_warnings",
    "error",
)
MIN_PAGE_TEXT_CHARACTERS = 25


def classify_pdf(pages: int, pages_with_text: int, text_characters: int) -> str:
    if text_characters == 0:
        return "no_extractable_text"
    if pages and pages_with_text / pages < 0.9:
        return "mixed_or_sparse_text"
    return "searchable_text"


def clean_text(text: str) -> str:
    return "".join(
        character
        for character in text
        if unicodedata.category(character) != "Cc"
        or character in {"\t", "\r", "\n"}
    )


def meaningful_characters(text: str) -> int:
    return sum(not character.isspace() for character in text)


def extract_pdf(
    source: Path,
    destination: Path,
) -> tuple[str, int, int, int, str]:
    with pymupdf.open(source) as document:
        if document.is_encrypted:
            return "encrypted", document.page_count, 0, 0, ""

        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name(f".{destination.name}.part")
        pages = document.page_count
        pages_with_text = 0
        text_characters = 0
        complete = False
        try:
            with temporary.open("w", encoding="utf-8", newline="\n") as output:
                for page_number in range(pages):
                    text = clean_text(document.load_page(page_number).get_text())
                    page_characters = meaningful_characters(text)
                    text_characters += page_characters
                    if page_characters >= MIN_PAGE_TEXT_CHARACTERS:
                        pages_with_text += 1
                    output.write(f"\n--- Page {page_number + 1} ---\n")
                    output.write(text)
                    output.write("\n")
            if text_characters:
                os.replace(temporary, destination)
                complete = True
            else:
                temporary.unlink(missing_ok=True)
                destination.unlink(missing_ok=True)
                complete = True
        finally:
            if not complete:
                temporary.unlink(missing_ok=True)

    status = classify_pdf(pages, pages_with_text, text_characters)
    text_file = str(destination) if text_characters else ""
    return status, pages, pages_with_text, text_characters, text_file


def audit_manifest(manifest: Path, output: Path, extract_dir: Path) -> tuple[int, int]:
    with manifest.open(encoding="utf-8-sig", newline="") as source_file:
        rows = list(csv.DictReader(source_file))

    output.parent.mkdir(parents=True, exist_ok=True)
    audited = 0
    errors = 0
    temporary_output = output.with_name(f".{output.name}.part")
    try:
        with temporary_output.open("w", encoding="utf-8-sig", newline="") as csv_file:
            writer = csv.DictWriter(csv_file, fieldnames=AUDIT_FIELDS)
            writer.writeheader()
            for row in rows:
                source = manifest.parent / row["filename"]
                record = {
                    "bank": row["bank"],
                    "category": row["category"],
                    "year": row["year"],
                    "title": row["title"],
                    "file_type": "",
                    "status": "",
                    "pages": "",
                    "pages_with_text": "",
                    "text_characters": "",
                    "text_file": "",
                    "source_file": row["filename"],
                    "source_url": row["source_url"],
                    "parser_warnings": "",
                    "error": "",
                }
                if not source.is_file():
                    record["status"] = "missing_file"
                    record["error"] = "File listed in manifest does not exist"
                    errors += 1
                else:
                    try:
                        with source.open("rb") as file:
                            signature = file.read(16)
                        if signature.startswith(b"%PDF-"):
                            record["file_type"] = "pdf"
                            text_path = extract_dir / Path(row["filename"]).with_suffix(".txt")
                            pymupdf.TOOLS.reset_mupdf_warnings()
                            try:
                                (
                                    record["status"],
                                    record["pages"],
                                    record["pages_with_text"],
                                    record["text_characters"],
                                    record["text_file"],
                                ) = extract_pdf(source, text_path)
                            finally:
                                record["parser_warnings"] = (
                                    pymupdf.TOOLS.mupdf_warnings(reset=True).strip()
                                )
                        elif signature.removeprefix(b"\xef\xbb\xbf").lstrip().lower().startswith(
                            (b"<!doctype", b"<html")
                        ):
                            record["file_type"] = "html"
                            record["status"] = "html_source"
                        else:
                            record["file_type"] = "other"
                            record["status"] = "unsupported_file_type"
                            record["error"] = f"Unrecognized file signature: {signature!r}"
                            errors += 1
                    except (OSError, RuntimeError, ValueError) as error:
                        record["status"] = "parse_error"
                        record["error"] = f"{type(error).__name__}: {error}"
                        errors += 1
                writer.writerow(record)
                audited += 1
                if audited % 100 == 0:
                    csv_file.flush()
                    print(f"Audited {audited}/{len(rows)} files", file=sys.stderr)
        os.replace(temporary_output, output)
    finally:
        temporary_output.unlink(missing_ok=True)
    return audited, errors


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Check bank-report PDFs for selectable text and save text extracts."
    )
    parser.add_argument("--manifest", type=Path, default=Path("data/manifest.csv"))
    parser.add_argument("--output", type=Path, default=Path("data/pdf_text_audit.csv"))
    parser.add_argument("--extract-dir", type=Path, default=Path("data/text_extracts"))
    args = parser.parse_args()

    try:
        audited, errors = audit_manifest(args.manifest, args.output, args.extract_dir)
    except (OSError, csv.Error) as error:
        print(f"PDF audit failed: {error}", file=sys.stderr)
        return 2
    print(
        f"Audited {audited} manifest rows; {errors} file errors. "
        f"See {args.output} and {args.extract_dir}."
    )
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
