"""视频播放量统计表：一个文件一直累加，同一天同一主页只保留最后一次。"""
from datetime import datetime
from pathlib import Path

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font, PatternFill

from videos import BUCKET_NAMES

DETAIL = "明细"
SUMMARY = "汇总"

DETAIL_HEADERS = (
    ["检测日期", "窗口序号", "窗口名称", "主页名称", "主页ID", "粉丝数", "检测视频数"]
    + BUCKET_NAMES
    + ["总播放量", "平均播放量", "比上次", "最高播放量", "新增视频数", "没读到播放量的视频数"]
)
SUMMARY_HEADERS = (
    ["检测日期", "主页数", "检测视频数"] + BUCKET_NAMES + ["总播放量", "平均播放量", "比上次"]
)

GREEN = (PatternFill("solid", fgColor="C6EFCE"), Font(color="006100"))
RED = (PatternFill("solid", fgColor="FFC7CE"), Font(color="9C0006"))
HEADER_FILL = PatternFill("solid", fgColor="DDEBF7")


def _sheet(wb, name, headers):
    if name in wb.sheetnames:
        return wb[name]
    ws = wb.create_sheet(name)
    ws.append(headers)
    for cell in ws[1]:
        cell.font = Font(bold=True)
        cell.fill = HEADER_FILL
        cell.alignment = Alignment(horizontal="center", wrap_text=True)
    for i, h in enumerate(headers, start=1):
        ws.column_dimensions[ws.cell(row=1, column=i).column_letter].width = max(10, len(h) * 2 + 2)
    ws.freeze_panes = "A2"
    return ws


def _delete_rows(ws, match):
    for r in range(ws.max_row, 1, -1):
        if match([c.value for c in ws[r]]):
            ws.delete_rows(r)


def _previous_avg(ws, today, avg_col, key_col=None, key=None):
    """找这个主页（或汇总）今天之前最近一次的平均播放量。"""
    prev = None
    for row in ws.iter_rows(min_row=2, values_only=True):
        if not row[0] or str(row[0]) >= today:
            continue
        if key_col is not None and str(row[key_col]) != str(key):
            continue
        if row[avg_col] is not None and (prev is None or str(row[0]) >= prev[0]):
            prev = (str(row[0]), row[avg_col])
    return prev[1] if prev else None


def _append_with_color(ws, values, avg_col, diff_col, prev):
    avg = values[avg_col]
    if prev is not None and avg is not None:
        values[diff_col] = round(avg - prev, 1)
    ws.append(values)
    r = ws.max_row
    if prev is None or avg is None or avg == prev:
        return
    fill, font = GREEN if avg > prev else RED
    for col in (avg_col, diff_col):
        cell = ws.cell(row=r, column=col + 1)
        cell.fill, cell.font = fill, font


def write(path, page_rows):
    """page_rows: [dict(seq, window, page_name, page_id, followers, stats, new_count), ...]
    返回实际保存的文件路径（原文件被 Excel 占用时会另存一份）。"""
    path = Path(path)
    today = datetime.now().strftime("%Y-%m-%d")
    if path.exists():
        wb = load_workbook(path)
    else:
        wb = Workbook()
        wb.remove(wb.active)
    detail = _sheet(wb, DETAIL, DETAIL_HEADERS)
    summary = _sheet(wb, SUMMARY, SUMMARY_HEADERS)

    d_avg, d_diff = DETAIL_HEADERS.index("平均播放量"), DETAIL_HEADERS.index("比上次")
    ids_today = {str(p["page_id"]) for p in page_rows}
    _delete_rows(detail, lambda v: str(v[0]) == today and str(v[4]) in ids_today)
    for p in page_rows:
        st = p["stats"]
        values = (
            [today, p["seq"], p["window"], p["page_name"], p["page_id"], p["followers"], st["count"]]
            + st["buckets"]
            + [st["total"], st["avg"], None, st["max"], p["new_count"], st["unknown"]]
        )
        prev = _previous_avg(detail, today, d_avg, key_col=4, key=p["page_id"])
        _append_with_color(detail, values, d_avg, d_diff, prev)

    # 汇总按今天明细里的所有主页重新算（同一天分几次跑也能合在一起）
    today_rows = [
        [c.value for c in row] for row in detail.iter_rows(min_row=2)
        if str(row[0].value) == today
    ]
    if today_rows:
        b0 = DETAIL_HEADERS.index(BUCKET_NAMES[0])
        count = sum(r[6] or 0 for r in today_rows)
        d_total = DETAIL_HEADERS.index("总播放量")
        total = sum(r[d_total] or 0 for r in today_rows)
        buckets = [sum(r[b0 + i] or 0 for r in today_rows) for i in range(len(BUCKET_NAMES))]
        s_avg, s_diff = SUMMARY_HEADERS.index("平均播放量"), SUMMARY_HEADERS.index("比上次")
        _delete_rows(summary, lambda v: str(v[0]) == today)
        prev = _previous_avg(summary, today, s_avg)
        values = ([today, len(today_rows), count] + buckets
                  + [total, round(total / count, 1) if count else None, None])
        _append_with_color(summary, values, s_avg, s_diff, prev)

    try:
        wb.save(path)
        return path
    except PermissionError:
        alt = path.with_name(f"{path.stem}_{datetime.now():%H%M%S}（原文件被占用）.xlsx")
        wb.save(alt)
        return alt
