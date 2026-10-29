# Thu thập báo cáo ngân hàng

Công cụ này tìm và tải BCTC, báo cáo ESG/phát triển bền vững và báo cáo thường niên được liên kết từ website ngân hàng. Báo cáo thường niên được phân loại riêng, không gộp vào ESG. Cấu hình mặc định gồm Vietcombank, BIDV, VietinBank, Agribank và ACB. Công cụ không phân tích nội dung báo cáo.

## Chạy

Yêu cầu Python 3.10 trở lên; không cần cài thêm thư viện:

```powershell
python .\collect_bank_reports.py
```

Tài liệu được lưu trong `data\<ten-ngan-hang>\<nhom-bao-cao>\`, theo các nhóm `financial_statements`, `annual_reports` và `sustainability`. File `data\manifest.csv` ghi ngân hàng, nhóm báo cáo, năm nếu có thể nhận diện, tên tệp, URL nguồn và SHA-256. Lỗi HTTP hoặc tệp bị từ chối được ghi vào `data\errors.csv`.
Có thể chạy lại lệnh sau khi bị gián đoạn: PDF hợp lệ đã có sẽ được giữ lại thay vì tải lại, và manifest sẽ được tạo lại.

Mặc định công cụ theo liên kết lưu trữ đến độ sâu 5, tối đa 250 trang mỗi ngân hàng, với khoảng nghỉ giữa các yêu cầu. Vietcombank được truy vấn theo từng năm từ 2000 đến năm hiện tại; ACB được lấy thêm qua API BCTC, ESG và báo cáo thường niên; Agribank dùng các danh sách BCTC và báo cáo thường niên phân trang của kho WCM; VietinBank có các trang BCTC, báo cáo thường niên, phát triển bền vững và công bố định kỳ. Tăng giới hạn nếu kho báo cáo có nhiều trang:

```powershell
python .\collect_bank_reports.py --max-pages 500 --max-depth 7
```

Các URL bắt đầu được cấu hình trong `report_sources.json`. Nếu ngân hàng đổi cấu trúc trang, hãy cập nhật URL kho báo cáo chính thức tại đó. Một số website tải danh sách bằng JavaScript hoặc API; khi đó crawler HTML thông thường có thể không nhìn thấy tệp và cần bổ sung URL kho báo cáo/API của website đó. Có thể khai báo `extra_pages` để lưu trang thông tin chính thức, chẳng hạn bài viết thay thế có liên quan ESG; các trang này được lưu dạng HTML trong thư mục `sustainability`, không được coi là báo cáo ESG độc lập. Chỉ dùng URL công khai và tôn trọng giới hạn truy cập của từng website.

## Kiểm thử

```powershell
python -m unittest discover -s tests -v
```

## Kiểm tra PDF scan và trích xuất lớp text

Crawler chỉ cần Python chuẩn. Để kiểm tra các PDF có lớp text tra cứu được hay là ảnh scan, cài thêm `pymupdf`:

```powershell
python -m pip install pymupdf
python .\audit_bank_reports.py
```

Lệnh giữ nguyên PDF gốc, ghi kết quả từng tài liệu vào `data\pdf_text_audit.csv` và lưu text trích xuất được vào `data\text_extracts\<ngan-hang>\<nhom-bao-cao>\`. `searchable_text` nghĩa là trích được text trên ít nhất 90% số trang; `mixed_or_sparse_text` nghĩa là chỉ một phần trang có text; `no_extractable_text` nghĩa là không trích được text (có thể là PDF scan hoặc PDF không có nội dung text). Cảnh báo trình đọc PDF được ghi riêng ở cột `parser_warnings`. HTML đã thu thập được đánh dấu `html_source` và không chuyển đổi.

Công cụ không chạy OCR và không thay đổi PDF. Với file không có lớp text, cần kiểm tra nguồn HTML/PDF khác hoặc chạy OCR riêng rồi soát thủ công vì OCR có thể nhận sai số liệu.

## OCR cục bộ PDF không có lớp text

Cài Tesseract OCR và các mô hình ngôn ngữ tiếng Việt/Anh (`vie`, `eng`), cùng PyMuPDF trong môi trường Python. Để OCR các PDF `no_extractable_text` của bộ lọc 3 năm gần nhất:

```powershell
python .\ocr_bank_reports.py
```

PDF gốc không bị sửa. Văn bản OCR UTF-8 được lưu riêng tại `data_3years\ocr\text\` theo ngân hàng/nhóm; `data_3years\ocr\ocr_manifest.csv` ghi hash nguồn và kết quả, số trang có text, trạng thái và hash đầu ra. Có thể chạy lại để tiếp tục: tệp hoàn tất chỉ được bỏ qua khi hash nguồn và đầu ra vẫn khớp. Mặc định dùng tiếng Việt + Anh ở 250 DPI với 4 tiến trình; đổi thư mục mô hình bằng `--tessdata`, độ phân giải bằng `--dpi`, số tiến trình bằng `--workers`, hoặc giới hạn số tệp để kiểm thử bằng `--limit`.

Text OCR chỉ phục vụ tra cứu/phân tích sơ bộ; cần đối chiếu ảnh PDF gốc, đặc biệt các con số, dấu âm/thập phân và bảng.

Để xử lý PDF `mixed_or_sparse_text` theo từng trang, dùng:

```powershell
python .\ocr_bank_reports.py --include-mixed
```

Trang giữ lại text gốc nếu có ít nhất 25 ký tự có nghĩa và không có ký tự điều khiển, surrogate, vùng private-use hoặc unassigned; trang ít text hoặc có các dấu hiệu mã hóa lỗi sẽ được OCR thay thế, không ghép chồng hai bản text. Kết quả ghép lưu riêng tại `data_3years\ocr\mixed_text\`; `mixed_ocr_manifest.csv` ghi hash và số trang theo phương thức, còn `mixed_page_manifest.csv` ghi phương thức cùng độ dài text từng trang. Bản PDF gốc và kết quả OCR 129 PDF không text đều được giữ nguyên. Chạy lại có thể tiếp tục dựa trên hash nguồn/đầu ra; `--limit` hỗ trợ chạy thử số PDF giới hạn trước batch.

## Tạo bộ dữ liệu 3 năm gần nhất

Để tạo một bản làm việc đã lọc mà vẫn giữ nguyên kho gốc `data\`, chạy:

```powershell
python .\build_recent_dataset.py
```

Mặc định, `data_3years\` gồm BCTC năm tài chính 2024–2026, báo cáo thường niên các năm 2023–2025 (ba năm báo cáo thường niên gần nhất hiện có), và toàn bộ tài liệu bền vững/ESG đã thu thập được. PDF gốc, HTML nguồn, manifest, kết quả audit và text extract tương ứng được giữ theo thư mục ngân hàng/nhóm; không chạy OCR. Các BCTC không có năm chỉ được đưa vào khi nguồn/tên tài liệu xác nhận kỳ 2024–2026. Thư mục đích phải chưa tồn tại hoặc đang rỗng.
