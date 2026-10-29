# DBI Studio — Dashboard chuyển đổi số ngân hàng

## Chạy dashboard
1. Cài Python 3.10 trở lên.
2. Mở Terminal trong thư mục này.
3. Chạy:
   ```bash
   pip install -r requirements.txt
   streamlit run app.py
   ```
4. Trong sidebar, tải file kết quả notebook:
   - `dbi_final_for_step4_scoring.xlsx` (ưu tiên: dữ liệu tổng hợp ngân hàng–năm)
   - hoặc `dbi_final_truth.xlsx` (dữ liệu theo từng báo cáo)
   - hoặc `dbi_clean_for_scoring.xlsx`.

## Ghi chú về diễn giải
- Dashboard hiển thị tần suất từ khóa DBI từ nội dung báo cáo, không khẳng định đó là điểm trưởng thành số chính thức.
- Các ngưỡng 5 cấp độ được đưa vào từ slide tham khảo; cần xác định quy tắc quy đổi điểm 0–100 trước khi tự động xếp cấp độ.
- Notebook hiện tại tổng hợp số lần khớp theo 6 trụ cột; chưa có thống kê riêng từng từ khóa và chưa có biến CIR/hiệu quả hoạt động trừ khi người dùng bổ sung.
