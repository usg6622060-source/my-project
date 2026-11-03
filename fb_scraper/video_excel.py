"""视频播放量统计表：一个文件一直累加，每个检测日期一行（所有主页加起来），同一天只保留最后一次。"""
from datetime import datetime
from pathlib import Path

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font, PatternFill

from videos import BUCKET_NAMES

SHEET = "播放量统计"
OLD_SHEETS = ("明细", "汇总")  # 第一版的格式，不再使用

HEADERS = (
    ["检测日期", "主页数", "粉丝数", "检测视频数"]
    + BUCKET_NAMES
    + ["总播放量", "平均播放量", "比上次", "最高播放量", "最高播放量的主页", "新增视频数", "没读到播放量的视频数"]
)
WIDTHS = {"检测日期": 12, "最高播放量的主页": 48}

GREEN = (PatternFill("solid", fgColor="C6EFCE"), Font(color="006100"))
RED = (PatternFill("solid", fgColor="FFC7CE"), Font(color="9C0006"))
HEADER_FILL = PatternFill("solid", fgColor="DDEBF7")

AVG = HEADERS.index("平均播放量")
DIFF = HEADERS.index("比上次")


def _sheet(wb):
    for name in OLD_SHEETS:
        if name in wb.sheetnames:
            del wb[name]
    if SHEET in wb.sheetnames:
        return wb[SHEET]
    ws = wb.create_sheet(SHEET)
    ws.append(HEADERS)
    for i, h in enumerate(HEADERS, start=1):
        cell = ws.cell(row=1, column=i)
        cell.font = Font(bold=True)
        cell.fill = HEADER_FILL
        cell.alignment = Alignment(horizontal="center", wrap_text=True)
        ws.column_dimensions[cell.column_letter].width = WIDTHS.get(h, max(10, len(h) * 2 + 2))
    ws.freeze_panes = "A2"
    return ws


def _row(today, page_rows):
    """把这次所有主页的统计加起来，变成一行。"""
    count = sum(p["stats"]["count"] for p in page_rows)
    total = sum(p["stats"]["total"] for p in page_rows)
    buckets = [sum(p["stats"]["buckets"][i] for p in page_rows) for i in range(len(BUCKET_NAMES))]
    with_max = [p for p in page_rows if p["stats"]["max"] is not None]
    best = max(with_max, key=lambda p: p["stats"]["max"]) if with_max else None
    new_counts = [p["new_count"] for p in page_rows if p["new_count"] is not None]
    return (
        [today, len(page_rows), sum(p["followers"] or 0 for p in page_rows), count]
        # 每档写成 "12（30.0%）"，百分比 = 这一档的视频数 / 检测视频数
        + [f"{b}（{b / count * 100:.1f}%）" if count else b for b in buckets]
        + [
            total,
            round(total / count, 1) if count else None,
            None,
            best["stats"]["max"] if best else None,
            f"{best['page_name']}（{best['page_id']}）窗口{best['seq']} {best['window']}" if best else "",
            sum(new_counts) if new_counts else None,
            sum(p["stats"]["unknown"] for p in page_rows),
        ]
    )


def write(path, page_rows):
    """page_rows: [dict(page_name, page_id, followers, stats, new_count), ...]
    返回实际保存的文件路径（原文件被 Excel 占用时会另存一份）。"""
    path = Path(path)
    today = datetime.now().strftime("%Y-%m-%d")
    if path.exists():
        wb = load_workbook(path)
    else:
        wb = Workbook()
        wb.remove(wb.active)
    ws = _sheet(wb)

    # 同一天只保留最后一次
    for r in range(ws.max_row, 1, -1):
        if str(ws.cell(row=r, column=1).value) == today:
            ws.delete_rows(r)

    prev = None  # 今天之前最近一次的平均播放量
    for row in ws.iter_rows(min_row=2, values_only=True):
        if row[0] and str(row[0]) < today and row[AVG] is not None:
            if prev is None or str(row[0]) >= prev[0]:
                prev = (str(row[0]), row[AVG])
    prev = prev[1] if prev else None

    values = _row(today, page_rows)
    avg = values[AVG]
    if prev is not None and avg is not None:
        values[DIFF] = round(avg - prev, 1)
    ws.append(values)
    # 平均播放量和比上次总是显示一位小数，比如 164.0
    for r in range(2, ws.max_row + 1):
        for col in (AVG, DIFF):
            ws.cell(row=r, column=col + 1).number_format = "0.0"
    if prev is not None and avg is not None and avg != prev:
        fill, font = GREEN if avg > prev else RED
        for col in (AVG, DIFF):
            cell = ws.cell(row=ws.max_row, column=col + 1)
            cell.fill, cell.font = fill, font

    try:
        wb.save(path)
        return path
    except PermissionError:
        alt = path.with_name(f"{path.stem}_{datetime.now():%H%M%S}（原文件被占用）.xlsx")
        wb.save(alt)
        return alt
