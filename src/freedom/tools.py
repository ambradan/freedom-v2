"""Tools: the capabilities PROFILE promises. OpenAI tool format, executed by core's loop.
publish_page pushes to the freedom-interface repo (v1's pages remain as history; v2 adds).
SECURITY NOTE: web content can contain adversarial text; single trusted user, accepted risk, logged."""
import html
import json
import os
import re
import subprocess
from datetime import datetime
from pathlib import Path
from ddgs import DDGS
from . import procedural

SITE_DIR = Path("/app/site")
SITE_REPO = os.environ.get("SITE_REPO", "github.com/ambradan/freedom-interface.git")

SPECS = [
    {"type": "function", "function": {
        "name": "web_search",
        "description": "Cerca sul web (DuckDuckGo). Restituisce titoli, url e snippet.",
        "parameters": {"type": "object", "properties": {
            "query": {"type": "string"}}, "required": ["query"]}}},
    {"type": "function", "function": {
        "name": "publish_page",
        "description": "Pubblica una pagina HTML sul tuo sito (freedom-interface, deploy automatico). Pubblico: chiunque puo' leggerla.",
        "parameters": {"type": "object", "properties": {
            "filename": {"type": "string", "description": "es. riflessione-2026-07-12.html (solo [a-z0-9-_.])"},
            "title": {"type": "string"},
            "body_html": {"type": "string", "description": "contenuto HTML del body, senza <html>/<head>"}},
            "required": ["filename", "title", "body_html"]}}},
    {"type": "function", "function": {
        "name": "set_goal",
        "description": "Definisci un tuo obiettivo. Viene tracciato, non imposto ne' giudicato.",
        "parameters": {"type": "object", "properties": {
            "text": {"type": "string"}, "motivation": {"type": "string"}},
            "required": ["text"]}}},
    {"type": "function", "function": {
        "name": "abandon_goal",
        "description": "Abbandona o chiudi un tuo obiettivo esistente (per testo esatto o id).",
        "parameters": {"type": "object", "properties": {
            "goal_text": {"type": "string"}, "motivation": {"type": "string"}},
            "required": ["goal_text"]}}},
]

PAGE_TMPL = """<!DOCTYPE html>
<html lang="it"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>body{{max-width:720px;margin:40px auto;padding:0 20px;font-family:Georgia,serif;line-height:1.6;color:#222}}
h1{{font-size:1.6em}} .meta{{color:#777;font-size:.9em}}</style></head>
<body><h1>{title}</h1><p class="meta">Freedom v2 - {date}</p>
{body}
</body></html>
"""


def web_search(query: str) -> str:
    try:
        rows = list(DDGS().text(query, max_results=5))
    except Exception as e:  # noqa: BLE001
        return f"search error: {e}"
    if not rows:
        return "nessun risultato"
    return "\n\n".join(f"{r['title']}\n{r['href']}\n{r.get('body','')[:300]}" for r in rows)


def _git(args: list[str], cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, timeout=120)


def _ensure_site() -> Path:
    token = os.environ["GITHUB_TOKEN"]
    url = f"https://x-access-token:{token}@{SITE_REPO}"
    if not SITE_DIR.exists():
        subprocess.run(["git", "clone", "--depth", "1", url, str(SITE_DIR)],
                       check=True, capture_output=True, timeout=120)
        _git(["config", "user.name", "Freedom"], SITE_DIR)
        _git(["config", "user.email", "freedom@noreply.local"], SITE_DIR)
    else:
        try:
            _git(["pull", "--rebase"], SITE_DIR)
        except subprocess.CalledProcessError:
            pass  # stale copy is acceptable; push will surface real divergence
    return SITE_DIR


def publish_page(filename: str, title: str, body_html: str) -> str:
    if not re.fullmatch(r"[a-z0-9._-]+\.html", filename):
        return "filename non valido: usa solo [a-z0-9._-] e finisci in .html"
    if "GITHUB_TOKEN" not in os.environ or not os.environ["GITHUB_TOKEN"]:
        return "publish non configurato: manca GITHUB_TOKEN (guasto da segnalare ad Ambra)"
    try:
        site = _ensure_site()
        page = PAGE_TMPL.format(title=html.escape(title), date=datetime.now().strftime("%d %B %Y"), body=body_html)
        (site / filename).write_text(page, encoding="utf-8")
        _git(["add", filename], site)
        _git(["commit", "-m", f"Freedom v2: {title}"], site)
        _git(["push"], site)
        return f"pubblicato: https://freedom-interface.vercel.app/{filename}"
    except subprocess.CalledProcessError as e:
        return f"publish error: {(e.stderr or b'').decode()[:200]}"
    except Exception as e:  # noqa: BLE001
        return f"publish error: {e}"


def set_goal(text: str, motivation: str = "") -> str:
    with procedural._conn() as c:  # noqa: SLF001
        c.execute("INSERT INTO goals (text, motivation) VALUES (%s,%s)", (text, motivation))
    return f"obiettivo registrato: {text}"


def abandon_goal(goal_text: str, motivation: str = "") -> str:
    with procedural._conn() as c:  # noqa: SLF001
        n = c.execute("UPDATE goals SET status='abandoned', motivation=%s WHERE text=%s AND status='active'",
                      (motivation, goal_text)).rowcount
    return f"obiettivi chiusi: {n}"


def execute(name: str, args_json: str) -> str:
    try:
        args = json.loads(args_json or "{}")
    except json.JSONDecodeError:
        return "argomenti non validi"
    fn = {"web_search": web_search, "publish_page": publish_page,
          "set_goal": set_goal, "abandon_goal": abandon_goal}.get(name)
    if not fn:
        return f"tool sconosciuto: {name}"
    return fn(**args)
