"""比特浏览器本地 API。"""
import json
import urllib.error
import urllib.request


class BitBrowserError(Exception):
    pass


class BitBrowser:
    def __init__(self, base_url, api_key=""):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key

    def _post(self, path, body, timeout=60):
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["x-api-key"] = self.api_key
        req = urllib.request.Request(
            self.base_url + path,
            data=json.dumps(body).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        # 本地地址不走系统代理
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            with opener.open(req, timeout=timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.URLError as e:
            raise BitBrowserError(
                f"连不上比特浏览器本地API（{self.base_url}）：{e}。"
                "请确认比特浏览器已打开、本地API已开启、端口和 设置.txt 一致"
            ) from e
        if not data.get("success", False):
            raise BitBrowserError(data.get("msg") or str(data))
        return data.get("data")

    def list_windows(self):
        """返回全部窗口：[{id, seq, name, remark}, ...]"""
        windows, page = [], 0
        while True:
            data = self._post("/browser/list", {"page": page, "pageSize": 100}) or {}
            items = data.get("list") or []
            windows.extend(items)
            total = data.get("totalNum") or 0
            if not items or len(windows) >= total:
                break
            page += 1
        return [
            {
                "id": w.get("id"),
                "seq": w.get("seq") or 0,
                "name": w.get("name") or "",
                "remark": w.get("remark") or "",
            }
            for w in windows
        ]

    def is_open(self, window_id):
        """窗口是否已经开着。查不到时返回 None。"""
        try:
            data = self._post("/browser/pids", {"ids": [window_id]}, timeout=15) or {}
        except BitBrowserError:
            return None
        return bool(data.get(window_id))

    def open(self, window_id):
        """打开窗口（已开着的会直接返回连接地址），返回 CDP ws 地址。"""
        data = self._post("/browser/open", {"id": window_id}, timeout=120) or {}
        ws = data.get("ws")
        if not ws:
            raise BitBrowserError(f"打开窗口没拿到连接地址：{data}")
        return ws

    def close(self, window_id):
        self._post("/browser/close", {"id": window_id}, timeout=60)
