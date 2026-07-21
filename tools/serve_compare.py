#!/usr/bin/env python3
"""
serve_compare.py - browse the compare/ reports in a browser.

Reads the Markdown written by compare_game_pois.py and serves it as styled
HTML, using the same weathered palette as the map app. Files are re-read on
every request, so after re-running the compare tool a browser refresh is
enough - no restart.

    python tools/serve_compare.py                 # http://127.0.0.1:8778
    python tools/serve_compare.py --open
    python tools/serve_compare.py --dir compare --port 9000

Read-only: it serves the .md files in the target directory and nothing else.
"""

from __future__ import annotations

import argparse
import html
import http.server
import re
import socketserver
import sys
import threading
import webbrowser
from pathlib import Path

# --------------------------------------------------------------------------
# a small Markdown renderer - only what the reports actually use
#   headings, tables (with alignment), blockquotes, unordered lists,
#   horizontal rules, paragraphs, and inline bold / italic / code / links
# --------------------------------------------------------------------------

# raw tags the reports emit on purpose and that are safe to pass through
INLINE_OK = ("sub", "sup", "br", "em", "strong")

_CODE_TOKEN = "\x00CODE{}\x00"


def _inline(text):
    """Escape, then apply inline Markdown. Code spans stay literal."""
    spans = []

    def stash(m):
        spans.append(m.group(1))
        return _CODE_TOKEN.format(len(spans) - 1)

    text = re.sub(r"`([^`]+)`", stash, text)
    text = html.escape(text, quote=False)

    # let the handful of intentional raw tags back through
    for tag in INLINE_OK:
        text = text.replace(f"&lt;{tag}&gt;", f"<{tag}>")
        text = text.replace(f"&lt;/{tag}&gt;", f"</{tag}>")
        text = text.replace(f"&lt;{tag}/&gt;", f"<{tag}>")
    text = re.sub(r"&amp;(#?\w{2,8});", r"&\1;", text)   # &middot; &#10003;

    text = re.sub(r"\[([^\]]+)\]\(([^)\s]+)\)",
                  lambda m: f'<a href="{html.escape(m.group(2), quote=True)}">'
                            f'{m.group(1)}</a>', text)
    text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"(?<![\w\\])_([^_]+)_(?![\w])", r"<em>\1</em>", text)

    for i, code in enumerate(spans):
        text = text.replace(_CODE_TOKEN.format(i),
                            "<code>" + html.escape(code, quote=False) + "</code>")
    return text


def _slug(text, seen):
    s = re.sub(r"<[^>]+>", "", text)
    s = re.sub(r"[^\w\s-]", "", s.lower()).strip()
    s = re.sub(r"[\s_]+", "-", s) or "section"
    n = seen.get(s, 0)
    seen[s] = n + 1
    return s if n == 0 else f"{s}-{n + 1}"


def _split_row(line):
    line = line.strip()
    if line.startswith("|"):
        line = line[1:]
    if line.endswith("|"):
        line = line[:-1]
    return [c.strip() for c in line.split("|")]


def _alignments(sep_cells):
    out = []
    for c in sep_cells:
        c = c.strip()
        if c.startswith(":") and c.endswith(":"):
            out.append("center")
        elif c.endswith(":"):
            out.append("right")
        else:
            out.append("left")
    return out


def render(md):
    """Markdown -> (html, toc) where toc is [(level, id, text)]."""
    lines = md.replace("\r\n", "\n").split("\n")
    out, toc, seen = [], [], {}
    i, n = 0, len(lines)

    def close_list(stack):
        while stack:
            out.append("</ul>")
            stack.pop()

    ul = []
    while i < n:
        line = lines[i]
        stripped = line.strip()

        # blank
        if not stripped:
            close_list(ul)
            i += 1
            continue

        # horizontal rule
        if re.fullmatch(r"-{3,}|\*{3,}|_{3,}", stripped):
            close_list(ul)
            out.append("<hr>")
            i += 1
            continue

        # heading
        m = re.match(r"(#{1,6})\s+(.*)$", stripped)
        if m:
            close_list(ul)
            lvl = len(m.group(1))
            body = _inline(m.group(2).rstrip("# ").strip())
            hid = _slug(body, seen)
            out.append(f'<h{lvl} id="{hid}">{body}'
                       f'<a class="anchor" href="#{hid}" aria-label="link">#</a></h{lvl}>')
            if 1 < lvl <= 3:
                toc.append((lvl, hid, re.sub(r"<[^>]+>", "", body)))
            i += 1
            continue

        # table: a pipe row followed by an alignment row
        if stripped.startswith("|") and i + 1 < n and \
                re.fullmatch(r"\|?[\s:|-]+\|?", lines[i + 1].strip()) and \
                "-" in lines[i + 1]:
            close_list(ul)
            head = _split_row(stripped)
            align = _alignments(_split_row(lines[i + 1]))
            i += 2
            body = []
            while i < n and lines[i].strip().startswith("|"):
                body.append(_split_row(lines[i].strip()))
                i += 1
            out.append('<div class="table-wrap"><table><thead><tr>')
            for j, c in enumerate(head):
                a = align[j] if j < len(align) else "left"
                out.append(f'<th class="a-{a}">{_inline(c)}</th>')
            out.append("</tr></thead><tbody>")
            for row in body:
                out.append("<tr>")
                for j, c in enumerate(row):
                    a = align[j] if j < len(align) else "left"
                    out.append(f'<td class="a-{a}">{_inline(c)}</td>')
                out.append("</tr>")
            out.append("</tbody></table></div>")
            continue

        # blockquote (consume the run)
        if stripped.startswith(">"):
            close_list(ul)
            buf = []
            while i < n and lines[i].strip().startswith(">"):
                buf.append(lines[i].strip()[1:].strip())
                i += 1
            out.append("<blockquote>" + _inline(" ".join(buf)) + "</blockquote>")
            continue

        # unordered list item
        m = re.match(r"[-*+]\s+(.*)$", stripped)
        if m:
            if not ul:
                out.append("<ul>")
                ul.append(1)
            out.append("<li>" + _inline(m.group(1)) + "</li>")
            i += 1
            continue

        # ordered list item
        m = re.match(r"\d+\.\s+(.*)$", stripped)
        if m:
            close_list(ul)
            out.append("<ol>")
            while i < n and re.match(r"\d+\.\s+", lines[i].strip()):
                out.append("<li>" + _inline(re.sub(r"^\d+\.\s+", "",
                                                   lines[i].strip())) + "</li>")
                i += 1
            out.append("</ol>")
            continue

        # paragraph (join until a blank line or a block starter)
        buf = []
        while i < n and lines[i].strip() and \
                not re.match(r"(#{1,6}\s|[-*+]\s|\d+\.\s|>|\|)", lines[i].strip()) and \
                not re.fullmatch(r"-{3,}", lines[i].strip()):
            buf.append(lines[i].strip())
            i += 1
        if buf:
            close_list(ul)
            out.append("<p>" + _inline(" ".join(buf)) + "</p>")
        else:
            i += 1

    close_list(ul)
    return "\n".join(out), toc


# --------------------------------------------------------------------------
# page shell
# --------------------------------------------------------------------------

CSS = """
:root{
  --bg:#100e0b; --panel:#1c1813; --panel-2:#241f18; --panel-hi:#2c261d;
  --line:#382f23; --line-hi:#4a3d2c;
  --ink:#ddd0ba; --muted:#8b8070; --faint:#6a6153;
  --gold:#c9a24b; --gold-hi:#e6c876; --blood:#a83828; --blood-hi:#c9503b;
  --green:#7f9b53;
  --serif:Georgia,"Iowan Old Style","Times New Roman",serif;
  --sans:"Segoe UI",system-ui,-apple-system,Roboto,Helvetica,Arial,sans-serif;
  --mono:ui-monospace,SFMono-Regular,"SF Mono",Consolas,monospace;
}
*{box-sizing:border-box}
html,body{margin:0;padding:0}
body{
  background:radial-gradient(1200px 600px at 50% -10%,#1d1811 0,transparent 60%),
             linear-gradient(180deg,#12100c 0,#0c0a08 100%);
  background-attachment:fixed;
  color:var(--ink); font-family:var(--sans); font-size:15px; line-height:1.55;
  min-height:100vh;
}
a{color:var(--gold-hi);text-decoration:none}
a:hover{text-decoration:underline}

.topbar{
  position:sticky; top:0; z-index:30; display:flex; align-items:center; gap:20px;
  height:56px; padding:0 20px;
  background:linear-gradient(180deg,rgba(20,17,12,.98),rgba(16,13,10,.94));
  border-bottom:1px solid var(--line); backdrop-filter:blur(6px);
}
.brand{font-family:var(--serif);font-size:20px;font-weight:700;letter-spacing:.5px;
  color:var(--ink);white-space:nowrap}
.brand span{color:var(--gold)}
.docnav{display:flex;gap:4px;flex:1;flex-wrap:wrap}
.docnav a{padding:6px 12px;border-radius:4px;border:1px solid transparent;
  color:var(--muted);font-size:13px;font-weight:600;letter-spacing:.3px;
  text-transform:uppercase}
.docnav a:hover{color:var(--ink);background:var(--panel);text-decoration:none}
.docnav a.active{color:var(--gold-hi);border-color:var(--line-hi);background:var(--panel)}
.stamp{color:var(--faint);font-size:12px;font-family:var(--mono);white-space:nowrap}

.wrap{display:grid;grid-template-columns:250px minmax(0,1fr);gap:28px;
  max-width:1320px;margin:0 auto;padding:24px 20px 70px}
.toc{position:sticky;top:80px;align-self:start;max-height:calc(100vh - 110px);
  overflow:auto;border-left:1px solid var(--line);padding-left:14px}
.toc h4{margin:0 0 8px;font-family:var(--serif);font-size:11px;letter-spacing:1.2px;
  text-transform:uppercase;color:var(--muted)}
.toc a{display:block;padding:3px 0;color:var(--muted);font-size:13px;
  border-left:2px solid transparent;padding-left:8px;margin-left:-10px}
.toc a:hover{color:var(--ink);text-decoration:none}
.toc a.lvl3{padding-left:20px;font-size:12.5px;color:var(--faint)}
.toc a.here{color:var(--gold-hi);border-left-color:var(--gold)}

main{min-width:0}
h1,h2,h3,h4{font-family:var(--serif);color:var(--ink);line-height:1.2;
  scroll-margin-top:72px}
h1{font-size:32px;margin:0 0 14px}
h2{font-size:25px;margin:34px 0 12px;padding-bottom:8px;border-bottom:1px solid var(--line)}
h3{font-size:19px;margin:26px 0 8px;color:var(--gold-hi)}
h4{font-size:16px;margin:18px 0 6px}
h2 sub{font-family:var(--sans);font-size:12px;color:var(--faint);font-weight:400}
.anchor{margin-left:8px;color:var(--line-hi);font-size:.65em;opacity:0;transition:.15s}
h1:hover .anchor,h2:hover .anchor,h3:hover .anchor,h4:hover .anchor{opacity:1}

p{margin:10px 0}
hr{border:0;border-top:1px solid var(--line);margin:26px 0}
ul,ol{margin:10px 0;padding-left:22px}
li{margin:3px 0}
blockquote{margin:14px 0;padding:10px 14px;border-left:3px solid var(--gold);
  background:rgba(201,162,75,.07);color:var(--ink);border-radius:0 4px 4px 0}
code{font-family:var(--mono);font-size:.86em;background:var(--panel-2);
  border:1px solid var(--line);border-radius:3px;padding:1px 5px;color:var(--gold-hi)}
strong{color:#f0e6d2}
em{color:var(--muted)}

.table-wrap{overflow-x:auto;margin:12px 0;border:1px solid var(--line);
  border-radius:5px;background:linear-gradient(180deg,var(--panel),#17140f)}
table{border-collapse:collapse;width:100%;font-size:13.5px}
/* .table-wrap is the scroll container, so sticky resolves against it -
   top:0 pins the header without leaving a gap under the wrap's edge */
thead th{position:sticky;top:0;background:var(--panel-hi);color:var(--muted);
  font-family:var(--sans);font-size:11px;letter-spacing:.6px;text-transform:uppercase;
  font-weight:700;padding:9px 12px;border-bottom:1px solid var(--line-hi);text-align:left}
td{padding:7px 12px;border-bottom:1px solid rgba(56,47,35,.6)}
tbody tr:last-child td{border-bottom:0}
tbody tr:nth-child(odd){background:rgba(0,0,0,.16)}
tbody tr:hover{background:rgba(201,162,75,.07)}
td code{background:transparent;border:0;padding:0}
.a-right{text-align:right}.a-center{text-align:center}.a-left{text-align:left}
td.a-right,th.a-right{font-variant-numeric:tabular-nums}

.empty{margin:60px auto;max-width:640px;text-align:center;color:var(--muted)}
.empty h1{color:var(--blood-hi)}
.empty pre{display:inline-block;text-align:left;background:var(--panel);
  border:1px solid var(--line);border-radius:5px;padding:12px 16px;
  font-family:var(--mono);font-size:13px;color:var(--ink)}

@media (max-width:900px){
  .wrap{grid-template-columns:1fr;gap:0}
  .toc{position:static;max-height:none;border-left:0;border-bottom:1px solid var(--line);
    padding:0 0 12px;margin-bottom:16px}
  thead th{position:static}
}
"""

TOC_JS = """
(function(){
  var links=[].slice.call(document.querySelectorAll('.toc a'));
  var targets=links.map(function(a){return document.getElementById(a.hash.slice(1));});
  function onScroll(){
    var best=-1;
    for(var i=0;i<targets.length;i++){
      if(targets[i] && targets[i].getBoundingClientRect().top<=90) best=i;
    }
    links.forEach(function(a,i){a.classList.toggle('here',i===best);});
  }
  document.addEventListener('scroll',onScroll,{passive:true});
  onScroll();
})();
"""


def page(title, nav_html, toc_html, body_html, stamp):
    return f"""<!DOCTYPE html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)}</title>
<style>{CSS}</style>
</head><body>
<header class="topbar">
  <span class="brand">Hunt<span>Map</span> compare</span>
  <nav class="docnav">{nav_html}</nav>
  <span class="stamp">{html.escape(stamp)}</span>
</header>
<div class="wrap">
  <aside class="toc">{toc_html}</aside>
  <main>{body_html}</main>
</div>
<script>{TOC_JS}</script>
</body></html>"""


def empty_page(directory):
    body = f"""<div class="empty">
<h1>No reports yet</h1>
<p>Nothing to show in <code>{html.escape(str(directory))}</code>.
Generate the comparison first:</p>
<pre>python tools/compare_game_pois.py
python tools/compare_game_pois.py --suggest</pre>
<p>Then refresh this page.</p></div>"""
    return page("HuntMap compare", "", "", body, "")


# --------------------------------------------------------------------------
# server
# --------------------------------------------------------------------------

def build_handler(directory: Path):

    def docs():
        """All .md in the directory, REPORT first, then SUGGEST, then the rest."""
        found = sorted(p for p in directory.glob("*.md") if p.is_file())
        rank = {"report.md": 0, "suggest.md": 1}
        return sorted(found, key=lambda p: (rank.get(p.name.lower(), 2), p.name))

    def label(path: Path):
        return path.stem.replace("_", " ").replace("-", " ").title()

    class Handler(http.server.BaseHTTPRequestHandler):
        server_version = "HuntMapCompare/1.0"

        def log_message(self, fmt, *a):        # keep the console readable
            pass

        def _send(self, body: str, code=200):
            data = body.encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(data)

        def do_HEAD(self):
            self.do_GET()

        def do_GET(self):
            path = self.path.split("?", 1)[0].split("#", 1)[0]
            available = docs()

            if not available:
                self._send(empty_page(directory), 200 if path == "/" else 404)
                return

            if path in ("/", "/index.html"):
                current = available[0]
            else:
                name = path.lstrip("/")
                # only ever serve a .md that is actually in the directory
                current = next((p for p in available
                                if p.name.lower() == name.lower()
                                or p.stem.lower() == name.lower()), None)
                if current is None:
                    self._send(page("Not found", "", "",
                                    '<div class="empty"><h1>404</h1>'
                                    '<p>No such report. <a href="/">Back</a></p></div>',
                                    ""), 404)
                    return

            try:
                md = current.read_text(encoding="utf-8")
            except OSError as exc:
                self._send(page("Error", "", "",
                                f'<div class="empty"><h1>Cannot read</h1>'
                                f'<p>{html.escape(str(exc))}</p></div>', ""), 500)
                return

            body, toc = render(md)

            nav = "".join(
                f'<a class="{"active" if p == current else ""}" '
                f'href="/{p.stem}">{html.escape(label(p))}</a>'
                for p in available)

            if toc:
                items = "".join(
                    f'<a class="lvl{lvl}" href="#{hid}">{html.escape(text)}</a>'
                    for lvl, hid, text in toc)
                toc_html = f"<h4>On this page</h4>{items}"
            else:
                toc_html = ""

            mtime = current.stat().st_mtime
            import datetime
            stamp = datetime.datetime.fromtimestamp(mtime).strftime(
                "%Y-%m-%d %H:%M")
            self._send(page(f"{label(current)} - HuntMap compare",
                            nav, toc_html, body, stamp))

    return Handler


class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def main(argv=None):
    root = Path(__file__).resolve().parent.parent
    ap = argparse.ArgumentParser(
        description="Serve the compare/ Markdown reports as HTML.")
    ap.add_argument("--dir", default=str(root / "compare"),
                    help="directory holding the .md reports (default: compare/)")
    ap.add_argument("--port", type=int, default=8778, help="port (default: 8778)")
    ap.add_argument("--host", default="127.0.0.1",
                    help="bind address (default: 127.0.0.1, local only)")
    ap.add_argument("--open", action="store_true",
                    help="open the page in a browser once the server is up")
    args = ap.parse_args(argv)

    directory = Path(args.dir).resolve()
    if not directory.is_dir():
        raise SystemExit(f"no such directory: {directory}\n"
                         "run tools/compare_game_pois.py first")

    found = sorted(directory.glob("*.md"))
    url = f"http://{args.host}:{args.port}/"

    try:
        httpd = Server((args.host, args.port), build_handler(directory))
    except OSError as exc:
        raise SystemExit(f"cannot bind {args.host}:{args.port} - {exc}")

    print(f"serving {directory}")
    if found:
        for p in found:
            print(f"  {p.name}")
    else:
        print("  (no .md files yet - run tools/compare_game_pois.py)")
    print(f"\n{url}   Ctrl+C to stop")

    if args.open:
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
