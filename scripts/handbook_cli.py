#!/usr/bin/env python3
"""
Token-efficient CLI for AI Agents to query 《机械设计手册（第六版）》 + Machine Design Databook.

Zero external dependencies for:
  overview                         Ultra-compact map of all volumes & parts
  toc [query] [--vol ...]          Search/list fine-grained TOC with exact PDF page ranges
  search <query> [--vol ...]       BM25 full-text search across 9,808 pages (CJK + EN)
  table <query> [--vol ...]        Search 6,942 engineering data tables by title or table number
  std <code_query> [--vol ...]     Search 3,066 GB/JB/ISO/DIN/ASTM standards
  read <vol> <pages>               Read cleaned compact text of specific page(s) or book-page (e.g. book:6-70)

Optional visual rendering (requires original PDFs + pypdfium2 + pillow):
  render <vol> <pages>             Render exact page(s) to crisp PNG for multimodal visual verification
"""

import os
import sys
import re
import json
import sqlite3
import argparse

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
KB_DIR = os.path.abspath(os.path.join(SCRIPT_DIR, '..'))
DB_PATH = os.path.join(KB_DIR, 'handbook.db')
RENDER_DIR = os.path.join(KB_DIR, 'rendered')


def resolve_pdf_dir(cli_pdf_dir=None):
    candidates = []
    if cli_pdf_dir:
        candidates.append(os.path.abspath(cli_pdf_dir))
    env_dir = os.environ.get('HANDBOOK_PDF_DIR')
    if env_dir:
        candidates.append(os.path.abspath(env_dir))
    # Default fallbacks: ./pdfs inside repo, or parent directory of repo
    candidates.append(os.path.join(KB_DIR, 'pdfs'))
    candidates.append(os.path.abspath(os.path.join(KB_DIR, '..')))
    for c in candidates:
        if os.path.isdir(c):
            if any(f.lower().endswith('.pdf') for f in os.listdir(c)):
                return c
    return candidates[0]


def get_conn():
    if not os.path.exists(DB_PATH):
        print(f'ERROR: Knowledge base DB not found at {DB_PATH}. Run scripts/build_kb.py first.', file=sys.stderr)
        sys.exit(1)
    conn = sqlite3.connect(f'file:{DB_PATH}?mode=ro', uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _norm_term(term: str) -> str:
    term = term.strip().replace('—', '-').replace('⁃', '-').replace('－', '-')
    toks = re.sub(r'([\u4e00-\u9fff])', r' \1 ', term)
    toks = re.sub(r'\s+', ' ', toks).strip()
    toks = toks.replace('"', '')
    return f'"{toks}"' if toks else ''


def build_fts_query(raw_query: str, use_or: bool = False) -> str:
    parts = [p for p in re.split(r'\s+', raw_query.strip()) if p]
    phrases = [_norm_term(p) for p in parts]
    phrases = [p for p in phrases if p]
    if not phrases:
        return ''
    op = ' OR ' if use_or else ' AND '
    return op.join(phrases)


def make_snippet(text: str, raw_query: str, max_len: int = 140) -> str:
    flat = re.sub(r'\s+', ' ', text).strip()
    if not flat:
        return ''
    terms = [t.strip() for t in re.split(r'\s+', raw_query.strip()) if t.strip()]
    pos = -1
    for t in terms:
        m = re.search(re.escape(t), flat, re.I)
        if m:
            pos = m.start()
            break
    if pos == -1:
        return flat[:max_len] + ('...' if len(flat) > max_len else '')
    start = max(0, pos - 40)
    end = min(len(flat), start + max_len)
    prefix = '...' if start > 0 else ''
    suffix = '...' if end < len(flat) else ''
    return f'{prefix}{flat[start:end]}{suffix}'


def parse_page_spec(spec: str, conn, vol_id: str, max_pages: int = 10):
    spec = spec.strip()
    if spec.lower().startswith(('book:', 'b:')):
        bp = spec.split(':', 1)[1].strip()
        row = conn.execute(
            'SELECT pdf_page FROM pages WHERE vol_id=? AND book_page=?',
            (vol_id, bp)
        ).fetchone()
        if row:
            return [row['pdf_page']]
        raise ValueError(f"Book page '{bp}' not found in volume '{vol_id}'.")

    pages = []
    for chunk in spec.split(','):
        chunk = chunk.strip()
        if not chunk:
            continue
        if '-' in chunk:
            a, b = chunk.split('-', 1)
            a_i, b_i = int(a), int(b)
            if b_i < a_i:
                raise ValueError(f'Invalid page range: {chunk}')
            pages.extend(range(a_i, b_i + 1))
        else:
            pages.append(int(chunk))

    seen = set()
    uniq = []
    for p in pages:
        if p not in seen:
            seen.add(p)
            uniq.append(p)
    if len(uniq) > max_pages:
        uniq = uniq[:max_pages]
    return uniq


def cmd_overview(args):
    conn = get_conn()
    vols = conn.execute('SELECT * FROM volumes').fetchall()
    print('=== 机械设计手册(第六版) + Databook 知识库总览 ===')
    for v in vols:
        print(f"  {v['vol_id']:3s} | {v['label']} | {v['parts_summary']} | {v['total_pages']}p")
    print('\n--- 中文各篇快捷路由 (L1) ---')
    parts = conn.execute(
        "SELECT vol_id, title, pdf_start, pdf_end FROM sections WHERE level=1 AND vol_id LIKE 'v%' ORDER BY vol_id, pdf_start"
    ).fetchall()
    for p in parts:
        print(f"  {p['vol_id']} | p.{p['pdf_start']}-{p['pdf_end']} | {p['title']}")
    print('\n--- 英文 Machine Design Databook (en) 章节 ---')
    en_chaps = conn.execute(
        "SELECT title, pdf_start, pdf_end FROM sections WHERE vol_id='en' AND level=1 ORDER BY pdf_start"
    ).fetchall()
    for c in en_chaps:
        print(f"  en | p.{c['pdf_start']}-{c['pdf_end']} | {c['title']}")
    conn.close()


def cmd_toc(args):
    conn = get_conn()
    query = ' '.join(args.query).strip() if args.query else ''
    limit = args.limit

    if not query:
        sql = 'SELECT vol_id, level, part_title, chapter_title, title, book_page, pdf_start, pdf_end FROM sections WHERE 1=1'
        params = []
        if args.vol:
            sql += ' AND vol_id=?'
            params.append(args.vol)
        if args.max_level:
            sql += ' AND level<=?'
            params.append(args.max_level)
        sql += ' ORDER BY vol_id, id LIMIT ?'
        params.append(limit)
        rows = conn.execute(sql, params).fetchall()
    else:
        fts_q = build_fts_query(query, use_or=False)
        sql = '''
            SELECT s.vol_id, s.level, s.part_title, s.chapter_title, s.title, s.book_page, s.pdf_start, s.pdf_end, s.standards
            FROM sections_fts f
            JOIN sections s ON s.id = f.section_id
            WHERE sections_fts MATCH ?
        '''
        params = [fts_q]
        if args.vol:
            sql += ' AND s.vol_id=?'
            params.append(args.vol)
        sql += ' ORDER BY bm25(sections_fts) LIMIT ?'
        params.append(limit)
        rows = conn.execute(sql, params).fetchall()

        if not rows and ' ' in query:
            params[0] = build_fts_query(query, use_or=True)
            rows = conn.execute(sql, params).fetchall()
            if rows:
                print('[提示] 严格AND无命中，已自动切换为OR匹配：')

    if args.json:
        print(json.dumps([dict(r) for r in rows], ensure_ascii=False, indent=2))
    else:
        if not rows:
            print('未命中目录项，请尝试 `search` 全文检索或更换近义关键词。')
        for r in rows:
            pr = f"p.{r['pdf_start']}-{r['pdf_end']}" if r['pdf_start'] != r['pdf_end'] else f"p.{r['pdf_start']}"
            bp = f" ({r['book_page']})" if r['book_page'] else ''
            ctx = f"{r['part_title']} / {r['chapter_title']}" if r['level'] > 2 else r['part_title']
            if r['level'] == 1:
                print(f"{r['vol_id']} | {pr:11s} | [L1] {r['title']}")
            else:
                print(f"{r['vol_id']} | {pr:11s}{bp} | {ctx} > {r['title']}")
    conn.close()


def cmd_search(args):
    conn = get_conn()
    query = ' '.join(args.query).strip()
    if not query:
        print('ERROR: Please provide search keywords.', file=sys.stderr)
        sys.exit(1)

    fts_q = build_fts_query(query, use_or=False)
    sql = '''
        SELECT p.vol_id, p.pdf_page, p.book_page, p.part_title, p.chapter_title, p.section_title, p.clean_text, p.has_formula_or_table
        FROM pages_fts f
        JOIN pages p ON p.vol_id = f.vol_id AND p.pdf_page = f.pdf_page
        WHERE pages_fts MATCH ?
    '''
    params = [fts_q]
    if args.vol:
        sql += ' AND p.vol_id=?'
        params.append(args.vol)
    sql += ' ORDER BY bm25(pages_fts) LIMIT ?'
    params.append(args.limit)
    rows = conn.execute(sql, params).fetchall()

    fallback = False
    if not rows and ' ' in query:
        fallback = True
        params[0] = build_fts_query(query, use_or=True)
        rows = conn.execute(sql, params).fetchall()

    if args.json:
        out = []
        for r in rows:
            out.append({
                'vol_id': r['vol_id'], 'pdf_page': r['pdf_page'], 'book_page': r['book_page'],
                'chapter': r['chapter_title'], 'section': r['section_title'],
                'snippet': make_snippet(r['clean_text'], query, 160),
                'has_formula_or_table': bool(r['has_formula_or_table']),
            })
        print(json.dumps(out, ensure_ascii=False, indent=2))
    else:
        if fallback and rows:
            print('[提示] 严格AND无命中，已切换为OR相关性排序：')
        if not rows:
            print(f'未检索到 "{query}"。建议：精简关键词、检查英文版(en)或用 `toc` 浏览相关篇目。')
        for idx, r in enumerate(rows, 1):
            bp = f" (书页{r['book_page']})" if r['book_page'] else ''
            ft = ' [含表/公式]' if r['has_formula_or_table'] else ''
            loc = ' > '.join(x for x in (r['chapter_title'], r['section_title']) if x)
            snip = make_snippet(r['clean_text'], query, 130)
            print(f"{idx}. {r['vol_id']} p.{r['pdf_page']}{bp}{ft} | {loc}")
            print(f"   {snip}")
    conn.close()


def cmd_table(args):
    conn = get_conn()
    query = ' '.join(args.query).strip()
    fts_q = build_fts_query(query, use_or=False)
    sql = '''
        SELECT t.vol_id, t.pdf_page, t.book_page, t.table_no, t.title, t.chapter_title
        FROM tables_fts f
        JOIN tables_index t ON t.id = f.table_id
        WHERE tables_fts MATCH ?
    '''
    params = [fts_q]
    if args.vol:
        sql += ' AND t.vol_id=?'
        params.append(args.vol)
    sql += ' ORDER BY bm25(tables_fts) LIMIT ?'
    params.append(args.limit)
    rows = conn.execute(sql, params).fetchall()

    if not rows and ' ' in query:
        params[0] = build_fts_query(query, use_or=True)
        rows = conn.execute(sql, params).fetchall()

    if args.json:
        print(json.dumps([dict(r) for r in rows], ensure_ascii=False, indent=2))
    else:
        if not rows:
            print(f'表索引中未命中 "{query}"，可改用 `search` 全文搜索。')
        for r in rows:
            bp = f" ({r['book_page']})" if r['book_page'] else ''
            print(f"{r['vol_id']} p.{r['pdf_page']}{bp} | {r['table_no']} | {r['title']} | [{r['chapter_title']}]")
    conn.close()


def cmd_std(args):
    conn = get_conn()
    raw = ' '.join(args.query).strip().upper().replace('—', '-').replace('⁃', '-')
    like_pat = '%' + re.sub(r'\s+', '%', raw) + '%'
    sql = '''
        SELECT std_code, vol_id, pdf_page, book_page, chapter_title, context
        FROM standards_index
        WHERE REPLACE(UPPER(std_code), ' ', '') LIKE REPLACE(?, ' ', '')
           OR UPPER(std_code) LIKE ?
    '''
    params = [like_pat, like_pat]
    if args.vol:
        sql += ' AND vol_id=?'
        params.append(args.vol)
    sql += ' ORDER BY std_code, vol_id, pdf_page LIMIT ?'
    params.append(args.limit)
    rows = conn.execute(sql, params).fetchall()

    if args.json:
        print(json.dumps([dict(r) for r in rows], ensure_ascii=False, indent=2))
    else:
        if not rows:
            print(f'未在标准索引中命中 "{raw}"，可尝试 `search {raw}`。')
        for r in rows:
            bp = f" ({r['book_page']})" if r['book_page'] else ''
            print(f"{r['std_code']:18s} | {r['vol_id']} p.{r['pdf_page']}{bp} | {r['chapter_title']} | {r['context']}")
    conn.close()


def cmd_read(args):
    conn = get_conn()
    vol_id = args.vol.lower()
    try:
        pages = parse_page_spec(args.pages, conn, vol_id, max_pages=6)
    except ValueError as e:
        print(f'ERROR: {e}', file=sys.stderr)
        sys.exit(1)

    total_budget = args.max_chars
    used = 0
    for pg in pages:
        row = conn.execute(
            'SELECT * FROM pages WHERE vol_id=? AND pdf_page=?',
            (vol_id, pg)
        ).fetchone()
        if not row:
            print(f'=== {vol_id} p.{pg}: NOT FOUND ===')
            continue

        bp = f" | 书页:{row['book_page']}" if row['book_page'] else ''
        loc = ' > '.join(x for x in (row['part_title'], row['chapter_title'], row['section_title']) if x)
        print(f"=== {vol_id} p.{pg}{bp} | {loc} ===")

        tbls = conn.execute(
            'SELECT table_no, title FROM tables_index WHERE vol_id=? AND pdf_page=?',
            (vol_id, pg)
        ).fetchall()
        if tbls:
            t_str = '; '.join(f"{t['table_no']} {t['title']}" for t in tbls)
            print(f"[本页数据表] {t_str}")

        txt = row['clean_text']
        if not txt:
            print(f"[本页无文字层/纯图页] 若本地已配置原 PDF，请运行: render {vol_id} {pg}")
            continue

        remain = total_budget - used
        if remain <= 0:
            print(f"[已达 --max-chars={total_budget} 截断上限，如需继续请单独 read {vol_id} {pg}]")
            break
        if len(txt) > remain:
            print(txt[:remain] + f"\n... [本页已截断，完整长度 {len(txt)} 字符，可增大 --max-chars]")
            used = total_budget
        else:
            print(txt)
            used += len(txt)

        if row['has_formula_or_table']:
            print(f"[视觉核验建议] 本页含公式/数据表/图例，若需精准读取单元格数值或公式上下标，可运行: render {vol_id} {pg}")
        print()
    conn.close()


def cmd_render(args):
    try:
        import pypdfium2 as pdfium
    except ImportError:
        print('ERROR: `render` requires optional packages `pypdfium2` and `pillow`. Install via: uv sync --extra pdf (or pip install pypdfium2 pillow)', file=sys.stderr)
        sys.exit(1)

    conn = get_conn()
    vol_id = args.vol.lower()
    vol = conn.execute('SELECT filename, total_pages FROM volumes WHERE vol_id=?', (vol_id,)).fetchone()
    if not vol:
        print(f"ERROR: Unknown volume '{vol_id}'. Valid: v1, v2, v3, v4, v5, en, ra", file=sys.stderr)
        sys.exit(1)

    try:
        pages = parse_page_spec(args.pages, conn, vol_id, max_pages=5)
    except ValueError as e:
        print(f'ERROR: {e}', file=sys.stderr)
        sys.exit(1)
    conn.close()

    pdf_dir = resolve_pdf_dir(args.pdf_dir)
    pdf_path = os.path.join(pdf_dir, vol['filename'])
    if not os.path.exists(pdf_path):
        print(
            f"ERROR: Source PDF not found: {pdf_path}\n"
            f"Hint: Original PDFs are excluded from Git. Set HANDBOOK_PDF_DIR or pass --pdf-dir <folder_with_pdfs> to enable PNG rendering.",
            file=sys.stderr
        )
        sys.exit(1)

    out_dir = os.path.abspath(args.out) if args.out else RENDER_DIR
    os.makedirs(out_dir, exist_ok=True)
    doc = pdfium.PdfDocument(pdf_path)

    scale = args.dpi / 72.0
    for pg in pages:
        if pg < 1 or pg > len(doc):
            print(f'[WARN] {vol_id} p.{pg} out of range (1..{len(doc)})', file=sys.stderr)
            continue
        page = doc[pg - 1]
        bitmap = page.render(scale=scale)
        pil_img = bitmap.to_pil()

        suffix = ''
        if args.clip:
            w, h = pil_img.size
            if args.clip == 'top':
                pil_img = pil_img.crop((0, 0, w, int(h * 0.55)))
            elif args.clip == 'bottom':
                pil_img = pil_img.crop((0, int(h * 0.45), w, h))
            elif args.clip == 'left':
                pil_img = pil_img.crop((0, 0, int(w * 0.53), h))
            elif args.clip == 'right':
                pil_img = pil_img.crop((int(w * 0.47), 0, w, h))
            suffix = f'_{args.clip}'

        fname = f'{vol_id}_p{pg:04d}_{args.dpi}dpi{suffix}.png'
        out_path = os.path.join(out_dir, fname)
        pil_img.save(out_path, format='PNG', optimize=True)
        print(f'RENDERED: {out_path} ({pil_img.width}x{pil_img.height})')

    doc.close()


def main():
    parser = argparse.ArgumentParser(description='Token-efficient Mechanical Design Handbook CLI for AI Agents')
    sub = parser.add_subparsers(dest='cmd', required=True)

    sub.add_parser('overview', help='Show compact map of all volumes, parts, and English chapters')

    p_toc = sub.add_parser('toc', help='Search or browse fine-grained TOC')
    p_toc.add_argument('query', nargs='*', help='Optional keywords to search in TOC')
    p_toc.add_argument('--vol', choices=['v1', 'v2', 'v3', 'v4', 'v5', 'en', 'ra'])
    p_toc.add_argument('--max-level', type=int, default=3)
    p_toc.add_argument('--limit', type=int, default=25)
    p_toc.add_argument('--json', action='store_true')

    p_s = sub.add_parser('search', help='Full-text BM25 search across all 9,808 pages')
    p_s.add_argument('query', nargs='+', help='Search keywords (Chinese or English)')
    p_s.add_argument('--vol', choices=['v1', 'v2', 'v3', 'v4', 'v5', 'en', 'ra'])
    p_s.add_argument('--limit', type=int, default=8)
    p_s.add_argument('--json', action='store_true')

    p_t = sub.add_parser('table', help='Search 6,942 engineering data tables')
    p_t.add_argument('query', nargs='+', help='Table title keywords or number (e.g. 梯形螺纹 旋合长度 / 6-1-11)')
    p_t.add_argument('--vol', choices=['v1', 'v2', 'v3', 'v4', 'v5', 'en'])
    p_t.add_argument('--limit', type=int, default=15)
    p_t.add_argument('--json', action='store_true')

    p_std = sub.add_parser('std', help='Search 3,066 GB/JB/ISO/DIN/ASTM standards')
    p_std.add_argument('query', nargs='+', help='Standard code or number (e.g. GB/T 3098 or 1800)')
    p_std.add_argument('--vol', choices=['v1', 'v2', 'v3', 'v4', 'v5', 'en'])
    p_std.add_argument('--limit', type=int, default=15)
    p_std.add_argument('--json', action='store_true')

    p_r = sub.add_parser('read', help='Read cleaned compact text of specific page(s)')
    p_r.add_argument('vol', choices=['v1', 'v2', 'v3', 'v4', 'v5', 'en', 'ra'])
    p_r.add_argument('pages', help='PDF page(s) e.g. 50, 50-52, 50,55 or book page book:6-70')
    p_r.add_argument('--max-chars', type=int, default=4500)

    p_ren = sub.add_parser('render', help='Render specific PDF page(s) to PNG for exact visual table/formula reading')
    p_ren.add_argument('vol', choices=['v1', 'v2', 'v3', 'v4', 'v5', 'en', 'ra'])
    p_ren.add_argument('pages', help='PDF page(s) e.g. 50 or 50-51 or book:6-70 (max 5 pages)')
    p_ren.add_argument('--dpi', type=int, default=170)
    p_ren.add_argument('--clip', choices=['top', 'bottom', 'left', 'right'])
    p_ren.add_argument('--out', help='Optional output directory for PNG files')
    p_ren.add_argument('--pdf-dir', help='Directory containing source PDF files (or set HANDBOOK_PDF_DIR)')

    args = parser.parse_args()
    dispatch = {
        'overview': cmd_overview,
        'toc': cmd_toc,
        'search': cmd_search,
        'table': cmd_table,
        'std': cmd_std,
        'read': cmd_read,
        'render': cmd_render,
    }
    dispatch[args.cmd](args)


if __name__ == '__main__':
    main()
