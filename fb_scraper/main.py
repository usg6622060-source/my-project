"""FB 主页数据抓取：比特浏览器窗口 -> 主页列表 -> 关注者数 -> Excel。"""
import asyncio
import json
import random
import sys
import traceback
from datetime import datetime
from pathlib import Path

from playwright.async_api import async_playwright

import config
import excel_out
import facebook as fb
from bitbrowser import BitBrowser, BitBrowserError

BASE = Path(__file__).resolve().parent
OUT = BASE / "输出"
PLATFORM = "FaceBook"


def now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


class Run:
    """一次运行的结果和日志。"""

    def __init__(self, settings, log_path, debug_dir, memory_path):
        self.s = settings
        self.rows = []  # (窗口序号, 顺序, 行)
        self.problems = []
        self.log_file = open(log_path, "a", encoding="utf-8")
        self.debug_dir = debug_dir
        self.memory_path = memory_path
        try:
            self.memory = json.loads(memory_path.read_text(encoding="utf-8"))
        except Exception:
            self.memory = {}

    def log(self, msg):
        line = f"{datetime.now():%H:%M:%S} {msg}"
        print(line, flush=True)
        self.log_file.write(line + "\n")
        self.log_file.flush()

    def problem(self, win, kind, detail, account="", page_name="", page_id=""):
        self.problems.append({
            "seq": win["seq"], "window": win["name"], "account": account,
            "page_name": page_name, "page_id": page_id,
            "kind": kind, "detail": detail, "time": now(),
        })
        target = f" {page_name}({page_id})" if page_id else ""
        self.log(f"  ✗ {kind}{target}：{detail}")

    def remember(self, win, account, pages):
        self.memory[win["id"]] = {
            "seq": win["seq"], "account": account,
            "pages": [[pid, name] for pid, name in pages], "time": now(),
        }

    def save_memory(self):
        self.memory_path.write_text(
            json.dumps(self.memory, ensure_ascii=False, indent=1), encoding="utf-8"
        )

    async def debug(self, page, label):
        if not self.s.debug_shots or page is None:
            return ""
        where = await fb.save_debug(page, self.debug_dir, label)
        return f"（截图：{where}.png）" if where else ""


async def process_window(pw, bb, win, run):
    s = run.s
    tag = f"[窗口{win['seq']} {win['name']}]"
    run.log(f"{tag} 开始")

    was_open = await asyncio.to_thread(bb.is_open, win["id"])
    try:
        ws_url = await asyncio.to_thread(bb.open, win["id"])
    except BitBrowserError as e:
        run.problem(win, "窗口打开失败", str(e))
        return
    if was_open:
        run.log(f"{tag} 窗口本来就开着，直接接管")

    browser = tab = None
    account = ""
    try:
        browser = await pw.chromium.connect_over_cdp(ws_url, timeout=s.timeout_s * 1000)
        context = browser.contexts[0] if browser.contexts else await browser.new_context()
        tab = await context.new_page()
        tab.set_default_timeout(s.timeout_s * 1000)

        try:
            account, pages = await fb.collect_pages(tab, s.timeout_s, run.log)
        except fb.AccountProblem as e:
            account = e.account_name
            shot = await run.debug(tab, f"窗口{win['seq']}_{e.kind}")
            remembered = run.memory.get(win["id"], {}).get("pages", [])
            if remembered:
                for pid, name in remembered:
                    run.problem(win, e.kind, e.detail + "（主页来自上次记录）" + shot,
                                account or run.memory[win["id"]].get("account", ""), name, pid)
            else:
                run.problem(win, e.kind, e.detail + shot, account)
            return

        run.log(f"{tag} 账号：{account or '（没读到名字）'}，找到 {len(pages)} 个主页")
        if not pages:
            shot = await run.debug(tab, f"窗口{win['seq']}_没找到主页")
            run.problem(win, "没找到主页",
                        "主页列表页没读到任何主页。可能这个号没有主页，"
                        "或者当前处于“以主页身份”状态，请手动切回个人号后再跑" + shot, account)
            return
        run.remember(win, account, pages)

        for i, (pid, list_name) in enumerate(pages):
            if i:
                await asyncio.sleep(random.uniform(s.wait_min, s.wait_max))
            run.log(f"{tag} ({i + 1}/{len(pages)}) {list_name or pid}")
            try:
                info = await fb.read_page(tab, pid, list_name, s.timeout_s, run.log)
            except fb.AccountProblem as e:
                shot = await run.debug(tab, f"窗口{win['seq']}_{e.kind}")
                for pid2, name2 in pages[i:]:
                    run.problem(win, e.kind, e.detail + "（读主页途中出现）" + shot,
                                account, name2, pid2)
                return
            except fb.PageProblem as e:
                shot = await run.debug(tab, f"窗口{win['seq']}_{pid}_{e.kind}")
                run.problem(win, e.kind, e.detail + shot, account, list_name, pid)
                continue
            except Exception as e:
                shot = await run.debug(tab, f"窗口{win['seq']}_{pid}_出错")
                run.problem(win, "读取出错", f"{type(e).__name__}: {e}{shot}", account, list_name, pid)
                continue

            name = info["name"] or list_name
            run.rows.append((win["seq"], i, [
                excel_talent_id(s.talent_id), PLATFORM, name, pid,
                fb.public_page_url(pid), info["followers"],
            ]))
            if info["approx_text"]:
                run.problem(win, "粉丝数为约数",
                            f"主页显示 {info['approx_text']}，没拿到精确数，主表先填了 {info['followers']}",
                            account, name, pid)
            else:
                run.log(f"  ✓ {name}：{info['followers']} followers")
    except Exception as e:
        run.log(traceback.format_exc())
        shot = await run.debug(tab, f"窗口{win['seq']}_出错") if tab else ""
        run.problem(win, "窗口处理出错", f"{type(e).__name__}: {e}{shot}", account)
    finally:
        if tab is not None:
            try:
                await tab.close()
            except Exception:
                pass
        if browser is not None:
            try:
                await browser.close()  # 只断开连接，不会关掉比特浏览器窗口
            except Exception:
                pass
        if was_open is False:
            try:
                await asyncio.to_thread(bb.close, win["id"])
            except Exception as e:
                run.log(f"{tag} 关闭窗口失败：{e}")
        elif was_open is None:
            run.log(f"{tag} 不确定窗口原来是否开着，为安全起见没有关闭")
        run.log(f"{tag} 结束")


def excel_talent_id(talent_id):
    return int(talent_id) if not talent_id.startswith("0") else talent_id


async def run_all(windows, bb, run):
    sem = asyncio.Semaphore(run.s.concurrency)
    async with async_playwright() as pw:
        async def one(win):
            async with sem:
                await process_window(pw, bb, win, run)
        await asyncio.gather(*(one(w) for w in windows))


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    try:
        s = config.load(BASE / "设置.txt")
    except Exception as e:
        print(f"设置.txt 有问题：{e}")
        return 1

    OUT.mkdir(exist_ok=True)
    (OUT / "日志").mkdir(exist_ok=True)
    date_str = datetime.now().strftime("%m-%d")
    n = excel_out.next_run_number(OUT, date_str)
    run_name = f"{date_str}_第{n}次"
    main_path = OUT / f"FB数据_{run_name}.xlsx"
    problem_path = OUT / f"FB问题_{run_name}.xlsx"

    run = Run(s, OUT / "日志" / f"运行_{run_name}.txt", OUT / "调试" / run_name, OUT / "记录.json")
    run.log(f"===== 开始：{run_name}，达人id {s.talent_id}，同时开 {s.concurrency} 个窗口 =====")

    bb = BitBrowser(s.api_url, s.api_key)
    try:
        windows = bb.list_windows()
    except BitBrowserError as e:
        run.log(str(e))
        return 1
    run.log(f"比特浏览器里共有 {len(windows)} 个窗口")
    if s.only_seqs:
        windows = [w for w in windows if w["seq"] in s.only_seqs]
    if s.skip_seqs:
        windows = [w for w in windows if w["seq"] not in s.skip_seqs]
    windows.sort(key=lambda w: w["seq"])
    run.log(f"本次处理 {len(windows)} 个窗口：{', '.join(str(w['seq']) for w in windows)}")

    stopped = False
    try:
        asyncio.run(run_all(windows, bb, run))
    except KeyboardInterrupt:
        stopped = True
        run.log("⚠ 手动中止，已读到的数据照样保存")

    rows = [r for _, _, r in sorted(run.rows, key=lambda x: (x[0], x[1]))]
    excel_out.write_main(BASE / "模板.xlsx", main_path, rows)
    run.log(f"主表：{main_path}（{len(rows)} 个主页）")
    if run.problems:
        problems = sorted(run.problems, key=lambda p: p["seq"])
        excel_out.write_problems(problem_path, problems)
        run.log(f"问题表：{problem_path}（{len(problems)} 条）")
    else:
        run.log("没有问题，不生成问题表")
    run.save_memory()
    run.log("===== 中途停止 =====" if stopped else "===== 完成 =====")
    return 0


if __name__ == "__main__":
    sys.exit(main())
