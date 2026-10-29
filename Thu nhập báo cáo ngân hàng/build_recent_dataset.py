from __future__ import annotations

import argparse
import csv
import os
import shutil
import sys
from pathlib import Path
from urllib.parse import unquote_plus, urlsplit


MANIFEST_FIELDS = (
    "bank",
    "category",
    "year",
    "title",
    "filename",
    "source_url",
    "downloaded_at",
    "sha256",
)
FINANCIAL_YEARS = {"2024", "2025", "2026"}
ANNUAL_REPORT_YEARS = {"2023", "2024", "2025"}
SUSTAINABILITY_CATEGORIES = {"sustainability"}
KNOWN_FINANCIAL_YEAR_OVERRIDES = {
    "/cae857f2-e486-446c-8313-3b43bbffe158/bctchngiuaniendoq3.pdf": "2025",
    "/166382/20250814_-_ctg_-_giai_trinh_bien_dong_loi_nhuan_tren_bctc_6t2025_da_soat_xet.pdf": "2025",
    "/166538/cv_cbtt_bctc_quy_ii.2025_va_giai_trinh_bien_dong_loi_nhuan.pdf": "2025",
    "/167115/cv_cbtt_bctc_quy_i.2025_vn.pdf": "2025",
    "/167376/20250331_-_ctg_-_dinh_chinh_thong_tin_trinh_bay_trong_thuyet_minh_bctc_rl_va_hn_nam_2024_da_kiem_toan.pdf": "2024",
    "/167797/20250124_-_ctg_-_bctc_quy_iv.2024_va_giai_trinh_bien_dong_loi_nhuan.pdf": "2024",
    "/169039/20240814_-_ctg_-_cv_cbtt_bctc_6_thang_dau_nam_2024_da_soat_xet.pdf": "2024",
    "/169158/20240730_-_ctg_-_cv_cbtt_bctc_quy_ii.2024.pdf": "2024",
    "/169854/20240426_-_ctg_-_cv_cbtt_bctc_quy_1.2024.pdf": "2024",
}


def financial_year(row: dict[str, str]) -> str:
    year = row["year"].strip()
    if year:
        return year
    source_path = unquote_plus(urlsplit(row["source_url"]).path).lower()
    return next(
        (
            override_year
            for path_suffix, override_year in KNOWN_FINANCIAL_YEAR_OVERRIDES.items()
            if path_suffix in source_path
        ),
        "",
    )


def report_year(row: dict[str, str]) -> str:
    if row["category"] == "financial_statements":
        return financial_year(row)
    return row["year"].strip()


def should_include(row: dict[str, str]) -> bool:
    category = row["category"]
    year = report_year(row)
    if category == "financial_statements":
        return year in FINANCIAL_YEARS
    if category == "annual_reports":
        return year in ANNUAL_REPORT_YEARS
    return category in SUSTAINABILITY_CATEGORIES


def official_filename(source_url: str) -> str:
    for segment in reversed(urlsplit(source_url).path.split("/")):
        decoded = unquote_plus(segment)
        pdf_index = decoded.lower().find(".pdf")
        if pdf_index >= 0:
            return decoded[: pdf_index + 4]
    return ""


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as csv_file:
        return list(csv.DictReader(csv_file))


def write_csv(path: Path, fields: tuple[str, ...] | list[str], rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def build_dataset(
    source_root: Path,
    manifest_path: Path,
    audit_path: Path,
    output_root: Path,
) -> tuple[int, int]:
    if output_root.exists() and any(output_root.iterdir()):
        raise FileExistsError(
            f"Output folder is not empty; choose a new output path: {output_root}"
        )
    output_root.mkdir(parents=True, exist_ok=True)

    manifest_rows = read_csv(manifest_path)
    selected_rows = [row.copy() for row in manifest_rows if should_include(row)]
    selected_sources = {row["source_url"] for row in selected_rows}

    audit_rows = read_csv(audit_path)
    selected_audit = [
        row.copy() for row in audit_rows if row["source_url"] in selected_sources
    ]
    audit_by_source = {row["source_url"]: row for row in selected_audit}
    if selected_sources != set(audit_by_source):
        missing_audit = selected_sources - set(audit_by_source)
        raise ValueError(f"Missing PDF audit records for {len(missing_audit)} selected sources")

    copied_files: set[str] = set()
    for row in selected_rows:
        corrected_year = report_year(row)
        if corrected_year:
            row["year"] = corrected_year
        if row["category"] == "financial_statements" and row["title"].strip().lower() in {
            "tải về",
            "download",
        }:
            row["title"] = official_filename(row["source_url"]) or row["title"]

        relative_source = Path(row["filename"])
        source_file = source_root / relative_source
        if not source_file.is_file():
            raise FileNotFoundError(f"Selected source file is missing: {source_file}")
        destination_file = output_root / relative_source
        destination_file.parent.mkdir(parents=True, exist_ok=True)
        if str(relative_source).casefold() not in copied_files:
            shutil.copy2(source_file, destination_file)
            copied_files.add(str(relative_source).casefold())

        audit_row = audit_by_source[row["source_url"]]
        audit_row["year"] = row["year"]
        audit_row["source_file"] = row["filename"]
        text_source_value = audit_row.get("text_file", "").strip()
        if text_source_value:
            relative_text_source = Path(text_source_value)
            if (
                not relative_text_source.is_absolute()
                and relative_text_source.parts
                and relative_text_source.parts[0].casefold()
                == source_root.name.casefold()
            ):
                relative_text_source = Path(*relative_text_source.parts[1:])
            text_source = (
                relative_text_source
                if relative_text_source.is_absolute()
                else source_root / relative_text_source
            )
            relative_text = Path("text_extracts") / relative_source.with_suffix(".txt")
            destination_text = output_root / relative_text
            if not text_source.is_file():
                raise FileNotFoundError(f"Text extract is missing: {text_source}")
            destination_text.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(text_source, destination_text)
            audit_row["text_file"] = str(relative_text)
        else:
            audit_row["text_file"] = ""

    write_csv(output_root / "manifest.csv", MANIFEST_FIELDS, selected_rows)
    write_csv(output_root / "pdf_text_audit.csv", list(audit_rows[0]), selected_audit)
    return len(selected_rows), len(selected_audit)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build a recent bank-report dataset while preserving original files."
    )
    parser.add_argument("--source", type=Path, default=Path("data"))
    parser.add_argument("--output", type=Path, default=Path("data_3years"))
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--audit", type=Path)
    args = parser.parse_args()
    manifest = args.manifest or args.source / "manifest.csv"
    audit = args.audit or args.source / "pdf_text_audit.csv"
    try:
        selected, audited = build_dataset(args.source, manifest, audit, args.output)
    except (OSError, csv.Error, ValueError) as error:
        print(f"Cannot build filtered dataset: {error}", file=sys.stderr)
        return 2
    print(
        f"Built {selected} selected documents and {audited} audit rows in "
        f"{args.output}; BCTC years 2024-2026, annual reports 2023-2025, "
        "all sustainability documents retained."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
