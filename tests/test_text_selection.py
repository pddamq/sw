"""Guard desktop text selection without starting WebView, services, or CAD."""
import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_desktop_window_explicitly_enables_text_selection():
    tree = ast.parse((ROOT / 'desktop.py').read_text('utf-8'))
    windows = [node for node in ast.walk(tree)
               if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
               and isinstance(node.func.value, ast.Name) and node.func.value.id == 'webview'
               and node.func.attr == 'create_window']
    assert windows, 'The desktop window configuration must be covered'
    for window in windows:
        setting = next((item.value for item in window.keywords if item.arg == 'text_select'), None)
        assert isinstance(setting, ast.Constant) and setting.value is True


def test_reply_styles_allow_nested_text_selection():
    css = (ROOT / 'frontend' / 'src' / 'improvements.css').read_text('utf-8')
    block = css.split('.message-body .prose,', 1)[1].split('}', 1)[0]
    assert '.message-body .prose *' in block
    assert '.message-body .reasoning-detail *' in block
    assert '.message-body .tool-line-detail *' in block
    assert '-webkit-user-select: text;' in block
    assert 'user-select: text;' in block
