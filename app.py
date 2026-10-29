import io
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

st.set_page_config(
    page_title="DBI | Digital Banking Intelligence",
    page_icon="◈",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ---------- Theme / layout ----------
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600;700&family=Manrope:wght@400;500;600;700;800&display=swap');
html, body, [class*="css"] { font-family: 'DM Sans', sans-serif; }
.stApp { background: #F5F7FB; color: #172033; }
.block-container { padding-top: 1.4rem; padding-bottom: 2.5rem; max-width: 1500px; }
section[data-testid="stSidebar"] { background: #101B35; }
section[data-testid="stSidebar"] * { color: #EAF0FF !important; }
section[data-testid="stSidebar"] .stCaption { color: #AEBBDD !important; }
h1, h2, h3 { font-family: 'Manrope', sans-serif; letter-spacing: -0.03em; }
.hero {
  background: linear-gradient(120deg, #14264A 0%, #1E477A 62%, #2B7192 100%);
  border-radius: 22px; padding: 28px 32px; color: white; margin-bottom: 20px;
  box-shadow: 0 12px 30px rgba(20,38,74,.12);
}
.hero-eyebrow { font-size: 11px; letter-spacing: .16em; text-transform: uppercase; color: #B8D7F7; font-weight: 700; }
.hero-title { font-family: 'Manrope',sans-serif; font-size: 30px; font-weight: 800; line-height: 1.2; margin: 8px 0; }
.hero-sub { color: #D6E6F7; font-size: 14px; max-width: 850px; }
.section-label { color:#66758F; font-size:11px; letter-spacing:.13em; font-weight:800; text-transform:uppercase; margin: 10px 0 7px; }
.metric-card {
  background: white; border: 1px solid #E5EAF3; border-radius: 16px; padding: 17px 18px;
  box-shadow: 0 4px 14px rgba(20,38,74,.035); min-height: 112px;
}
.metric-label { color:#728099; font-size:12px; font-weight:700; }
.metric-value { color:#172744; font-family:'Manrope',sans-serif; font-size:27px; font-weight:800; margin-top:6px; }
.metric-note { color:#8793A7; font-size:11px; margin-top:3px; }
.panel {
  background:white; border:1px solid #E5EAF3; border-radius:18px; padding:18px 20px;
  box-shadow: 0 4px 14px rgba(20,38,74,.03); margin-bottom: 14px;
}
.panel-title { font-family:'Manrope',sans-serif; font-size:16px; font-weight:800; color:#1B2A46; }
.panel-sub { color:#7A879D; font-size:12px; margin-top:3px; margin-bottom:12px; }
.small-pill { display:inline-block; background:#EAF3FF; color:#245C98; padding:5px 9px; border-radius:99px; font-size:11px; font-weight:700; }
div[data-testid="stDataFrame"] { border: 1px solid #E5EAF3; border-radius: 12px; overflow: hidden; }
div[data-testid="stTabs"] button { font-weight:700; }
div[data-testid="stAlert"] { border-radius: 12px; }
</style>
""", unsafe_allow_html=True)

PILLARS = {
    "Cong_Nghe": "Công nghệ",
    "Khach_Hang": "Khách hàng",
    "Chien_Luoc": "Chiến lược",
    "Van_Hanh": "Vận hành",
    "Van_Hoa": "Văn hóa",
    "Du_Lieu": "Dữ liệu",
}
PILLAR_WEIGHTS = {
    "Cong_Nghe": 29/140, "Khach_Hang": 25/140, "Chien_Luoc": 24/140,
    "Van_Hanh": 22/140, "Van_Hoa": 22/140, "Du_Lieu": 18/140
}
LEVELS = [
    ("Cấp độ 1 · Khởi động", 0, 25, "Số hóa rời rạc, xử lý thủ công, dữ liệu phân mảnh."),
    ("Cấp độ 2 · Bắt đầu", 25, 50, "Ứng dụng công nghệ tại một số mảng kinh doanh cốt lõi."),
    ("Cấp độ 3 · Hình thành", 50, 75, "Hệ thống liên thông cơ bản, mô hình số bắt đầu tạo hiệu quả."),
    ("Cấp độ 4 · Nâng cao", 75, 100, "Tối ưu hóa sâu rộng nhờ dữ liệu số; vận hành chủ yếu trên nền tảng số."),
    ("Cấp độ 5 · Dẫn dắt", 100, 101, "Doanh nghiệp số toàn diện, hệ sinh thái mở và gắn với ESG."),
]
COLORS = ["#4C78A8", "#F2A65A", "#45A58A", "#9276C8", "#D76B6B", "#6C91C2"]

def normalize_col_name(s):
    return str(s).strip().lower().replace(" ", "_")

def load_uploaded(file):
    if file is None:
        return None
    name = file.name.lower()
    try:
        if name.endswith(".csv"):
            return pd.read_csv(file)
        if name.endswith((".xlsx", ".xls")):
            return pd.read_excel(file)
    except Exception as e:
        st.error(f"Không đọc được file {file.name}: {e}")
    return None

def load_local_data():
    candidates = [
        Path("dbi_final_for_step4_scoring.xlsx"),
        Path("dbi_final_truth.xlsx"),
        Path("dbi_clean_for_scoring.xlsx"),
        Path("dbi_final_truth.csv"),
        Path("dbi_final_for_step4_scoring.csv"),
    ]
    for p in candidates:
        if p.exists():
            try:
                df = pd.read_csv(p) if p.suffix.lower() == ".csv" else pd.read_excel(p)
                return df, p.name
            except Exception:
                pass
    return None, None

def identify_col(df, options):
    mapping = {normalize_col_name(c): c for c in df.columns}
    for op in options:
        if normalize_col_name(op) in mapping:
            return mapping[normalize_col_name(op)]
    return None

def prepare_data(df):
    df = df.copy()
    df.columns = [str(c).strip() for c in df.columns]
    bank_col = identify_col(df, ["bank", "bank_name", "ticker", "ngan_hang"])
    year_col = identify_col(df, ["fiscal_year", "year", "nam", "năm"])
    type_col = identify_col(df, ["report_type", "type", "loai_bao_cao"])
    if bank_col is None:
        df["bank"] = "Chưa phân loại"
        bank_col = "bank"
    if year_col is None:
        df["fiscal_year"] = "Không rõ"
        year_col = "fiscal_year"
    if type_col is None:
        df["report_type"] = "Không rõ"
        type_col = "report_type"
    df = df.rename(columns={bank_col: "bank", year_col: "fiscal_year", type_col: "report_type"})
    df["bank"] = df["bank"].fillna("Chưa phân loại").astype(str).str.upper().str.strip()
    df["fiscal_year"] = df["fiscal_year"].fillna("Không rõ").astype(str).str.replace(r"\.0$", "", regex=True)
    df["report_type"] = df["report_type"].fillna("Không rõ").astype(str)

    # Ensure counts/frequencies are numeric.
    for c in df.columns:
        if c.startswith(("count_", "norm_freq_")) or c in ["total_words", "total_dx_hits", "total_pages"]:
            df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0)

    # If raw count columns exist but normalized columns do not, calculate per 10,000 words.
    for key in PILLARS:
        count_col = f"count_{key}"
        freq_col = f"norm_freq_{key}"
        if count_col in df.columns and freq_col not in df.columns:
            words = df["total_words"].replace(0, np.nan) if "total_words" in df.columns else np.nan
            df[freq_col] = (df[count_col] / words * 10000).replace([np.inf, -np.inf], np.nan).fillna(0)
    return df

def make_pillar_summary(df):
    rows = []
    for key, label in PILLARS.items():
        col = f"norm_freq_{key}"
        count_col = f"count_{key}"
        rows.append({
            "pillar_key": key, "Trụ cột": label,
            "Tần suất chuẩn hóa": float(df[col].mean()) if col in df.columns and len(df) else 0,
            "Lượt từ khóa": int(df[count_col].sum()) if count_col in df.columns else 0,
            "Trọng số tham chiếu (%)": PILLAR_WEIGHTS[key] * 100,
        })
    return pd.DataFrame(rows)

def chart_layout(fig, height=330):
    fig.update_layout(
        height=height, margin=dict(l=12, r=12, t=35, b=12),
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        font=dict(family="DM Sans", color="#53627A", size=11),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
    )
    fig.update_xaxes(showgrid=False, linecolor="#E8EDF5")
    fig.update_yaxes(gridcolor="#EEF1F6", zeroline=False)
    return fig

# ---------- Sidebar ----------
with st.sidebar:
    st.markdown("## ◈ DBI STUDIO")
    st.caption("Digital Banking Intelligence")
    st.markdown("---")
    st.markdown("### Dữ liệu đầu vào")
    uploaded = st.file_uploader("Tải file kết quả đã clean", type=["xlsx", "xls", "csv"], help="Ưu tiên dbi_final_for_step4_scoring.xlsx; có thể dùng dbi_final_truth.xlsx hoặc file CSV.")
    uploaded_detail = st.file_uploader("Tải bảng báo cáo chi tiết (không bắt buộc)", type=["xlsx", "xls", "csv"], key="detail_upload")
    st.markdown("---")
    st.markdown("### Bộ lọc")
    st.caption("Bộ lọc áp dụng cho các biểu đồ và bảng bên dưới.")
    st.markdown("---")
    st.caption("DBI · 6 trụ cột · 5 cấp độ")
    st.caption("Bản dashboard phân tích văn bản báo cáo.")

local_df, local_name = load_local_data()
if uploaded is not None:
    raw_df = load_uploaded(uploaded)
    data_source = uploaded.name
elif local_df is not None:
    raw_df = local_df
    data_source = local_name
else:
    raw_df = None
    data_source = "Chưa có file dữ liệu"

if raw_df is None:
    st.markdown("""
    <div class="hero">
      <div class="hero-eyebrow">Digital Banking Intelligence · DBI</div>
      <div class="hero-title">Đo lường mức độ trưởng thành số<br>của ngân hàng</div>
      <div class="hero-sub">Dashboard phân tích tần suất từ khóa chuyển đổi số trong báo cáo thường niên và báo cáo phát triển bền vững của 5 ngân hàng.</div>
    </div>
    """, unsafe_allow_html=True)
    st.warning("Chưa tìm thấy file dữ liệu đầu ra. Hãy tải lên file `dbi_final_for_step4_scoring.xlsx` hoặc `dbi_final_truth.xlsx` ở thanh bên trái. Dashboard không tự tạo số liệu thực tế.")
    st.markdown("#### File phù hợp với notebook `codeclean.ipynb`")
    st.code("dbi_final_for_step4_scoring.xlsx  # dữ liệu tổng hợp theo ngân hàng – năm\n"
            "dbi_final_truth.xlsx             # dữ liệu tần suất theo từng báo cáo\n"
            "dbi_clean_for_scoring.xlsx       # danh sách báo cáo đã lọc")
    st.stop()

df = prepare_data(raw_df)
detail_df = prepare_data(load_uploaded(uploaded_detail)) if uploaded_detail is not None else df.copy()

# Sidebar filters are populated after loading.
with st.sidebar:
    banks = sorted(df["bank"].dropna().unique().tolist())
    years = sorted(df["fiscal_year"].dropna().unique().tolist())
    types = sorted(df["report_type"].dropna().unique().tolist())
    selected_banks = st.multiselect("Ngân hàng", banks, default=banks)
    selected_years = st.multiselect("Năm", years, default=years)
    selected_types = st.multiselect("Loại báo cáo", types, default=types)

filtered = df[
    df["bank"].isin(selected_banks)
    & df["fiscal_year"].isin(selected_years)
    & df["report_type"].isin(selected_types)
].copy()
if filtered.empty:
    st.warning("Không có dữ liệu phù hợp với bộ lọc hiện tại. Hãy chọn lại ngân hàng, năm hoặc loại báo cáo.")
    st.stop()

banks_n = filtered["bank"].nunique()
years_n = filtered["fiscal_year"].nunique()
reports_n = len(filtered)
word_count = int(filtered["total_words"].sum()) if "total_words" in filtered.columns else 0
hit_count = int(filtered["total_dx_hits"].sum()) if "total_dx_hits" in filtered.columns else 0

st.markdown(f"""
<div class="hero">
  <div class="hero-eyebrow">Digital Banking Intelligence · Research dashboard</div>
  <div class="hero-title">Chuyển đổi số ngân hàng<br>được nhìn thấy qua dữ liệu</div>
  <div class="hero-sub">Theo dõi mức độ đề cập đến 6 trụ cột DBI trong báo cáo ngân hàng, so sánh giữa các ngân hàng và qua thời gian. Nguồn dữ liệu hiện tại: <b>{data_source}</b>.</div>
</div>
""", unsafe_allow_html=True)

if "total_words" not in filtered.columns or not any(f"norm_freq_{k}" in filtered.columns for k in PILLARS):
    st.warning("File đã tải lên thiếu một số cột cần thiết (`total_words` hoặc `norm_freq_*`). Một vài KPI/biểu đồ có thể không hiển thị đầy đủ.")

# KPIs
st.markdown('<div class="section-label">Tổng quan dữ liệu</div>', unsafe_allow_html=True)
kpi_cols = st.columns(4)
kpis = [
    ("Ngân hàng", f"{banks_n}", "Đang nằm trong bộ lọc"),
    ("Báo cáo / dòng dữ liệu", f"{reports_n:,}", "Sau khi áp dụng bộ lọc"),
    ("Tổng số từ đã phân tích", f"{word_count:,}" if word_count else "—", "Từ văn bản đã làm sạch"),
    ("Lượt khớp từ khóa DBI", f"{hit_count:,}" if hit_count else "—", "Tổng số lần khớp từ điển"),
]
for col, (label, value, note) in zip(kpi_cols, kpis):
    col.markdown(f'<div class="metric-card"><div class="metric-label">{label}</div><div class="metric-value">{value}</div><div class="metric-note">{note}</div></div>', unsafe_allow_html=True)

st.markdown("")
tab_overview, tab_compare, tab_dictionary, tab_maturity, tab_data = st.tabs([
    "◈  Tổng quan", "⇄  So sánh ngân hàng", "⌕  Từ điển DBI", "◎  Cấp độ trưởng thành", "▤  Dữ liệu & xuất file"
])

with tab_overview:
    left, right = st.columns([1.15, 1])
    with left:
        st.markdown('<div class="panel"><div class="panel-title">Bức tranh 6 trụ cột</div><div class="panel-sub">Tần suất từ khóa trung bình trên 10.000 từ; giá trị cao nghĩa là báo cáo đề cập nhiều hơn.</div>', unsafe_allow_html=True)
        ps = make_pillar_summary(filtered)
        fig = px.bar(ps.sort_values("Tần suất chuẩn hóa"), x="Tần suất chuẩn hóa", y="Trụ cột", orientation="h",
                     color="Trụ cột", color_discrete_sequence=COLORS, text_auto=".1f")
        fig.update_layout(showlegend=False)
        fig.update_traces(textposition="outside", cliponaxis=False)
        st.plotly_chart(chart_layout(fig, 350), use_container_width=True)
        st.markdown('</div>', unsafe_allow_html=True)
    with right:
        st.markdown('<div class="panel"><div class="panel-title">Cường độ đề cập theo năm</div><div class="panel-sub">Tổng lượt khớp từ khóa DBI theo năm; dùng để xem xu hướng, không phải điểm kiểm toán.</div>', unsafe_allow_html=True)
        if "total_dx_hits" in filtered.columns:
            trend = filtered.groupby("fiscal_year", as_index=False)["total_dx_hits"].sum()
            trend["fiscal_year"] = trend["fiscal_year"].astype(str)
            fig = px.line(trend, x="fiscal_year", y="total_dx_hits", markers=True)
            fig.update_traces(line=dict(width=3, color="#2877B7"), marker=dict(size=8, color="#2877B7"))
            st.plotly_chart(chart_layout(fig, 350), use_container_width=True)
        else:
            st.info("File không có cột `total_dx_hits` để dựng xu hướng.")
        st.markdown('</div>', unsafe_allow_html=True)

    st.markdown('<div class="panel"><div class="panel-title">Bản đồ nhiệt · Ngân hàng × Trụ cột</div><div class="panel-sub">Mỗi ô thể hiện tần suất chuẩn hóa trung bình của một ngân hàng ở một trụ cột.</div>', unsafe_allow_html=True)
    freq_cols = [f"norm_freq_{k}" for k in PILLARS if f"norm_freq_{k}" in filtered.columns]
    if freq_cols:
        heat = filtered.groupby("bank", as_index=True)[freq_cols].mean()
        heat.columns = [PILLARS[c.replace("norm_freq_", "")] for c in heat.columns]
        fig = px.imshow(heat, aspect="auto", color_continuous_scale="Blues", text_auto=".1f",
                        labels=dict(x="Trụ cột DBI", y="Ngân hàng", color="Tần suất / 10.000 từ"))
        st.plotly_chart(chart_layout(fig, max(270, 65*len(heat)+100)), use_container_width=True)
    else:
        st.info("Không tìm thấy các cột `norm_freq_*` trong dữ liệu.")
    st.markdown('</div>', unsafe_allow_html=True)

with tab_compare:
    st.markdown('<div class="panel"><div class="panel-title">So sánh cấu trúc chuyển đổi số</div><div class="panel-sub">Chọn tối đa 5 ngân hàng để so sánh hồ sơ 6 trụ cột.</div>', unsafe_allow_html=True)
    compare_banks = st.multiselect("Ngân hàng cần so sánh", sorted(filtered["bank"].unique()), default=sorted(filtered["bank"].unique())[:5], key="compare_banks")
    compare_df = filtered[filtered["bank"].isin(compare_banks)]
    freq_cols = [f"norm_freq_{k}" for k in PILLARS if f"norm_freq_{k}" in compare_df.columns]
    if freq_cols and not compare_df.empty:
        avg = compare_df.groupby("bank", as_index=False)[freq_cols].mean()
        fig = go.Figure()
        for i, (_, row) in enumerate(avg.iterrows()):
            fig.add_trace(go.Scatterpolar(
                r=[row.get(f"norm_freq_{k}", 0) for k in PILLARS if f"norm_freq_{k}" in freq_cols],
                theta=[PILLARS[k] for k in PILLARS if f"norm_freq_{k}" in freq_cols],
                fill="toself", opacity=.18, name=row["bank"],
                line=dict(width=2, color=COLORS[i % len(COLORS)])
            ))
        fig.update_layout(polar=dict(radialaxis=dict(visible=True, gridcolor="#E7ECF4"),
                                     angularaxis=dict(gridcolor="#E7ECF4")),
                          showlegend=True)
        st.plotly_chart(chart_layout(fig, 480), use_container_width=True)
    else:
        st.info("Chưa đủ cột tần suất để vẽ biểu đồ radar.")
    st.markdown('</div>', unsafe_allow_html=True)

    st.markdown('<div class="panel"><div class="panel-title">Bảng so sánh theo ngân hàng</div><div class="panel-sub">Các giá trị tần suất là chỉ báo dựa trên nội dung báo cáo, không phải điểm DBI chính thức.</div>', unsafe_allow_html=True)
    summary = compare_df.groupby("bank", as_index=False).agg(
        So_bao_cao=("file_name", "count") if "file_name" in compare_df.columns else ("bank", "size"),
        Tong_luot_khop=("total_dx_hits", "sum") if "total_dx_hits" in compare_df.columns else ("bank", "size"),
        Tong_so_tu=("total_words", "sum") if "total_words" in compare_df.columns else ("bank", "size")
    )
    for k, label in PILLARS.items():
        c = f"norm_freq_{k}"
        if c in compare_df.columns:
            tmp = compare_df.groupby("bank")[c].mean()
            summary[label] = summary["bank"].map(tmp).round(2)
    st.dataframe(summary, use_container_width=True, hide_index=True)
    st.markdown('</div>', unsafe_allow_html=True)

with tab_dictionary:
    st.markdown('<div class="panel"><div class="panel-title">Tần suất theo 6 trụ cột</div><div class="panel-sub">Phân biệt số lần khớp thô và tần suất đã chuẩn hóa theo độ dài văn bản.</div>', unsafe_allow_html=True)
    ps = make_pillar_summary(filtered)
    st.dataframe(ps.rename(columns={"pillar_key":"Mã trụ cột"}).round(2), use_container_width=True, hide_index=True)
    st.markdown('</div>', unsafe_allow_html=True)

    st.markdown('<div class="panel"><div class="panel-title">Từ khóa nào được nhắc đến nhiều nhất?</div><div class="panel-sub">Notebook hiện lưu số đếm theo trụ cột (`count_*`), chưa xuất tần suất cho từng từ khóa riêng lẻ. Bảng dưới đây thể hiện mức độ đề cập theo trụ cột.</div>', unsafe_allow_html=True)
    count_cols = [f"count_{k}" for k in PILLARS if f"count_{k}" in filtered.columns]
    if count_cols:
        word_summary = filtered[count_cols].sum().rename(lambda c: PILLARS[c.replace("count_", "")]).sort_values(ascending=False).reset_index()
        word_summary.columns = ["Trụ cột", "Lượt khớp"]
        fig = px.bar(word_summary.sort_values("Lượt khớp"), x="Lượt khớp", y="Trụ cột", orientation="h",
                     color="Trụ cột", color_discrete_sequence=COLORS, text_auto=True)
        fig.update_layout(showlegend=False)
        st.plotly_chart(chart_layout(fig, 330), use_container_width=True)
    else:
        st.info("Không tìm thấy các cột `count_*`. Muốn phân tích từng từ khóa, cần bổ sung bước xuất thống kê keyword-level từ notebook.")
    st.markdown('</div>', unsafe_allow_html=True)

with tab_maturity:
    st.markdown('<div class="panel"><div class="panel-title">Thang 5 cấp độ trưởng thành số</div><div class="panel-sub">Ngưỡng hiển thị được lấy từ slide tham khảo. Không tự động gán cấp độ cho ngân hàng nếu chưa có quy tắc quy đổi chỉ số tần suất thành điểm DBI.</div>', unsafe_allow_html=True)
    for name, lo, hi, desc in LEVELS:
        end = "100%" if hi > 100 else f"{hi}%"
        st.markdown(f'<div style="display:flex;gap:14px;align-items:flex-start;padding:13px 0;border-bottom:1px solid #EEF1F6"><div style="min-width:190px;font-weight:800;color:#233655">{name}</div><div style="min-width:85px"><span class="small-pill">{lo}% – {end}</span></div><div style="color:#6E7B91;font-size:13px">{desc}</div></div>', unsafe_allow_html=True)
    st.markdown('</div>', unsafe_allow_html=True)

    st.markdown('<div class="panel"><div class="panel-title">Khoảng trống dữ liệu trước khi chấm cấp độ</div><div class="panel-sub">Để chấm cấp độ có căn cứ, cần xác định công thức chuyển 6 trụ cột thành điểm 0–100 và quy tắc kiểm chứng.</div>', unsafe_allow_html=True)
    st.markdown("""
    - **Bước 1:** quy đổi từng trụ cột về cùng thang điểm, tránh trụ cột có nhiều từ khóa hơn mặc nhiên được điểm cao.
    - **Bước 2:** áp dụng trọng số tham chiếu trong từ điển (tổng trọng số = 1).
    - **Bước 3:** xác định ngưỡng điểm và kiểm tra độ nhạy; tần suất từ khóa cao không đồng nghĩa triển khai thực tế tốt.
    - **Bước 4:** đối chiếu với bằng chứng triển khai và chỉ tiêu vận hành/tài chính như CIR, chi phí hoạt động, giao dịch số nếu có.
    """)
    st.markdown('</div>', unsafe_allow_html=True)

with tab_data:
    st.markdown('<div class="panel"><div class="panel-title">Dữ liệu đang được phân tích</div><div class="panel-sub">Kiểm tra nhanh chất lượng dữ liệu sau bước clean và lọc.</div>', unsafe_allow_html=True)
    st.write(f"**Nguồn:** `{data_source}` · **Số dòng:** {len(filtered):,} · **Số cột:** {len(filtered.columns)}")
    st.dataframe(filtered, use_container_width=True, hide_index=True)
    csv_bytes = filtered.to_csv(index=False).encode("utf-8-sig")
    st.download_button("⬇ Tải dữ liệu đang lọc (CSV)", data=csv_bytes, file_name="dbi_dashboard_filtered.csv", mime="text/csv")
    xlsx_buffer = io.BytesIO()
    with pd.ExcelWriter(xlsx_buffer, engine="openpyxl") as writer:
        filtered.to_excel(writer, index=False, sheet_name="DBI_Filtered")
        make_pillar_summary(filtered).to_excel(writer, index=False, sheet_name="Pillar_Summary")
    st.download_button("⬇ Tải dữ liệu đang lọc (Excel)", data=xlsx_buffer.getvalue(), file_name="dbi_dashboard_filtered.xlsx",
                       mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    st.markdown('</div>', unsafe_allow_html=True)

st.markdown("""
<div style="text-align:center;color:#98A3B6;font-size:11px;padding:18px 0 0">
DBI STUDIO · Dashboard nghiên cứu · Kết quả phụ thuộc vào dữ liệu đầu vào và từ điển từ khóa
</div>
""", unsafe_allow_html=True)
