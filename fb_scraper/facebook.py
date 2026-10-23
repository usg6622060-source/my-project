"""在已登录的 Facebook 窗口里读取主页列表和关注者数。

页面结构以 2026-10 英文界面为准：
- 主页列表：/pages/?category=your_pages，标题 "Pages <账号名> manages"
- 主页首页：/profile.php?id=<14位>，名字下面一行 "420 followers • 0 following"
- 新主页第一次打开会弹 "Welcome to your new Page!"
- 被限制的号会跳到 /checkpoint/
"""
import asyncio
import os
import re
from pathlib import Path

# 测试时可以用环境变量指向本地假页面
FB = os.environ.get("FB_BASE", "https://www.facebook.com").rstrip("/")
BIZ = os.environ.get("FB_BIZ_BASE", "https://business.facebook.com").rstrip("/")

YOUR_PAGES_URL = FB + "/pages/?category=your_pages"

FOLLOWERS_RE = re.compile(r"(\d[\d,.]*)\s*([KMB])?\s+followers?\b", re.I)
PAGE_ID_RE = re.compile(r"profile\.php\?id=(\d{5,})")
NOT_A_NAME_RE = re.compile(
    r"^(\d+\+?\s+notifications?|notifications?|messages?|create post|promote|switch now|copy link|share)$",
    re.I,
)
PROFILE_PIC_RE = re.compile(r"^\s*profile (picture|photo) (for|of)\s+", re.I)
UNAVAILABLE_RE = re.compile(
    r"this content isn.t available|this page isn.t available|content isn.t available right now",
    re.I,
)
ACCOUNT_TEXT_PROBLEMS = [
    (re.compile(r"we (have )?disabled your account|your account (has been|was) disabled", re.I), "账号被封"),
    (re.compile(r"we (have )?suspended your account|your account (has been|was) suspended", re.I), "账号被封"),
    (re.compile(r"your account (has been|is|was) locked", re.I), "账号被锁"),
]

JS_MAIN_TEXT = """() => {
  const root = document.querySelector('[role="main"]') || document.body;
  return root ? root.innerText : '';
}"""

JS_BODY_TEXT = "() => document.body ? document.body.innerText : ''"

JS_PAGE_LINKS = """() => {
  const root = document.querySelector('[role="main"]') || document.body;
  return Array.from(root.querySelectorAll('a[href*="profile.php?id="]'))
    .map(a => [a.href, (a.innerText || '').trim(), (a.getAttribute('aria-label') || '').trim()]);
}"""

JS_HAS_PAGE_LINKS = """() => {
  const root = document.querySelector('[role="main"]') || document.body;
  return !!root && !!root.querySelector('a[href*="profile.php?id="]');
}"""

JS_H1 = """() => {
  const root = document.querySelector('[role="main"]') || document.body;
  return Array.from(root.querySelectorAll('h1'))
    .map(h => h.innerText.trim())
    .filter(t => t && !/^(manage page|notifications)$/i.test(t));
}"""

# 出现关注者数或"主页不可用"就不用再等了
JS_PAGE_READY = r"""() => {
  const root = document.querySelector('[role="main"]') || document.body;
  if (!root) return false;
  const t = root.innerText;
  return /\d[\d,.]*\s*[KMB]?\s+followers?\b/i.test(t) || /isn.t available/i.test(t);
}"""


class AccountProblem(Exception):
    """整个号出问题（被封 / 要验证 / 掉登录），这个号后面的主页都不用读了。"""

    def __init__(self, kind, detail, account_name=""):
        super().__init__(f"{kind}：{detail}")
        self.kind = kind
        self.detail = detail
        self.account_name = account_name


class PageProblem(Exception):
    """单个主页出问题。"""

    def __init__(self, kind, detail):
        super().__init__(f"{kind}：{detail}")
        self.kind = kind
        self.detail = detail


def clean_name(text):
    name = PROFILE_PIC_RE.sub("", (text or "").split("\n")[0]).strip()
    return "" if NOT_A_NAME_RE.match(name) else name


def public_page_url(page_id):
    return f"https://www.facebook.com/profile.php?id={page_id}"


def parse_count(num, unit):
    """'420',None -> (420, 0)；'1.2','K' -> (1200, 100)。第二个值是约数的误差范围，0 表示精确。"""
    num = num.replace(",", "").rstrip(".")
    if not unit:
        return int(float(num)), 0
    mult = {"K": 1_000, "M": 1_000_000, "B": 1_000_000_000}[unit.upper()]
    decimals = len(num.split(".")[1]) if "." in num else 0
    return round(float(num) * mult), mult // (10 ** decimals)


async def _text(page, js=JS_BODY_TEXT):
    try:
        return await page.evaluate(js)
    except Exception:
        return ""


async def check_account(page, check_text=False):
    url = page.url
    if "/checkpoint/" in url:
        text = await _text(page)
        m = re.search(r"^(.+?), confirm that you", text, re.M)
        raise AccountProblem(
            "账号被限制",
            "跳到了 checkpoint 验证页（confirm that you're human），脚本没有做任何点击",
            m.group(1).strip() if m else "",
        )
    if re.search(r"/login(/|\.php|\?|$)", url) or await page.locator('input[name="pass"]').count():
        raise AccountProblem("未登录", "跳到了登录页，这个窗口的登录状态失效了")
    if check_text:
        text = await _text(page)
        for pattern, kind in ACCOUNT_TEXT_PROBLEMS:
            if pattern.search(text):
                raise AccountProblem(kind, f"页面提示：{pattern.search(text).group(0)}")


async def goto(page, url, timeout_s):
    await page.goto(url, wait_until="domcontentloaded", timeout=timeout_s * 1000)


async def dismiss_cookies(page, log):
    """处理 "Allow the use of cookies from Facebook on this browser?"：点 Decline optional cookies。
    选过一次之后这个窗口就不会再弹。"""
    dialog = page.locator('[role="dialog"]').filter(
        has_text=re.compile(r"use of cookies", re.I)
    )
    if not await dialog.count():
        return False
    dialog = dialog.last
    button = dialog.get_by_role(
        "button", name=re.compile(r"decline optional cookies|only allow essential cookies", re.I)
    )
    if not await button.count():
        button = dialog.get_by_text(
            re.compile(r"^\s*(decline optional cookies|only allow essential cookies)\s*$", re.I)
        )
    if not await button.count():
        log("  ⚠ 出现 cookies 弹窗，但没找到 Decline optional cookies 按钮")
        return False
    await button.first.click()
    try:
        await dialog.wait_for(state="hidden", timeout=8000)
    except Exception:
        log("  ⚠ 点了 Decline optional cookies，但弹窗没消失")
    log("  已关闭 cookies 弹窗（Decline optional cookies）")
    return True


async def wait_until(page, js, seconds, log):
    """等页面准备好；等的过程中顺手关掉 cookies 弹窗。"""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + seconds
    while True:
        await dismiss_cookies(page, log)
        try:
            if await page.evaluate(js):
                return True
        except Exception:
            pass
        if loop.time() > deadline:
            return False
        await asyncio.sleep(1)


async def collect_pages(page, timeout_s, log):
    """打开 "Pages xxx manages"，返回 (账号名, [(主页ID, 主页名称), ...])。"""
    await goto(page, YOUR_PAGES_URL, timeout_s)
    await asyncio.sleep(2)
    await check_account(page, check_text=True)
    await wait_until(page, JS_HAS_PAGE_LINKS, min(20, timeout_s), log)
    await check_account(page, check_text=True)

    found = {}  # id -> name，保持页面上的顺序
    aria_names = {}
    stable = 0
    for _ in range(40):
        before = len(found)
        for href, text, aria in await page.evaluate(JS_PAGE_LINKS):
            m = PAGE_ID_RE.search(href)
            if not m:
                continue
            pid = m.group(1)
            name = clean_name(text)
            if pid not in found or (not found[pid] and name):
                found[pid] = name
            # 头像链接没有文字，只有 "Profile picture for xxx" 这种说明，只在实在没名字时才用
            if not found[pid]:
                aria_names.setdefault(pid, clean_name(PROFILE_PIC_RE.sub("", aria)))
        stable = stable + 1 if len(found) == before else 0
        if stable >= 3:
            break
        await page.evaluate("() => window.scrollTo(0, document.body.scrollHeight)")
        await asyncio.sleep(1.2)

    main_text = await _text(page, JS_MAIN_TEXT)
    m = re.search(r"Pages (.+?) manages", main_text)
    account = m.group(1).strip() if m else ""
    return account, [(pid, name or aria_names.get(pid, "")) for pid, name in found.items()]


async def _is_on(switch):
    aria = await switch.get_attribute("aria-checked")
    if aria in ("true", "false"):
        return aria == "true"
    try:
        return await switch.is_checked()
    except Exception:
        return None


async def dismiss_welcome(page, log):
    """处理 "Welcome to your new Page!"：营销邮件开关如果开着就关掉，再点 Use Page。"""
    dialog = page.locator('[role="dialog"]').filter(
        has_text=re.compile(r"Welcome to your new Page", re.I)
    )
    if not await dialog.count():
        return
    dialog = dialog.last
    switches = dialog.locator('[role="switch"], input[type="checkbox"]')
    n = await switches.count()
    if n >= 2:
        # 最下面那个是 "Marketing & promotional emails"
        marketing = switches.nth(n - 1)
        state = await _is_on(marketing)
        if state:
            await marketing.click(force=True)
            await asyncio.sleep(0.8)
            if await _is_on(marketing):
                log("  ⚠ 营销邮件开关没关掉，继续")
        elif state is None:
            log("  ⚠ 看不出营销邮件开关的状态，没有动它")
    else:
        log("  ⚠ 欢迎弹窗里没找到开关，直接点 Use Page")

    button = dialog.get_by_role("button", name=re.compile(r"^\s*Use Page\s*$", re.I))
    if not await button.count():
        button = dialog.get_by_text("Use Page", exact=True)
    if await button.count():
        await button.first.click()
        try:
            await dialog.wait_for(state="hidden", timeout=8000)
        except Exception:
            log("  ⚠ 点了 Use Page，但弹窗没消失，继续读数据")
    else:
        log("  ⚠ 欢迎弹窗里没找到 Use Page 按钮，继续读数据")


def _numbers_near_followers(text, lo, hi):
    found = set()
    for m in re.finditer(r"follower", text, re.I):
        window = text[max(0, m.start() - 150): m.end() + 150]
        for n in re.finditer(r"(?<![\d.,])(\d{1,3}(?:,\d{3})+|\d{4,})(?![\d.,]*\d)", window):
            v = int(n.group(1).replace(",", ""))
            if lo <= v <= hi:
                found.add(v)
    return found


async def exact_followers(page, page_id, approx, step, timeout_s, log):
    """主页显示 1.2K 这种约数时，想办法拿精确数。拿不到返回 None。"""
    lo, hi = approx - step, approx + step

    # 1) 当前主页的网页源码里有时直接带精确数字
    try:
        html = await page.content()
    except Exception:
        html = ""
    for key in ("follower_count", "followers_count", "subscriber_count"):
        values = {int(v) for v in re.findall(rf'"{key}"\s*:\s*(\d+)', html)}
        values = {v for v in values if lo <= v <= hi}
        if len(values) == 1:
            return values.pop(), f"主页源码 {key}"

    # 2) Meta Business Suite 的 Insights（管理员能看到精确数）
    for url in (
        f"{BIZ}/latest/insights/audience?asset_id={page_id}",
        f"{BIZ}/latest/insights/results?asset_id={page_id}",
    ):
        try:
            await goto(page, url, timeout_s)
            await check_account(page)
            await dismiss_cookies(page, log)
            try:
                await page.wait_for_load_state("networkidle", timeout=20000)
            except Exception:
                pass
            await asyncio.sleep(3)
            values = _numbers_near_followers(await _text(page), lo, hi)
            if len(values) == 1:
                return values.pop(), "Business Suite Insights"
            if len(values) > 1:
                log(f"  ⚠ Insights 里找到多个可能的数字 {sorted(values)}，无法确定")
        except AccountProblem:
            raise
        except Exception as e:
            log(f"  ⚠ 打开 Insights 失败：{e}")
    return None, ""


async def read_page(page, page_id, list_name, timeout_s, log):
    """返回 {'name', 'followers', 'approx_text'}；approx_text 非空表示只拿到约数。"""
    await goto(page, f"{FB}/profile.php?id={page_id}", timeout_s)
    await check_account(page)
    await wait_until(page, JS_PAGE_READY, min(25, timeout_s), log)
    await asyncio.sleep(1.5)
    await check_account(page)
    await dismiss_cookies(page, log)
    await dismiss_welcome(page, log)

    main_text = await _text(page, JS_MAIN_TEXT)
    if UNAVAILABLE_RE.search(main_text):
        raise PageProblem("主页不可用", "页面显示 This content isn't available（可能被限制或删除）")

    h1 = await _text(page, JS_H1)
    name = clean_name(list_name) or (clean_name(h1[0]) if h1 else "")

    m = FOLLOWERS_RE.search(main_text)
    if not m:
        raise PageProblem("粉丝数读取失败", "主页上没找到 “xxx followers”")
    shown = m.group(0).strip()
    value, step = parse_count(m.group(1), m.group(2))
    if not step:
        return {"name": name, "followers": value, "approx_text": ""}

    log(f"  主页显示 {shown}，去找精确数字…")
    exact, source = await exact_followers(page, page_id, value, step, timeout_s, log)
    if exact is not None:
        log(f"  精确数字 {exact}（来源：{source}）")
        return {"name": name, "followers": exact, "approx_text": ""}
    return {"name": name, "followers": value, "approx_text": shown}


async def save_debug(page, folder, label):
    """出问题时保存截图和网页源码，返回保存位置。"""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    safe = re.sub(r'[\\/:*?"<>|\s]+', "_", label)[:80]
    try:
        await page.screenshot(path=str(folder / f"{safe}.png"))
        (folder / f"{safe}.html").write_text(await page.content(), encoding="utf-8")
        return str(folder / safe)
    except Exception:
        return ""
