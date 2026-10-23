"""读取 设置.txt。"""
import re
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Settings:
    talent_id: str = ""
    api_url: str = "http://127.0.0.1:54345"
    api_key: str = ""
    concurrency: int = 1
    wait_min: float = 3.0
    wait_max: float = 6.0
    only_seqs: set = field(default_factory=set)
    skip_seqs: set = field(default_factory=set)
    timeout_s: int = 60
    debug_shots: bool = True


def _read_text(path):
    raw = Path(path).read_bytes()
    # 记事本可能存成 UTF-8（带或不带 BOM），老系统可能是 GBK
    for enc in ("utf-8-sig", "gbk"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def parse_seq_list(text):
    """'1-3,7' -> {1, 2, 3, 7}"""
    result = set()
    for part in re.split(r"[,，\s]+", text.strip()):
        if not part:
            continue
        m = re.fullmatch(r"(\d+)\s*[-~－]\s*(\d+)", part)
        if m:
            a, b = int(m.group(1)), int(m.group(2))
            result.update(range(min(a, b), max(a, b) + 1))
        elif part.isdigit():
            result.add(int(part))
        else:
            raise ValueError(f"窗口序号写法不对：{part!r}（例子：1-3,7）")
    return result


def load(path):
    values = {}
    for line in _read_text(path).splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, sep, val = line.partition("=")
        if not sep:
            key, sep, val = line.partition("＝")
        if sep:
            values[key.strip()] = val.strip()

    s = Settings()
    s.talent_id = values.get("达人id", "")
    if not re.fullmatch(r"\d{5}", s.talent_id):
        raise ValueError(f"达人id 应该是五位数字，现在是：{s.talent_id!r}")
    s.api_url = values.get("比特浏览器地址", s.api_url).rstrip("/") or s.api_url
    if not s.api_url.startswith("http"):
        s.api_url = "http://" + s.api_url
    s.api_key = values.get("API密钥", "")
    s.concurrency = max(1, int(values.get("同时打开窗口数", "1") or 1))

    wait = values.get("每个主页之间等待秒数", "3-6") or "3-6"
    m = re.fullmatch(r"([\d.]+)\s*(?:[-~－]\s*([\d.]+))?", wait)
    if not m:
        raise ValueError(f"每个主页之间等待秒数 写法不对：{wait!r}（例子：3-6）")
    a = float(m.group(1))
    b = float(m.group(2)) if m.group(2) else a
    s.wait_min, s.wait_max = min(a, b), max(a, b)

    s.only_seqs = parse_seq_list(values.get("只处理这些窗口序号", ""))
    s.skip_seqs = parse_seq_list(values.get("跳过这些窗口序号", ""))
    s.timeout_s = max(10, int(values.get("页面加载超时秒数", "60") or 60))
    s.debug_shots = values.get("保存调试截图", "是") not in ("否", "不", "no", "false", "0")
    return s
