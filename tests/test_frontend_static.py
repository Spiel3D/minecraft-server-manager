"""Règles statiques de l'interface : compatibles avec la CSP stricte (pas de script ni de style en ligne), pas d'insertion HTML."""
import re
from pathlib import Path

FRONT = Path(__file__).resolve().parent.parent / "frontend"


def test_index_has_no_inline_code_and_all_scripts_exist():
    html = (FRONT / "index.html").read_text()
    assert not re.search(r"<script(?![^>]*\bsrc=)", html) and "<style" not in html and not re.search(r"\sstyle=|\son\w+=", html)
    for src in re.findall(r'<script src="/([^"]+)"', html):
        assert (FRONT / src).is_file(), src
    assert "views2.js" in html


def test_js_never_injects_html_or_inline_style_text():
    for f in FRONT.glob("*.js"):
        code = re.sub(r"/\*.*?\*/", "", f.read_text(), flags=re.S)        # commentaires exclus
        for bad in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(", 'setAttribute("style"', "new Function"):
            assert bad not in code, f"{f.name} utilise {bad}"


def test_css_has_no_external_resources():
    css = (FRONT / "style.css").read_text()
    assert "http://" not in css and "https://" not in css and "@import" not in css
