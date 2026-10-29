from __future__ import annotations

import argparse
import csv
import hashlib
import os
import re
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from unicodedata import category as unicode_category

import pymupdf


OCR_FIELDS = (
    "bank",
    "category",
    "year",
    "title",
    "source_file",
    "source_sha256",
    "pages",
    "pages_with_text",
    "text_characters",
    "status",
    "output_file",
    "output_sha256",
    "error",
)
MIXED_OCR_FIELDS = (
    "bank",
    "category",
    "year",
    "title",
    "source_file",
    "source_sha256",
    "pages",
    "pages_ocr",
    "pages_native",
    "text_characters",
    "status",
    "output_file",
    "output_sha256",
    "error",
)
MIXED_PAGE_FIELDS = (
    "source_file",
    "page",
    "method",
    "native_characters",
    "output_characters",
)
MIN_PAGE_TEXT_CHARACTERS = 25


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as csv_file:
        return list(csv.DictReader(csv_file))


def write_manifest(path: Path, rows: list[dict[str, str]]) -> None:
    temporary = path.with_name(f".{path.name}.part")
    try:
        with temporary.open("w", encoding="utf-8-sig", newline="") as csv_file:
            writer = csv.DictWriter(csv_file, fieldnames=OCR_FIELDS)
            writer.writeheader()
            writer.writerows(rows)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def write_csv(path: Path, fields: tuple[str, ...], rows: list[dict[str, str]]) -> None:
    temporary = path.with_name(f".{path.name}.part")
    try:
        with temporary.open("w", encoding="utf-8-sig", newline="") as csv_file:
            writer = csv.DictWriter(csv_file, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def needs_page_ocr(text: str) -> bool:
    meaningful_characters = sum(not character.isspace() for character in text)
    if meaningful_characters < MIN_PAGE_TEXT_CHARACTERS:
        return True
    return any(
        unicode_category(character) in {"Cc", "Cs", "Co", "Cn"}
        and character not in "\t\r\n"
        for character in text
    )


def extract_pdf_ocr(
    source: Path,
    destination: Path,
    tessdata: Path,
    language: str,
    dpi: int,
) -> tuple[int, int, int]:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.part")
    pages_with_text = 0
    text_characters = 0
    complete = False
    try:
        with pymupdf.open(source) as document:
            if document.is_encrypted:
                raise RuntimeError("Encrypted PDF cannot be OCRed without a password")
            with temporary.open("w", encoding="utf-8", newline="\n") as output:
                for page_number in range(document.page_count):
                    page = document.load_page(page_number)
                    text_page = page.get_textpage_ocr(
                        language=language,
                        dpi=dpi,
                        full=True,
                        tessdata=str(tessdata),
                    )
                    text = page.get_text("text", textpage=text_page)
                    page_characters = sum(not character.isspace() for character in text)
                    text_characters += page_characters
                    if page_characters >= MIN_PAGE_TEXT_CHARACTERS:
                        pages_with_text += 1
                    output.write(f"\n--- Page {page_number + 1} ---\n")
                    output.write(text)
                    output.write("\n")
                page_count = document.page_count
        os.replace(temporary, destination)
        complete = True
    finally:
        if not complete:
            temporary.unlink(missing_ok=True)
    return page_count, pages_with_text, text_characters


def extract_pdf_mixed_ocr(
    source: Path,
    destination: Path,
    tessdata: Path,
    language: str,
    dpi: int,
    source_relative: str,
) -> tuple[int, int, int, int, list[dict[str, str]]]:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.part")
    pages_ocr = 0
    pages_native = 0
    text_characters = 0
    page_records = []
    complete = False
    try:
        with pymupdf.open(source) as document:
            if document.is_encrypted:
                raise RuntimeError("Encrypted PDF cannot be OCRed without a password")
            with temporary.open("w", encoding="utf-8", newline="\n") as output:
                for page_number in range(document.page_count):
                    page = document.load_page(page_number)
                    native_text = page.get_text("text")
                    native_characters = sum(
                        not character.isspace() for character in native_text
                    )
                    if needs_page_ocr(native_text):
                        text_page = page.get_textpage_ocr(
                            language=language,
                            dpi=dpi,
                            full=True,
                            tessdata=str(tessdata),
                        )
                        text = page.get_text("text", textpage=text_page)
                        method = "ocr"
                        pages_ocr += 1
                    else:
                        text = native_text
                        method = "native"
                        pages_native += 1
                    output_characters = sum(
                        not character.isspace() for character in text
                    )
                    text_characters += output_characters
                    output.write(f"\n--- Page {page_number + 1} ---\n")
                    output.write(text)
                    output.write("\n")
                    page_records.append(
                        {
                            "source_file": source_relative,
                            "page": str(page_number + 1),
                            "method": method,
                            "native_characters": str(native_characters),
                            "output_characters": str(output_characters),
                        }
                    )
                page_count = document.page_count
        os.replace(temporary, destination)
        complete = True
    finally:
        if not complete:
            temporary.unlink(missing_ok=True)
    return page_count, pages_ocr, pages_native, text_characters, page_records


def process_pdf_ocr(
    audit_row: dict[str, str],
    source: Path,
    expected_source_hash: str,
    destination: Path,
    output_relative: str,
    tessdata: Path,
    language: str,
    dpi: int,
) -> dict[str, str]:
    record = {
        "bank": audit_row["bank"],
        "category": audit_row["category"],
        "year": audit_row["year"],
        "title": audit_row["title"],
        "source_file": audit_row["source_file"],
        "source_sha256": "",
        "pages": "",
        "pages_with_text": "",
        "text_characters": "",
        "status": "failed",
        "output_file": "",
        "output_sha256": "",
        "error": "",
    }
    temporary = destination.with_name(f".{destination.name}.part")
    temporary.unlink(missing_ok=True)
    try:
        if not source.is_file():
            raise FileNotFoundError(f"Source PDF does not exist: {source}")
        actual_source_hash = sha256_file(source)
        record["source_sha256"] = actual_source_hash
        if actual_source_hash != expected_source_hash:
            raise ValueError(
                f"Source SHA-256 mismatch for {audit_row['source_file']}: "
                f"expected {expected_source_hash}, got {actual_source_hash}"
            )
        pages, pages_with_text, text_characters = extract_pdf_ocr(
            source, destination, tessdata, language, dpi
        )
        if sha256_file(source) != actual_source_hash:
            destination.unlink(missing_ok=True)
            raise ValueError(f"Source PDF changed during OCR: {audit_row['source_file']}")
        record.update(
            {
                "pages": str(pages),
                "pages_with_text": str(pages_with_text),
                "text_characters": str(text_characters),
                "status": "completed" if text_characters else "completed_no_text",
                "output_file": output_relative,
                "output_sha256": sha256_file(destination),
            }
        )
    except (OSError, RuntimeError, ValueError) as error:
        record["error"] = f"{type(error).__name__}: {error}"
    return record


def process_pdf_mixed_ocr(
    audit_row: dict[str, str],
    source: Path,
    expected_source_hash: str,
    destination: Path,
    output_relative: str,
    tessdata: Path,
    language: str,
    dpi: int,
) -> tuple[dict[str, str], list[dict[str, str]]]:
    record = {
        "bank": audit_row["bank"],
        "category": audit_row["category"],
        "year": audit_row["year"],
        "title": audit_row["title"],
        "source_file": audit_row["source_file"],
        "source_sha256": "",
        "pages": "",
        "pages_ocr": "",
        "pages_native": "",
        "text_characters": "",
        "status": "failed",
        "output_file": "",
        "output_sha256": "",
        "error": "",
    }
    page_records: list[dict[str, str]] = []
    temporary = destination.with_name(f".{destination.name}.part")
    temporary.unlink(missing_ok=True)
    try:
        if not source.is_file():
            raise FileNotFoundError(f"Source PDF does not exist: {source}")
        actual_source_hash = sha256_file(source)
        record["source_sha256"] = actual_source_hash
        if actual_source_hash != expected_source_hash:
            raise ValueError(
                f"Source SHA-256 mismatch for {audit_row['source_file']}: "
                f"expected {expected_source_hash}, got {actual_source_hash}"
            )
        pages, pages_ocr, pages_native, text_characters, page_records = (
            extract_pdf_mixed_ocr(
                source,
                destination,
                tessdata,
                language,
                dpi,
                audit_row["source_file"],
            )
        )
        if sha256_file(source) != actual_source_hash:
            destination.unlink(missing_ok=True)
            raise ValueError(f"Source PDF changed during OCR: {audit_row['source_file']}")
        record.update(
            {
                "pages": str(pages),
                "pages_ocr": str(pages_ocr),
                "pages_native": str(pages_native),
                "text_characters": str(text_characters),
                "status": "completed",
                "output_file": output_relative,
                "output_sha256": sha256_file(destination),
            }
        )
    except (OSError, RuntimeError, ValueError) as error:
        record["error"] = f"{type(error).__name__}: {error}"
    return record, page_records


def adopt_existing_mixed_output(
    audit_row: dict[str, str],
    source: Path,
    destination: Path,
    expected_source_hash: str,
    output_relative: str,
) -> tuple[dict[str, str], list[dict[str, str]]] | None:
    if not source.is_file() or not destination.is_file():
        return None
    actual_source_hash = sha256_file(source)
    if actual_source_hash != expected_source_hash:
        return None
    output = destination.read_text(encoding="utf-8")
    sections = re.split(r"\n--- Page (\d+) ---\n", output)
    if len(sections) < 3 or len(sections) % 2 == 0:
        return None
    page_texts = []
    for index in range(1, len(sections), 2):
        expected_page = len(page_texts) + 1
        if sections[index] != str(expected_page):
            return None
        page_texts.append(sections[index + 1].rstrip("\n"))

    page_records = []
    pages_ocr = pages_native = text_characters = 0
    with pymupdf.open(source) as document:
        if document.is_encrypted or document.page_count != len(page_texts):
            return None
        for page_number, (page, output_text) in enumerate(
            zip(document, page_texts, strict=True), start=1
        ):
            native_text = page.get_text("text")
            native_characters = sum(
                not character.isspace() for character in native_text
            )
            if needs_page_ocr(native_text):
                method = "ocr"
                pages_ocr += 1
            else:
                method = "native"
                pages_native += 1
                if output_text != native_text.rstrip("\n"):
                    return None
            output_characters = sum(
                not character.isspace() for character in output_text
            )
            text_characters += output_characters
            page_records.append(
                {
                    "source_file": audit_row["source_file"],
                    "page": str(page_number),
                    "method": method,
                    "native_characters": str(native_characters),
                    "output_characters": str(output_characters),
                }
            )

    record = {
        "bank": audit_row["bank"],
        "category": audit_row["category"],
        "year": audit_row["year"],
        "title": audit_row["title"],
        "source_file": audit_row["source_file"],
        "source_sha256": actual_source_hash,
        "pages": str(len(page_texts)),
        "pages_ocr": str(pages_ocr),
        "pages_native": str(pages_native),
        "text_characters": str(text_characters),
        "status": "completed",
        "output_file": output_relative,
        "output_sha256": sha256_file(destination),
        "error": "",
    }
    return record, page_records


def ocr_manifest(
    manifest_path: Path,
    audit_path: Path,
    source_root: Path,
    output_root: Path,
    tessdata: Path,
    language: str = "vie+eng",
    dpi: int = 250,
    limit: int | None = None,
    workers: int = 1,
) -> tuple[int, int, int]:
    if workers < 1:
        raise ValueError("workers must be a positive integer")
    manifest_rows = read_csv(manifest_path)
    audit_rows = read_csv(audit_path)
    manifest_by_file = {row["filename"]: row for row in manifest_rows}
    targets = [
        row
        for row in audit_rows
        if row["file_type"] == "pdf" and row["status"] == "no_extractable_text"
    ]
    if limit is not None:
        targets = targets[:limit]

    for audit_row in targets:
        if audit_row["source_file"] not in manifest_by_file:
            raise ValueError(
                f"PDF audit source is missing from manifest: {audit_row['source_file']}"
            )

    output_root.mkdir(parents=True, exist_ok=True)
    output_manifest = output_root / "ocr_manifest.csv"
    previous = {
        row["source_file"]: row
        for row in read_csv(output_manifest)
    } if output_manifest.is_file() else {}
    records_by_source: dict[str, dict[str, str]] = {}
    tasks = []
    completed = 0
    failed = 0
    skipped = 0
    started = time.monotonic()

    for audit_row in targets:
        source_relative = Path(audit_row["source_file"])
        source = source_root / source_relative
        output_relative = Path("text") / source_relative.with_suffix(".txt")
        output_relative_text = output_relative.as_posix()
        destination = output_root / output_relative
        source_metadata = manifest_by_file[audit_row["source_file"]]
        expected_source_hash = source_metadata["sha256"]
        prior = previous.get(audit_row["source_file"])

        if (
            prior
            and prior["status"] in {"completed", "completed_no_text"}
            and prior["source_sha256"] == expected_source_hash
            and Path(prior["output_file"]).as_posix() == output_relative_text
            and destination.is_file()
            and sha256_file(destination) == prior["output_sha256"]
        ):
            records_by_source[audit_row["source_file"]] = prior
            skipped += 1
            continue

        tasks.append(
            (
                audit_row,
                source,
                expected_source_hash,
                destination,
                output_relative_text,
                tessdata,
                language,
                dpi,
            )
        )

    def save_checkpoint() -> None:
        checkpoint = [
            records_by_source[source_file]
            for source_file in (row["source_file"] for row in targets)
            if source_file in records_by_source
        ]
        checkpoint.extend(
            previous[source_file]
            for source_file in (row["source_file"] for row in targets)
            if source_file not in records_by_source and source_file in previous
        )
        write_manifest(output_manifest, checkpoint)

    if workers == 1:
        results = (process_pdf_ocr(*task) for task in tasks)
        for record in results:
            records_by_source[record["source_file"]] = record
            if record["status"] == "failed":
                failed += 1
            else:
                completed += 1
            save_checkpoint()
            processed = completed + failed
            processed_total = skipped + processed
            if processed % 5 == 0 or processed == len(tasks):
                elapsed = time.monotonic() - started
                print(
                    f"Processed {processed_total}/{len(targets)}; "
                    f"completed this run={completed}, failed={failed}, "
                    f"resumed={skipped}, elapsed={elapsed:.0f}s",
                    flush=True,
                )
    else:
        with ProcessPoolExecutor(max_workers=workers) as executor:
            futures = [executor.submit(process_pdf_ocr, *task) for task in tasks]
            for future in as_completed(futures):
                record = future.result()
                records_by_source[record["source_file"]] = record
                if record["status"] == "failed":
                    failed += 1
                else:
                    completed += 1
                save_checkpoint()
                processed = completed + failed
                processed_total = skipped + processed
                if processed % 5 == 0 or processed == len(tasks):
                    elapsed = time.monotonic() - started
                    print(
                        f"Processed {processed_total}/{len(targets)}; "
                        f"completed this run={completed}, failed={failed}, "
                        f"resumed={skipped}, elapsed={elapsed:.0f}s",
                        flush=True,
                    )
    save_checkpoint()
    return completed, failed, skipped


def ocr_mixed_manifest(
    manifest_path: Path,
    audit_path: Path,
    source_root: Path,
    output_root: Path,
    tessdata: Path,
    language: str = "vie+eng",
    dpi: int = 250,
    limit: int | None = None,
    workers: int = 1,
) -> tuple[int, int, int]:
    if workers < 1:
        raise ValueError("workers must be a positive integer")
    manifest_rows = read_csv(manifest_path)
    audit_rows = read_csv(audit_path)
    manifest_by_file = {row["filename"]: row for row in manifest_rows}
    targets = [
        row
        for row in audit_rows
        if row["file_type"] == "pdf" and row["status"] == "mixed_or_sparse_text"
    ]
    if limit is not None:
        targets = targets[:limit]
    for audit_row in targets:
        if audit_row["source_file"] not in manifest_by_file:
            raise ValueError(
                f"PDF audit source is missing from manifest: {audit_row['source_file']}"
            )

    output_root.mkdir(parents=True, exist_ok=True)
    output_manifest = output_root / "mixed_ocr_manifest.csv"
    page_manifest = output_root / "mixed_page_manifest.csv"
    previous = {
        row["source_file"]: row
        for row in read_csv(output_manifest)
    } if output_manifest.is_file() else {}
    previous_pages = {
        row["source_file"]: []
        for row in read_csv(page_manifest)
    } if page_manifest.is_file() else {}
    if page_manifest.is_file():
        for page_row in read_csv(page_manifest):
            previous_pages.setdefault(page_row["source_file"], []).append(page_row)

    records_by_source: dict[str, dict[str, str]] = {}
    pages_by_source: dict[str, list[dict[str, str]]] = {}
    tasks = []
    completed = failed = skipped = 0
    started = time.monotonic()
    for audit_row in targets:
        source_relative = Path(audit_row["source_file"])
        source = source_root / source_relative
        output_relative = Path("mixed_text") / source_relative.with_suffix(".txt")
        output_relative_text = output_relative.as_posix()
        destination = output_root / output_relative
        expected_source_hash = manifest_by_file[audit_row["source_file"]]["sha256"]
        prior = previous.get(audit_row["source_file"])
        prior_pages = previous_pages.get(audit_row["source_file"], [])
        try:
            output_matches = (
                prior is not None
                and Path(prior["output_file"]).as_posix() == output_relative_text
                and destination.is_file()
                and sha256_file(destination) == prior["output_sha256"]
            )
        except OSError:
            output_matches = False
        if (
            prior
            and prior["status"] == "completed"
            and prior["source_sha256"] == expected_source_hash
            and output_matches
            and len(prior_pages) == int(prior["pages"])
        ):
            records_by_source[audit_row["source_file"]] = prior
            pages_by_source[audit_row["source_file"]] = prior_pages
            skipped += 1
            continue
        if prior is None:
            adopted = adopt_existing_mixed_output(
                audit_row,
                source,
                destination,
                expected_source_hash,
                output_relative_text,
            )
            if adopted is not None:
                record, page_rows = adopted
                records_by_source[audit_row["source_file"]] = record
                pages_by_source[audit_row["source_file"]] = page_rows
                previous[audit_row["source_file"]] = record
                previous_pages[audit_row["source_file"]] = page_rows
                skipped += 1
                continue
        tasks.append(
            (
                audit_row,
                source,
                expected_source_hash,
                destination,
                output_relative_text,
                tessdata,
                language,
                dpi,
            )
        )

    def save_checkpoint() -> None:
        ordered_files = [row["source_file"] for row in targets]
        records = [
            records_by_source[source_file]
            for source_file in ordered_files
            if source_file in records_by_source
        ]
        for source_file in ordered_files:
            if source_file not in records_by_source and source_file in previous:
                records.append(previous[source_file])
        page_rows = [
            page_row
            for source_file in ordered_files
            for page_row in pages_by_source.get(
                source_file, previous_pages.get(source_file, [])
            )
        ]
        write_csv(output_manifest, MIXED_OCR_FIELDS, records)
        write_csv(page_manifest, MIXED_PAGE_FIELDS, page_rows)

    def consume(results) -> None:
        nonlocal completed, failed
        for record, page_rows in results:
            source_file = record["source_file"]
            records_by_source[source_file] = record
            if record["status"] == "failed":
                failed += 1
            else:
                completed += 1
                pages_by_source[source_file] = page_rows
            save_checkpoint()
            processed = completed + failed
            if processed % 5 == 0 or processed == len(tasks):
                print(
                    f"Processed {skipped + processed}/{len(targets)} mixed PDFs; "
                    f"completed this run={completed}, failed={failed}, "
                    f"resumed={skipped}, elapsed={time.monotonic() - started:.0f}s",
                    flush=True,
                )

    if workers == 1:
        consume(process_pdf_mixed_ocr(*task) for task in tasks)
    else:
        with ProcessPoolExecutor(max_workers=workers) as executor:
            futures = [executor.submit(process_pdf_mixed_ocr, *task) for task in tasks]
            consume(future.result() for future in as_completed(futures))
    save_checkpoint()
    return completed, failed, skipped


def main() -> int:
    parser = argparse.ArgumentParser(
        description="OCR selected pages of bank-report PDFs into UTF-8 text files."
    )
    parser.add_argument("--source", type=Path, default=Path("data_3years"))
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--audit", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--tessdata", type=Path, default=Path("data/ocr_models_standard")
    )
    parser.add_argument("--language", default="vie+eng")
    parser.add_argument("--dpi", type=int, default=250)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument(
        "--include-mixed",
        action="store_true",
        help="OCR only missing or corrupted-text pages in mixed/sparse PDFs.",
    )
    args = parser.parse_args()
    manifest = args.manifest or args.source / "manifest.csv"
    audit = args.audit or args.source / "pdf_text_audit.csv"
    output = args.output or args.source / "ocr"
    try:
        if args.dpi < 1:
            raise ValueError("--dpi must be a positive integer")
        if args.include_mixed:
            completed, failed, skipped = ocr_mixed_manifest(
                manifest,
                audit,
                args.source,
                output,
                args.tessdata,
                args.language,
                args.dpi,
                args.limit,
                args.workers,
            )
        else:
            completed, failed, skipped = ocr_manifest(
                manifest,
                audit,
                args.source,
                output,
                args.tessdata,
                args.language,
                args.dpi,
                args.limit,
                args.workers,
            )
    except (OSError, csv.Error, RuntimeError, ValueError) as error:
        print(f"OCR failed: {type(error).__name__}: {error}", file=sys.stderr)
        return 2
    manifest_name = "mixed_ocr_manifest.csv" if args.include_mixed else "ocr_manifest.csv"
    print(
        f"OCR run finished: {completed} completed, {failed} failed, "
        f"{skipped} resumed. Manifest: {output / manifest_name}"
    )
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
