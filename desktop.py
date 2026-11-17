from __future__ import annotations

import argparse
import json
import logging
from logging.handlers import RotatingFileHandler
import os
import socket
import sys
from pathlib import Path
import threading
import time
from urllib.parse import urlencode

import uvicorn

from backend.app import create_app
from backend.config import DATA_DIR, TOKEN
from backend.mcp_bridge import SHUTDOWN_GRACE_SECONDS
from backend.prerequisites import DesktopDependencyError, ensure_desktop_runtime, startup_report


def configure_logging():
    handler = RotatingFileHandler(DATA_DIR / 'forma.log', maxBytes=3_000_000, backupCount=3, encoding='utf-8')
    logging.basicConfig(level=logging.INFO, handlers=[handler],
                        format='%(asctime)s %(levelname)s %(name)s %(message)s')
    logging.getLogger('httpx').setLevel(logging.WARNING)
    logging.getLogger('openai').setLevel(logging.WARNING)


def main():
    parser = argparse.ArgumentParser(description='Forma CAD desktop and local API')
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument('--headless', action='store_true', help='Run backend without desktop window')
    modes.add_argument('--smoke-test', action='store_true', help='Load a hidden WebView2 window; never install prerequisites')
    modes.add_argument('--check-runtime', nargs='?', const=str(DATA_DIR / 'startup-check.json'),
                       metavar='REPORT_PATH', help='Write read-only startup diagnostics and exit without starting MCP or CAD')
    parser.add_argument('--port', type=int, default=0, help='Local API port; default picks a free port')
    args = parser.parse_args()
    configure_logging()
    if args.check_runtime:
        report = startup_report()
        target = Path(args.check_runtime).resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(report, ensure_ascii=False, indent=2), 'utf-8')
        if sys.stdout:
            print(f'Startup report: {target}', flush=True)
        elif sys.platform == 'win32' and args.check_runtime == str(DATA_DIR / 'startup-check.json'):
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, f'启动环境报告已保存：\n{target}\n\n此检查没有启动连接器、SW 或安装程序。', 'Forma CAD', 0x40)
        return
    if not args.headless:
        ensure_desktop_runtime(interactive=not args.smoke_test)
        import webview
        webview.settings['ALLOW_DOWNLOADS'] = True
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(('127.0.0.1', args.port))
    port = listener.getsockname()[1]
    listener.listen(128)
    app = create_app()
    server = uvicorn.Server(uvicorn.Config(app, host='127.0.0.1', port=port, log_level='warning',
                                          access_log=False, log_config=None))
    if args.headless:
        if sys.stdout:
            print(f'Forma CAD: http://127.0.0.1:{port}/?{urlencode({"token": TOKEN})}', flush=True)
            print('This address contains a local access credential. Keep it private.', flush=True)
        server.run(sockets=[listener])
        return
    thread = threading.Thread(target=lambda: server.run(sockets=[listener]), daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 20
        while not server.started and thread.is_alive() and time.monotonic() < deadline:
            time.sleep(0.05)
        if not server.started:
            raise RuntimeError(f'Local server failed to start. See {DATA_DIR / "forma.log"}')
        window = webview.create_window('Forma CAD — SOLIDWORKS AI 工作台',
                              f'http://127.0.0.1:{port}/?{urlencode({"token": TOKEN})}',
                              width=1440, height=920, min_size=(1050, 680), background_color='#0b0e13',
                              hidden=args.smoke_test, text_select=True)
        smoke = {'loaded': False}
        watchdog = None
        if args.smoke_test:
            def on_loaded():
                try:
                    smoke['loaded'] = bool(window.evaluate_js(
                        "document.getElementById('app')?.children.length > 0"))
                    logging.info('WebView2 smoke test: UI loaded=%s', smoke['loaded'])
                finally:
                    window.destroy()
            window.events.loaded += on_loaded
            watchdog = threading.Timer(30, window.destroy)
            watchdog.daemon = True
            watchdog.start()
        try:
            webview.start(gui='edgechromium', debug=False)
        finally:
            if watchdog:
                watchdog.cancel()
        if args.smoke_test and not smoke['loaded']:
            raise RuntimeError('WebView2 UI smoke test failed')
    finally:
        server.should_exit = True
        thread.join(timeout=SHUTDOWN_GRACE_SECONDS + 30)
        listener.close()


if __name__ == '__main__':
    import multiprocessing
    multiprocessing.freeze_support()
    try:
        main()
    except Exception as exc:
        logging.exception('Desktop startup failed')
        if sys.platform == 'win32' and not sys.stdout:
            import ctypes
            detail = str(exc) if isinstance(exc, DesktopDependencyError) else '请查看日志定位启动错误：' + str(exc)[:500]
            ctypes.windll.user32.MessageBoxW(None, f'{detail}\n\n日志：{DATA_DIR / "forma.log"}', 'Forma CAD', 0x10)
            if isinstance(exc, DesktopDependencyError):
                raise SystemExit(1) from None
        raise
