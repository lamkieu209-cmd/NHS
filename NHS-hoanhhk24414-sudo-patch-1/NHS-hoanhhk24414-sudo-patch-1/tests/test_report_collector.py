import hashlib
import unittest
from email.message import Message
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, patch

from collect_bank_reports import (
    BankSource,
    Link,
    classify_report,
    crawl_bank,
    document_filename,
    extract_year,
    api_documents,
    get_links,
    get_embedded_document_links,
    is_document_url,
    normalize,
    save_document,
    set_page_parameter,
)


class ReportCollectorTests(unittest.TestCase):
    def test_normalize_vietnamese_text(self) -> None:
        self.assertEqual(normalize("Báo cáo phát triển bền vững"), "bao cao phat trien ben vung")

    def test_classifies_financial_and_sustainability_reports(self) -> None:
        self.assertEqual(classify_report("BCTC hợp nhất kiểm toán năm 2024"), "financial_statements")
        self.assertEqual(
            classify_report("Báo cáo phát triển bền vững 2023"),
            "sustainability",
        )
        self.assertEqual(
            classify_report("Báo cáo thường niên Agribank 2025"),
            "annual_reports",
        )
        self.assertEqual(
            classify_report("https://bidv.com.vn/B%C3%A1o+c%C3%A1o+PTBV+2025.pdf"),
            "sustainability",
        )
        self.assertIsNone(classify_report("Báo cáo quản trị công ty"))

    def test_extracts_year_from_title_or_url(self) -> None:
        self.assertEqual(extract_year("BCTC_Q4_2022.pdf"), "2022")
        self.assertEqual(extract_year("BCTC_2009.pdf?archive_year=2023"), "2009")
        self.assertEqual(extract_year("Annual report 2019-2020"), "2020")
        self.assertEqual(extract_year("report.pdf"), "")

    def test_identifies_supported_report_files(self) -> None:
        self.assertTrue(is_document_url("https://example.vn/report.pdf?download=true"))
        self.assertTrue(
            is_document_url("https://example.vn/documents/financials_2024.pdf/file-id")
        )
        self.assertTrue(is_document_url("https://example.vn/financials.xlsx"))
        self.assertFalse(is_document_url("https://example.vn/report.html"))

    def test_extracts_filename_from_document_service_path(self) -> None:
        self.assertEqual(
            document_filename("https://example.vn/documents/BCTC%202024.pdf/file-id"),
            "BCTC 2024.pdf",
        )

    def test_parses_links_and_resolves_relative_urls(self) -> None:
        self.assertEqual(
            get_links(
                b'<a href="/reports/2024.pdf">B\xc3\xa1o c\xc3\xa1o t\xc3\xa0i ch\xc3\xadnh 2024</a>',
                "https://example.vn/investors",
            ),
            [Link("https://example.vn/reports/2024.pdf", "Báo cáo tài chính 2024")],
        )

    def test_parses_document_urls_embedded_in_script_data(self) -> None:
        links = get_embedded_document_links(
            b"file_title: '/files/BCTC_2024.pdf?download=true&amp;id=1'",
            "https://bank.example.vn/reports",
        )
        self.assertEqual(
            links,
            [Link("https://bank.example.vn/files/BCTC_2024.pdf?download=true&id=1", "")],
        )

    def test_sets_api_page_without_dropping_filters(self) -> None:
        url = "https://acb.com.vn/api/posts?limit=100&search%5Bid%5D=42"
        self.assertEqual(
            set_page_parameter(url, 2),
            "https://acb.com.vn/api/posts?limit=100&search%5Bid%5D=42&page=2",
        )

    def test_extracts_downloads_from_sitecore_api_html(self) -> None:
        links, total = api_documents(
            {
                "Count": 1,
                "Results": [
                    {
                        "Html": (
                            '<li data-download-url="/files/BCTC_2024.pdf">'
                            "<p>BCTC 2024</p></li>"
                        )
                    }
                ],
            },
            {"response_format": "sitecore_html"},
            "https://bank.example.vn/",
        )
        self.assertEqual(total, 1)
        self.assertEqual(
            links,
            [Link("https://bank.example.vn/files/BCTC_2024.pdf", "BCTC 2024")],
        )

    def test_extracts_documents_from_agribank_wcm_html(self) -> None:
        links, last_page = api_documents(
            (
                '<div><a href="/wcm/connect/123/BCTC+2024.pdf?MOD=AJPERES">'
                "Báo cáo tài chính năm 2024</a></div>"
            ),
            {"response_format": "agribank_html"},
            "https://www.agribank.com.vn/wcm/connect/agbank/reports",
        )
        self.assertEqual(last_page, 0)
        self.assertEqual(
            links,
            [
                Link(
                    "https://www.agribank.com.vn/wcm/connect/123/BCTC+2024.pdf?MOD=AJPERES",
                    "Báo cáo tài chính năm 2024",
                )
            ],
        )

    def test_extracts_downloads_from_acb_posts_api(self) -> None:
        links, total = api_documents(
            {
                "data": [
                    {
                        "title": "Báo cáo ESG 2024",
                        "featured_image": {
                            "path": "https://acb.com.vn/files/ESG 2024.pdf"
                        },
                    }
                ],
                "meta": {"last_page": 1},
            },
            {"category": "sustainability"},
            "https://acb.com.vn/api/front/v1/posts",
        )
        self.assertEqual(total, 1)
        self.assertEqual(
            links,
            [
                Link(
                    "https://acb.com.vn/files/ESG%202024.pdf",
                    "Báo cáo ESG 2024",
                )
            ],
        )

    def test_adds_acb_annual_category_year_to_section_titles(self) -> None:
        links, total = api_documents(
            {
                "data": [
                    {
                        "title": "Overview chapter",
                        "featured_image": {
                            "path": "https://acb.com.vn/files/chapter.pdf"
                        },
                        "categories": [
                            {"name": "Báo cáo thường niên 2022"}
                        ],
                    }
                ],
                "meta": {"last_page": 1},
            },
            {"category": "annual_reports"},
            "https://acb.com.vn/api/front/v1/posts",
        )
        self.assertEqual(total, 1)
        self.assertEqual(
            links,
            [
                Link(
                    "https://acb.com.vn/files/chapter.pdf",
                    "Overview chapter - Báo cáo thường niên 2022",
                )
            ],
        )

    def test_crawls_extensionless_annual_report_documents(self) -> None:
        bank: BankSource = {
            "name": "Example Bank",
            "slug": "example-bank",
            "seed_urls": ["https://bank.example.vn/annualreports.aspx"],
            "api_sources": [],
            "extra_documents": [],
        }
        page = (
            b'<a href="https://bank.example.vn/documents/20126/annual-id">'
            b"Annual report 2025</a>"
            b'<a href="https://bank.example.vn/documents/20126/annual-id?download=true">'
            b"Download</a>"
        )

        with TemporaryDirectory() as temporary_directory:
            with (
                patch(
                    "collect_bank_reports.read_page",
                    return_value=(page, bank["seed_urls"][0]),
                ),
                patch("collect_bank_reports.save_document", return_value=("sha256", 123)),
            ):
                records, errors, _ = crawl_bank(
                    bank,
                    Path(temporary_directory),
                    max_pages=1,
                    max_depth=0,
                    delay=0,
                    timeout=1,
                )

        self.assertEqual(errors, [])
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["category"], "annual_reports")
        self.assertEqual(records[0]["year"], "2025")
        self.assertIn("download=true", records[0]["source_url"])

    def test_skips_extensionless_non_target_documents_in_archive_pages(self) -> None:
        bank: BankSource = {
            "name": "Example Bank",
            "slug": "example-bank",
            "seed_urls": ["https://bank.example.vn/archive"],
            "api_sources": [],
            "extra_documents": [],
        }
        page = (
            b'<a href="https://bank.example.vn/documents/20126/annual-report-id">'
            b"Reference documents</a>"
        )

        def fake_read_page(url: str, timeout: int) -> tuple[bytes, str]:
            if url != bank["seed_urls"][0]:
                raise AssertionError(f"Attempted to parse a document as a page: {url}")
            return page, url

        with TemporaryDirectory() as temporary_directory:
            with patch("collect_bank_reports.read_page", side_effect=fake_read_page):
                records, errors, pages = crawl_bank(
                    bank,
                    Path(temporary_directory),
                    max_pages=2,
                    max_depth=1,
                    delay=0,
                    timeout=1,
                )

        self.assertEqual(records, [])
        self.assertEqual(errors, [])
        self.assertEqual(pages, 1)

    def test_reuses_existing_valid_pdf_when_resuming(self) -> None:
        url = "https://bank.example.vn/BCTC_2024.pdf"
        bank: BankSource = {
            "name": "Example Bank",
            "slug": "example-bank",
            "seed_urls": ["https://bank.example.vn/reports"],
            "api_sources": [],
            "extra_documents": [
                {
                    "url": url,
                    "category": "financial_statements",
                    "title": "BCTC 2024",
                }
            ],
        }
        content = b"%PDF-1.7 sample"
        file_hash = hashlib.sha256(url.encode("utf-8")).hexdigest()[:10]
        filename = f"example-bank_financial_statements_2024_{file_hash}_BCTC_2024.pdf"
        previous_filename = (
            f"example-bank_financial_statements_2023_{file_hash}_BCTC_2024.pdf"
        )

        with TemporaryDirectory() as temporary_directory:
            output_dir = Path(temporary_directory)
            existing_file = output_dir / "example-bank" / previous_filename
            existing_file.parent.mkdir(parents=True)
            existing_file.write_bytes(content)
            with (
                patch("collect_bank_reports.read_page", return_value=(b"<html></html>", bank["seed_urls"][0])),
                patch("collect_bank_reports.save_document") as save_mock,
            ):
                records, errors, _ = crawl_bank(
                    bank,
                    output_dir,
                    max_pages=1,
                    max_depth=0,
                    delay=0,
                    timeout=1,
                )
            self.assertTrue(
                (output_dir / "example-bank" / "financial_statements" / filename).is_file()
            )
            self.assertFalse(existing_file.exists())

        save_mock.assert_not_called()
        self.assertEqual(errors, [])
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["year"], "2024")
        self.assertEqual(records[0]["sha256"], hashlib.sha256(content).hexdigest())

    def test_archives_extra_webpage_in_category_folder(self) -> None:
        bank: BankSource = {
            "name": "Example Bank",
            "slug": "example-bank",
            "seed_urls": ["https://bank.example.vn/sustainability"],
            "api_sources": [],
            "extra_documents": [],
            "extra_pages": [
                {
                    "url": "https://bank.example.vn/sustainability/green-credit",
                    "category": "sustainability",
                    "title": "Green credit information",
                }
            ],
        }

        with TemporaryDirectory() as temporary_directory:
            with patch(
                "collect_bank_reports.read_page",
                return_value=(b"<html><body>Green credit</body></html>", bank["seed_urls"][0]),
            ):
                records, errors, _ = crawl_bank(
                    bank,
                    Path(temporary_directory),
                    max_pages=1,
                    max_depth=0,
                    delay=0,
                    timeout=1,
                )

        self.assertEqual(errors, [])
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["category"], "sustainability")
        normalized_filename = records[0]["filename"].replace("\\", "/")
        self.assertTrue(normalized_filename.startswith("example-bank/sustainability/"))
        self.assertTrue(records[0]["filename"].endswith(".html"))

    def test_does_not_infer_report_year_from_url_query(self) -> None:
        bank: BankSource = {
            "name": "Example Bank",
            "slug": "example-bank",
            "seed_urls": ["https://bank.example.vn/reports"],
            "api_sources": [],
            "extra_documents": [
                {
                    "url": "https://bank.example.vn/financials.pdf?archive_year=2023",
                    "category": "financial_statements",
                    "title": "Financial statement",
                }
            ],
        }

        with TemporaryDirectory() as temporary_directory:
            with (
                patch("collect_bank_reports.read_page", return_value=(b"<html></html>", bank["seed_urls"][0])),
                patch("collect_bank_reports.save_document", return_value=("sha256", 123)),
            ):
                records, errors, _ = crawl_bank(
                    bank,
                    Path(temporary_directory),
                    max_pages=1,
                    max_depth=0,
                    delay=0,
                    timeout=1,
                )

        self.assertEqual(errors, [])
        self.assertEqual(records[0]["year"], "")

    def test_extra_document_metadata_overrides_discovered_link_label(self) -> None:
        url = "https://bank.example.vn/files/document.pdf"
        bank: BankSource = {
            "name": "Example Bank",
            "slug": "example-bank",
            "seed_urls": ["https://bank.example.vn/reports/sustainability"],
            "api_sources": [],
            "extra_documents": [
                {
                    "url": url,
                    "category": "sustainability",
                    "title": "ESG report 2025",
                }
            ],
        }
        page = f'<a href="{url}">Download</a>'.encode()

        with TemporaryDirectory() as temporary_directory:
            with (
                patch(
                    "collect_bank_reports.read_page",
                    return_value=(page, bank["seed_urls"][0]),
                ),
                patch("collect_bank_reports.save_document", return_value=("sha256", 123)),
            ):
                records, errors, _ = crawl_bank(
                    bank,
                    Path(temporary_directory),
                    max_pages=1,
                    max_depth=0,
                    delay=0,
                    timeout=1,
                )

        self.assertEqual(errors, [])
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["title"], "ESG report 2025")
        self.assertEqual(records[0]["year"], "2025")

    def test_crawls_and_records_api_documents(self) -> None:
        bank: BankSource = {
            "name": "Example Bank",
            "slug": "example-bank",
            "seed_urls": ["https://bank.example.vn/reports"],
            "api_sources": [
                {
                    "url": "https://bank.example.vn/api/posts?limit=100",
                    "category": "financial_statements",
                }
            ],
            "extra_documents": [],
        }

        def fake_read_page(url: str, timeout: int) -> tuple[bytes, str]:
            if "/api/posts" in url:
                return (
                    b'{"data":[{"title":"BCTC 2024","featured_image":{"path":"https://bank.example.vn/BCTC_2024.pdf"}}],"meta":{"last_page":1}}',
                    url,
                )
            return b"<html></html>", url

        with TemporaryDirectory() as temporary_directory:
            output_dir = Path(temporary_directory)
            with (
                patch("collect_bank_reports.read_page", side_effect=fake_read_page),
                patch("collect_bank_reports.save_document", return_value=("sha256", 123)),
            ):
                records, errors, pages = crawl_bank(
                    bank,
                    output_dir,
                    max_pages=2,
                    max_depth=0,
                    delay=0,
                    timeout=1,
                )

        self.assertEqual(errors, [])
        self.assertEqual(pages, 2)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["category"], "financial_statements")
        self.assertEqual(records[0]["year"], "2024")

    def test_associates_download_link_with_title_from_nested_document_anchor(self) -> None:
        bank: BankSource = {
            "name": "Example Bank",
            "slug": "example-bank",
            "seed_urls": ["https://bank.example.vn/sustainability"],
            "api_sources": [],
            "extra_documents": [],
        }
        page = (
            b'<a href="https://bank.example.vn/documents/123">'
            b'<h3>Ba\xcc\x81o ca\xcc\x81o pha\xcc\x81t trie\xcc\x82\xcc\x89n be\xcc\x82\xcc\x80n vu\xcc\x83ng 2025</h3>'
            b'<a href="https://bank.example.vn/documents/123?download=true">Ta\xcc\x89i ve\xcc\x82\xcc\x80</a>'
            b"</a>"
        )

        with TemporaryDirectory() as temporary_directory:
            with (
                patch("collect_bank_reports.read_page", return_value=(page, bank["seed_urls"][0])),
                patch("collect_bank_reports.save_document", return_value=("sha256", 123)),
            ):
                records, errors, _ = crawl_bank(
                    bank,
                    Path(temporary_directory),
                    max_pages=2,
                    max_depth=0,
                    delay=0,
                    timeout=1,
                )

        self.assertEqual(errors, [])
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["category"], "sustainability")
        self.assertIn("2025", records[0]["title"])
        self.assertIn("download=true", records[0]["source_url"])

    def test_downloads_and_validates_pdf_content(self) -> None:
        content = b"%PDF-1.7 sample"
        response = MagicMock()
        response.headers = Message()
        response.headers["Content-Type"] = "application/pdf"
        response.headers["Content-Length"] = str(len(content))
        response.__enter__.return_value = response
        response.__exit__.return_value = False
        response.read.side_effect = [content, b""]

        with TemporaryDirectory() as temporary_directory:
            destination = Path(temporary_directory) / "report.pdf"
            with patch("collect_bank_reports.urlopen", return_value=response):
                digest, size = save_document(
                    "https://bank.example.vn/report.pdf",
                    destination,
                    timeout=1,
                )

            self.assertEqual(destination.read_bytes(), content)
            self.assertEqual(digest, hashlib.sha256(content).hexdigest())
            self.assertEqual(size, len(content))
            self.assertEqual(list(Path(temporary_directory).glob("*.part")), [])


if __name__ == "__main__":
    unittest.main()
