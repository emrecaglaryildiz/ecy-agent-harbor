"""Onboarded Playwright MCP, private per-run profiles, public web read tools only."""
import ipaddress
import json
import os
import queue
import re
import select
import shutil
import socket
import subprocess
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, quote

VERSION = "0.0.83"
INSTALL_LOCK = threading.Lock()
INSTALL_STATE = {"installing": False, "error": None, "stage": ""}


def runtime(database_path):
    return Path(os.getenv("BROWSER_RUNTIME_PATH", str(Path(database_path).resolve().parent / "browser-runtime"))).resolve()


def clean_env(folder):
    env = {key: os.environ[key] for key in ("PATH", "HOME", "LANG", "TMPDIR", "SYSTEMROOT") if key in os.environ}
    env["PLAYWRIGHT_BROWSERS_PATH"] = str(folder / "browsers")
    env["npm_config_cache"] = str(folder / "npm-cache")
    return env


def browser_executable():
    configured = os.getenv("PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH", "")
    choices = [configured, "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome", shutil.which("chromium"), shutil.which("google-chrome")]
    return next((value for value in choices if value and Path(value).is_file()), None)


def status(database_path):
    folder = runtime(database_path)
    package = folder / "node_modules" / "@playwright" / "mcp" / "cli.js"
    browser = bool(browser_executable()) or (any((folder / "browsers").glob("chromium-*/INSTALLATION_COMPLETE")) if (folder / "browsers").exists() else False)
    return dict(INSTALL_STATE, installed=package.is_file() and browser and bool(shutil.which("node")),
                node_available=bool(shutil.which("node")), npm_available=bool(shutil.which("npm")), version=VERSION)


def install(database_path):
    if not INSTALL_LOCK.acquire(blocking=False):
        return
    INSTALL_STATE.update(installing=True, error=None, stage="Paket hazırlanıyor")
    try:
        folder = runtime(database_path)
        folder.mkdir(parents=True, exist_ok=True)
        if not shutil.which("node") or not shutil.which("npm"):
            raise RuntimeError("Node.js 18+ ve npm kurulu olmalı")
        commands = [([shutil.which("npm"), "install", "--prefix", str(folder), "--no-fund", "--no-audit", "@playwright/mcp@" + VERSION], "Playwright MCP kuruluyor"),
                    ([shutil.which("node"), str(folder / "node_modules/playwright/cli.js"), "install", "chromium"], "Chromium indiriliyor")]
        for index, (command, stage) in enumerate(commands):
            if index == 1 and browser_executable():
                continue
            INSTALL_STATE["stage"] = stage
            with (folder / "install.log").open("wb") as log:
                result = subprocess.run(command, cwd=str(folder), env=clean_env(folder), stdout=log, stderr=subprocess.STDOUT, timeout=900)
            if result.returncode:
                raise RuntimeError(stage + " tamamlanamadı; Node.js ve ağ erişimini kontrol et")
        INSTALL_STATE["stage"] = "Playwright MCP hazır"
    except Exception as exc:
        INSTALL_STATE["error"] = str(exc) if isinstance(exc, RuntimeError) else "Kurulum tamamlanamadı; ağ ve Node.js durumunu kontrol et"
    finally:
        INSTALL_STATE["installing"] = False
        INSTALL_LOCK.release()


def public_addresses(host, port):
    addresses = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    if not addresses or any(not ipaddress.ip_address(item[4][0]).is_global for item in addresses):
        raise ValueError("Yalnızca genel internet adreslerine erişilebilir; yerel ve özel ağlar kapalı")
    return addresses


def validate_url(value):
    if not isinstance(value, str) or len(value) > 8192:
        raise ValueError("Geçersiz web adresi")
    parsed = urlparse(value)
    if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("Web aracı yalnızca HTTP/HTTPS adreslerini kabul eder")
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    if port not in (80, 443):
        raise ValueError("Web aracı yalnızca 80/443 portlarını kullanabilir")
    public_addresses(parsed.hostname, port)
    return value


class PublicProxy(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def _connect(self, host, port):
        if port not in (80, 443):
            raise ValueError("Port engellendi")
        addresses = public_addresses(host, port)
        # Connect to the validated IP itself: redirects and DNS rebinding cannot bypass this boundary.
        family, kind, proto, _, target = addresses[0]
        remote = socket.socket(family, kind, proto)
        remote.settimeout(30)
        remote.connect(target)
        return remote

    def _relay(self, remote):
        try:
            while True:
                readable, _, _ = select.select([self.connection, remote], [], [], 60)
                if not readable:
                    break
                for source in readable:
                    data = source.recv(65536)
                    if not data:
                        return
                    (remote if source is self.connection else self.connection).sendall(data)
        finally:
            remote.close()
            self.close_connection = True

    def do_CONNECT(self):
        try:
            target = urlparse("https://" + self.path)
            remote = self._connect(target.hostname, target.port or 443)
            self.send_response(200)
            self.end_headers()
            self._relay(remote)
        except Exception:
            self.send_error(403, "Public web access only")

    def do_GET(self):
        try:
            target = urlparse(validate_url(self.path))
            remote = self._connect(target.hostname, target.port or 80)
            path = (target.path or "/") + ("?" + target.query if target.query else "")
            headers = "".join(f"{key}: {value}\r\n" for key, value in self.headers.items() if key.lower() not in ("proxy-connection", "proxy-authorization", "connection"))
            remote.sendall((f"{self.command} {path} HTTP/1.1\r\n" + headers + "Connection: close\r\n\r\n").encode("latin-1"))
            self._relay(remote)
        except Exception:
            self.send_error(403, "Public web access only")

    do_HEAD = do_GET


class BrowserMCP:
    def __init__(self, database_path, on_log=None):
        if not status(database_path)["installed"]:
            raise RuntimeError("Önce ayarlardan Playwright MCP kurulumunu tamamla")
        self.folder = runtime(database_path)
        self.on_log = on_log or (lambda event, message: None)
        self.proxy = ThreadingHTTPServer(("127.0.0.1", 0), PublicProxy)
        self.proxy.daemon_threads = True
        threading.Thread(target=self.proxy.serve_forever, daemon=True).start()
        self.directory = tempfile.TemporaryDirectory(prefix="harbor-web-", dir=str(self.folder))
        config = Path(self.directory.name) / "config.json"
        launch = {"headless": True, "proxy": {"server": f"http://127.0.0.1:{self.proxy.server_port}", "bypass": "<-loopback>"}}
        executable = browser_executable()
        if executable:
            launch["executablePath"] = executable
        config.write_text(json.dumps({"browser": {"browserName": "chromium", "isolated": True,
            "launchOptions": launch,
            "contextOptions": {"acceptDownloads": False, "serviceWorkers": "block"}}}), encoding="utf-8")
        command = [shutil.which("node"), str(self.folder / "node_modules/@playwright/mcp/cli.js"),
                   "--config", str(config), "--headless", "--isolated", "--no-webmcp", "--block-service-workers", "--image-responses", "omit", "--codegen", "none",
                   "--init-page", str(Path(__file__).with_name("browser_guard.cjs")), "--output-dir", self.directory.name]
        self.process = subprocess.Popen(command, cwd=self.directory.name, env=clean_env(self.folder),
                                        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, bufsize=1)
        self.responses = queue.Queue()
        threading.Thread(target=self._read, daemon=True).start()
        self.sequence = 0
        try:
            self.rpc("initialize", {"protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "agent-harbor", "version": "0.2"}})
            self._write({"jsonrpc": "2.0", "method": "notifications/initialized"})
        except Exception:
            self.close()
            raise

    def _read(self):
        for line in self.process.stdout:
            try:
                self.responses.put(json.loads(line))
            except ValueError:
                continue
        self.responses.put(None)

    def _write(self, message):
        self.process.stdin.write(json.dumps(message) + "\n")
        self.process.stdin.flush()

    def rpc(self, method, params):
        self.sequence += 1
        current = self.sequence
        self._write({"jsonrpc": "2.0", "id": current, "method": method, "params": params})
        while True:
            response = self.responses.get(timeout=120)
            if response is None:
                raise RuntimeError("Playwright MCP bağlantısı kapandı")
            if response.get("id") != current:
                continue
            if "error" in response:
                raise RuntimeError("Playwright MCP aracı tamamlanamadı")
            return response.get("result", {})

    def call(self, name, arguments):
        # Models never see MCP's click/type/evaluate/upload/filesystem/code tools.
        if name == "web_search":
            query = arguments.get("query", "")
            if not isinstance(query, str) or not query.strip() or len(query) > 1000:
                raise ValueError("Arama sorgusu geçersiz")
            url = "https://www.google.com/search?q=" + quote(query)
        elif name == "web_open":
            url = validate_url(arguments.get("url"))
        else:
            raise PermissionError("Bu web aracı izin listesinde değil")
        self.on_log("web_navigation", f"Web sayfası okunuyor · {urlparse(url).hostname}")
        result = self.rpc("tools/call", {"name": "browser_navigate", "arguments": {"url": url}})
        blocks = result.get("content", [])
        text = "\n".join(block.get("text", "") for block in blocks if block.get("type") == "text")
        # New MCP versions return snapshot files. Read only files inside this isolated session.
        for match in re.finditer(r"\[Snapshot\]\(([^)]+)\)", text):
            path = (Path(self.directory.name) / match.group(1)).resolve()
            if path.is_relative_to(Path(self.directory.name).resolve()) and path.suffix in (".yml", ".yaml") and path.is_file():
                text += "\n\n" + path.read_text(encoding="utf-8")
        if result.get("isError"):
            return "Web sayfası okunamadı: " + text
        return text

    def close(self):
        if getattr(self, "process", None):
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
        self.proxy.shutdown()
        self.proxy.server_close()
        self.directory.cleanup()
