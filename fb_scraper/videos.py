"""读主页的 Reels 和视频播放量。

页面结构以 2026-11 英文界面为准：
- Reels：/profile.php?id=<ID>&sk=reels_tab，"Your reels" 网格，每个缩略图左下角 "👁 507"
- 视频：Videos 标签页（在 More 下面），缩略图上有 "1.2K views"
网格按发布时间从新到旧排，取前 N 个。
"""
import asyncio
import re

import facebook as fb

# 分档：0~9、10~99、100~999、1000~9999、1万以上
BUCKETS = [(0, 9), (10, 99), (100, 999), (1000, 9999), (10000, None)]
BUCKET_NAMES = ["0~9", "10~99", "100~999", "1000~9999", "1万以上"]

VIEWS_LINE_RE = re.compile(r"^(\d[\d,.]*)\s*([KMB])?(?:\s+(?:views?|plays?))?$", re.I)
REEL_ID_RE = re.compile(r"/reel/(\d+)")
VIDEO_ID_RE = re.compile(r"/videos/(?:[^/?#]+/)?(\d{6,})|[?&]v=(\d{6,})")

JS_LINKS = """(sel) => {
  const root = document.querySelector('[role="main"]') || document.body;
  return Array.from(root.querySelectorAll(sel)).map(a => [a.href, (a.innerText || '').trim()]);
}"""

JS_SELECTED_TAB = """() => Array.from(document.querySelectorAll('[role="tab"][aria-selected="true"]'))
  .map(t => (t.innerText || '').trim())"""


def parse_views(text):
    """缩略图上的文字 -> 播放量。多行时取最后一个纯数字行（前面可能是视频标题）。"""
    for line in reversed([l.strip() for l in (text or "").splitlines()]):
        m = VIEWS_LINE_RE.match(line)
        if m:
            value, _ = fb.parse_count(m.group(1), m.group(2))
            return value
    return None


def bucket_counts(views):
    counts = [0] * len(BUCKETS)
    for v in views:
        for i, (lo, hi) in enumerate(BUCKETS):
            if v >= lo and (hi is None or v <= hi):
                counts[i] += 1
                break
    return counts


async def _collect(page, selector, id_re, limit, log):
    """滚动网格，按页面顺序收集前 limit 个 (视频ID, 播放量)。"""
    found = {}
    stable = 0
    for _ in range(60):
        before = len(found)
        for href, text in await page.evaluate(JS_LINKS, selector):
            m = id_re.search(href)
            if not m:
                continue
            vid = next(g for g in m.groups() if g)
            views = parse_views(text)
            if vid not in found or (found[vid] is None and views is not None):
                found[vid] = views
        if len(found) >= limit:
            break
        stable = stable + 1 if len(found) == before else 0
        if stable >= 3:
            break
        await page.evaluate("() => window.scrollTo(0, document.body.scrollHeight)")
        await asyncio.sleep(1.5)
    return list(found.items())[:limit]


async def _tab_is(page, name):
    try:
        tabs = await page.evaluate(JS_SELECTED_TAB)
    except Exception:
        return False
    return any(t.lower().startswith(name.lower()) for t in tabs)


async def _followers(page):
    """标签页顶部也有 "420 followers"，读出来就不用再单独打开主页首页。"""
    try:
        text = await page.evaluate(fb.JS_MAIN_TEXT)
    except Exception:
        return None
    m = fb.FOLLOWERS_RE.search(text or "")
    return fb.parse_count(m.group(1), m.group(2))[0] if m else None


async def read_videos(page, page_id, n_reels, n_videos, timeout_s, log):
    """返回 ([(视频ID, 类型, 播放量或None), ...], 粉丝数或None)，视频已去重。"""
    results = {}
    followers = None

    if n_reels[1] > 0:
        await fb.goto(page, f"{fb.FB}/profile.php?id={page_id}&sk=reels_tab", timeout_s)
        await fb.check_account(page)
        await fb.wait_until(page, """() => !!document.querySelector('[role="main"] a[href*="/reel/"]')""",
                            min(15, timeout_s), log)
        await fb.dismiss_welcome(page, log)
        if fb.UNAVAILABLE_RE.search(await page.evaluate(fb.JS_MAIN_TEXT) or ""):
            raise fb.PageProblem("主页不可用", "页面显示 This content isn't available（可能被限制或删除）")
        followers = await _followers(page)
        found = await _collect(page, 'a[href*="/reel/"]', REEL_ID_RE, n_reels[1], log)
        for vid, views in found[n_reels[0] - 1:]:
            results[vid] = ("Reels", views)

    if n_videos[1] > 0:
        opened = False
        for sk in ("videos_tab", "videos"):
            await fb.goto(page, f"{fb.FB}/profile.php?id={page_id}&sk={sk}", timeout_s)
            await fb.check_account(page)
            await fb.wait_until(page, "() => false", 3, log)  # 等页面加载，顺手关 cookies 弹窗
            if await _tab_is(page, "Videos"):
                opened = True
                break
        if opened:
            if followers is None:
                followers = await _followers(page)
            await fb.wait_until(
                page,
                """() => !!document.querySelector('[role="main"] a[href*="/videos/"], [role="main"] a[href*="watch/?v="]')""",
                min(10, timeout_s), log,
            )
            found = await _collect(page, 'a[href*="/videos/"], a[href*="watch/?v="]',
                                   VIDEO_ID_RE, n_videos[1], log)
            for vid, views in found[n_videos[0] - 1:]:
                results.setdefault(vid, ("视频", views))
        else:
            # 没有 Videos 标签页（很多主页只发 Reels）时不算错误，只记日志
            log("  这个主页没找到 Videos 标签页，只统计 Reels")

    return [(vid, kind, views) for vid, (kind, views) in results.items()], followers


def summarize(videos):
    """把一个主页的视频列表算成统计数字。"""
    views = [v for _, _, v in videos if v is not None]
    total = sum(views)
    return {
        "count": len(views),
        "unknown": sum(1 for _, _, v in videos if v is None),
        "buckets": bucket_counts(views),
        "total": total,
        "avg": round(total / len(views), 1) if views else None,
        "max": max(views) if views else None,
        "ids": [vid for vid, _, _ in videos],
    }
