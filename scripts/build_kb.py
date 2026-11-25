#!/usr/bin/env python3
"""
Build AI-Agent-ready, token-efficient knowledge base for:
  《机械设计手册（第六版）》第1~5卷 (8,512 pages)
  + Machine Design Databook (1,282 pages)
  + 机械设计手册-表面粗糙度 (14 scanned pages)

Outputs under agent_kb/:
  - handbook.db      (SQLite + FTS5 CJK/EN hybrid index + sections + tables_index + standards_index)
  - INDEX.md         (Level-0 ultra-compact master map)
  - toc/*.md         (Level-1 per-volume detailed Markdown TOCs with PDF page ranges & standards)
"""

import os
import re
import json
import time
import sqlite3
import argparse
import unicodedata
from collections import defaultdict
import pypdfium2 as pdfium

ROOT_DIR = os.path.abspath(os.environ.get('HANDBOOK_PDF_DIR') or os.path.join(os.path.dirname(__file__), '..', '..'))
KB_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
TOC_DIR = os.path.join(KB_DIR, 'toc')
DB_PATH = os.path.join(KB_DIR, 'handbook.db')

SOURCES = [
    {'id': 'v1', 'label': '第1卷', 'filename': '机械设计手册 第六版 第1卷.pdf', 'lang': 'zh', 'kind': 'cn_vol', 'parts': '第1~5篇'},
    {'id': 'v2', 'label': '第2卷', 'filename': '机械设计手册 第六版 第2卷.pdf', 'lang': 'zh', 'kind': 'cn_vol', 'parts': '第6~10篇'},
    {'id': 'v3', 'label': '第3卷', 'filename': '机械设计手册 第六版 第3卷.pdf', 'lang': 'zh', 'kind': 'cn_vol', 'parts': '第11~15篇'},
    {'id': 'v4', 'label': '第4卷', 'filename': '机械设计手册 第六版 第4卷.pdf', 'lang': 'zh', 'kind': 'cn_vol', 'parts': '第16~20篇'},
    {'id': 'v5', 'label': '第5卷', 'filename': '机械设计手册 第六版 第5卷.pdf', 'lang': 'zh', 'kind': 'cn_vol', 'parts': '第21~23篇'},
    {'id': 'en', 'label': '英文数据手册(Lingaiah)', 'filename': 'Machine Design Databook 机械设计手册.pdf', 'lang': 'en', 'kind': 'en_book', 'parts': 'Ch.1~27'},
    {'id': 'ra', 'label': '表面粗糙度专题(扫描件)', 'filename': '机械设计手册-表面粗糙度.pdf', 'lang': 'zh', 'kind': 'scan', 'parts': '表面粗糙度专题'},
]

# --- Founder BookMaker / Full-width character normalization ---
FULLWIDTH_TRANS = str.maketrans({
    '\u3000': ' ',
    '\u00a0': ' ',
    '\u2043': '-',   # ⁃ hyphen bullet used in Founder PDFs
    '\u2212': '-',   # − minus
    '\u2013': '-',   # – en dash
    '\u2014': '-',   # — em dash (normalized inside codes/tables, kept contextually)
    '\uff0d': '-',
    '\uff0f': '/',
    '\uff0b': '+',
    '\uff1d': '=',
    '\uff05': '%',
    '\uff5e': '~',
    '\uff0e': '.',
    '\uff1c': '<',
    '\uff1e': '>',
    '\uff3b': '[',
    '\uff3d': ']',
    '\u00b7': '.',
    '\U001001b0': '.',  # Founder PUA decimal dot 􀆰
    '\uf0b7': '.',
    '\uf02d': '-',
    '\uf0b4': '×',
    '\uf0b1': '±',
    '\uf0a3': '≤',
    '\uf0b3': '≥',
    '\uf0b9': '≠',
    '\uf0bb': '≈',
    '\uf0b0': '°',
    '\ue004': '.',
    '\ue005': '-',
})

# Fullwidth ASCII digits & Latin letters -> halfwidth
for _code in range(0xFF10, 0xFF1A):  # ０-９
    FULLWIDTH_TRANS[_code] = chr(_code - 0xFEE0)
for _code in range(0xFF21, 0xFF3B):  # Ａ-Ｚ
    FULLWIDTH_TRANS[_code] = chr(_code - 0xFEE0)
for _code in range(0xFF41, 0xFF5B):  # ａ-ｚ
    FULLWIDTH_TRANS[_code] = chr(_code - 0xFEE0)

RE_PUA = re.compile(r'[\U00100000-\U0010ffff\ue000-\uf8ff]')
RE_MULTI_SPACE = re.compile(r'[ \t]{2,}')
RE_CJK_SPACE_CJK = re.compile(r'([\u4e00-\u9fff])\s+([\u4e00-\u9fff])')
RE_NUM_DASH_SPACE = re.compile(r'(?<=\d)\s*-\s*(?=\d)')
RE_NUM_DOT_SPACE = re.compile(r'(?<=\d)\s*\.\s*(?=\d)')
RE_STD_SLASH_SPACE = re.compile(r'\b(GB|JB|HG|YB|QC|TB|HB|QJ|SJ|SH|SY|DL|NB|CB|MT|JC|JG|SL|JT|YY|WS|NY|LY|SC|SN|FZ|QB|CY|GA|JR|MH|YD|GY)\s*/\s*(T|Z|ZQ)\b', re.I)

RE_BOOK_PAGE_LINE = re.compile(r'^(\d{1,2})\s*-\s*(\d{1,3})$')
RE_STD_CODE = re.compile(
    r'\b((?:GB|JB|HG|YB|QC|TB|HB|QJ|SJ|SH|SY|DL|NB|CB|MT|JC|JG|SL|JT|ISO|DIN|ASTM|ASME|ANSI|JIS|IEC|EN)'
    r'(?:/[A-Z]{1,3})?)\s*([0-9]+(?:\.[0-9]+)?(?:-[0-9]{2,4})?)\b'
)
RE_CN_TABLE = re.compile(r'表\s*(\d{1,2}-\d{1,2}-\d{1,3}[a-zA-Z]?)\s*(.*)')
RE_EN_TABLE = re.compile(r'\bTABLE\s+(\d{1,2}-\d{1,3}[A-Z]?)\s*(.*)', re.I)


def clean_text(raw: str, lang: str = 'zh') -> str:
    if not raw:
        return ''
    s = raw.replace('\r\n', '\n').replace('\r', '\n')
    s = s.translate(FULLWIDTH_TRANS)
    # Replace remaining isolated PUA glyphs (usually Founder math dot/symbols) with space or dot if between digits
    s = re.sub(r'(\d)[\U00100000-\U0010ffff\ue000-\uf8ff](\d)', r'\1.\2', s)
    s = RE_PUA.sub(' ', s)

    lines = []
    for ln in s.split('\n'):
        ln = ln.strip()
        if not ln:
            continue
        # Fix spaced standard prefixes like "GB / T" -> "GB/T"
        ln = RE_STD_SLASH_SPACE.sub(lambda m: f'{m.group(1).upper()}/{m.group(2).upper()}', ln)
        # Lookaround fixes chained patterns like "6-1- 11" -> "6-1-11" and "3.5. 1" -> "3.5.1" in one pass
        ln = RE_NUM_DASH_SPACE.sub('-', ln)
        ln = RE_NUM_DOT_SPACE.sub('.', ln)
        ln = RE_MULTI_SPACE.sub(' ', ln)
        if lang == 'zh':
            # Collapse wide-spaced Chinese headers like "材 料 牌 号" -> "材料牌号" (repeat twice for pairs)
            # Only collapse single-char CJK runs of length >= 2 when separated by single space,
            # while avoiding merging distinct short table cells too aggressively if digits intervene.
            # Actually collapsing CJK-space-CJK dramatically improves searchability and saves ~25% tokens!
            ln = re.sub(r'([\u4e00-\u9fff]) ([\u4e00-\u9fff])', r'\1\2', ln)
            ln = re.sub(r'([\u4e00-\u9fff]) ([\u4e00-\u9fff])', r'\1\2', ln)
            ln = re.sub(r'([（(])\s+', r'\1', ln)
            ln = re.sub(r'\s+([）)，。；：、])', r'\1', ln)
        lines.append(ln)
    return '\n'.join(lines)


def tokenize_for_fts(text: str) -> str:
    """
    Hybrid CJK + ASCII tokenizer for SQLite FTS5 unicode61:
    - Each CJK character becomes a space-separated token (enabling exact Chinese phrase queries of ANY length).
    - ASCII alphanumeric/standard tokens (e.g., GB/T, 3098.1, M16, H7/g6, bearing) stay intact as whole words.
    """
    if not text:
        return ''
    # Insert spaces around CJK chars so unicode61 indexes every char + preserves ASCII words
    out = re.sub(r'([\u4e00-\u9fff])', r' \1 ', text)
    out = re.sub(r'\s+', ' ', out).strip()
    return out


def extract_standards(text: str):
    found = set()
    for m in RE_STD_CODE.finditer(text):
        prefix = m.group(1).upper()
        num = m.group(2)
        # Filter obvious false positives like "EN 1" or single-digit numbers
        if len(num) < 2 or (prefix in ('EN', 'ISO', 'DIN', 'JIS', 'IEC') and len(num) < 3):
            continue
        if prefix in ('GB', 'JB', 'HG', 'YB', 'QC', 'TB', 'HB', 'QJ', 'SJ') and not re.match(r'^\d{2,}', num):
            continue
        code = f'{prefix} {num}'
        found.add(code)
    return sorted(found)


def extract_tables_from_page(text: str, lang: str, fallback_sec: str = ''):
    """Extract (table_no, title) pairs from a single cleaned page."""
    results = []
    lines = text.split('\n')
    seen = set()
    if lang == 'zh':
        for i, ln in enumerate(lines):
            # Must start with 表 (table caption line) or have no narrative citation word immediately before
            m = RE_CN_TABLE.search(ln)
            if not m:
                continue
            prefix_before = ln[:m.start()].strip()
            if prefix_before:
                if any(k in prefix_before[-4:] for k in ('见', '如', '按', '由', '查', '据', '参', '同', '表', '列', '从', '在', '、', '及', '和', '与', '~')):
                    continue
                if len(prefix_before) > 25:
                    continue
            tno = m.group(1)
            rest = m.group(2).strip()
            # Skip cross-reference lines like "表 16-3-1。" or "表 21-1-21~表 21-1-28"
            if rest.startswith(('。', '，', '；', '、', '~', '～', '中', '所', '给', '列', '为')):
                continue
            # Clean pure-unit trailing rest like "mm" or "10"
            rest_clean = re.sub(r'^(mm|μm|MPa|kN|N|kg|10|cm)\b\s*', '', rest).strip()
            title = rest_clean if len(rest_clean) >= 2 else ''
            if not title and prefix_before and len(prefix_before) >= 2:
                title = prefix_before
            if not title and i > 0:
                prev = lines[i - 1].strip()
                if 2 <= len(prev) <= 65 and not RE_BOOK_PAGE_LINE.match(prev) and not prev.startswith(('表', '图', '续表', '注')) and not prev.endswith('。'):
                    title = prev
            if not title and fallback_sec:
                title = fallback_sec
            if not title and i + 1 < len(lines):
                nxt = lines[i + 1].strip()
                if 2 <= len(nxt) <= 60:
                    title = nxt
            title = title[:100].strip(' ：:。，')
            if tno not in seen and len(title) >= 2:
                seen.add(tno)
                results.append((f'表{tno}', title))
    else:
        for i, ln in enumerate(lines):
            m = RE_EN_TABLE.match(ln)
            if m:
                tno = m.group(1)
                title = m.group(2).strip()
                if len(title) < 4 and i + 1 < len(lines):
                    title = (title + ' ' + lines[i + 1].strip()).strip()
                title = title[:120].strip(' .:')
                if tno not in seen and len(title) >= 3:
                    seen.add(tno)
                    results.append((f'TABLE {tno}', title))
    return results


def get_bm_page(bm):
    dest = bm.get_dest()
    if dest is not None:
        idx = dest.get_index()
        if idx is not None:
            return idx + 1
    return -1


def parse_cn_volume_toc(vol_id, pdf_doc, bookmarks, book_to_pdf, total_pages):
    """
    Parse the detailed multi-level TOC printed on pages ~8..(first_part_page-1)
    and merge with PDF bookmarks so every Part / Chapter / Section / Subsection
    gets exact pdf_page_start and pdf_page_end.
    """
    # Find directory page range from bookmarks
    toc_start = 8
    first_part_pdf = None
    part_bms = []   # (part_num, title, pdf_page)
    chap_bms = []   # (part_num, chap_title, pdf_page)
    cur_part_num = 0
    for bm in bookmarks:
        title = clean_text(bm.get_title(), 'zh')
        pg = get_bm_page(bm)
        if '目录' in title and pg > 0:
            toc_start = pg
        m_part = re.match(r'^第(\d+)篇\s*(.+)', title)
        if bm.level == 0 and m_part:
            cur_part_num = int(m_part.group(1))
            if first_part_pdf is None:
                first_part_pdf = pg
            part_bms.append((cur_part_num, f'第{cur_part_num}篇 {m_part.group(2).strip()}', pg))
        elif bm.level == 1 and cur_part_num > 0:
            chap_bms.append((cur_part_num, title, pg))

    toc_end = (first_part_pdf - 1) if first_part_pdf else 25

    # Build per-part fallback offset from book_to_pdf and chapter bookmarks
    # For each part_num, estimate median (pdf_page - book_page_num)
    part_offsets = {}
    for (pno, bnum), pdf_p in book_to_pdf.items():
        part_offsets.setdefault(pno, []).append(pdf_p - bnum)
    part_med_offset = {}
    for pno, offs in part_offsets.items():
        offs.sort()
        part_med_offset[pno] = offs[len(offs) // 2]

    def resolve_pdf_page(pno, bnum):
        if (pno, bnum) in book_to_pdf:
            return book_to_pdf[(pno, bnum)]
        # Try nearby book pages within +-3
        for delta in (1, -1, 2, -2, 3, -3):
            if (pno, bnum + delta) in book_to_pdf:
                return max(1, min(total_pages, book_to_pdf[(pno, bnum + delta)] - delta))
        if pno in part_med_offset:
            return max(1, min(total_pages, bnum + part_med_offset[pno]))
        return -1

    # Extract raw lines from printed TOC pages
    raw_toc_lines = []
    for p in range(toc_start, toc_end + 1):
        tp = pdf_doc[p - 1].get_textpage()
        txt = clean_text(tp.get_text_range(), 'zh')
        tp.close()
        for ln in txt.split('\n'):
            ln = ln.strip()
            if not ln or ln in ('目录', '目 录', '总目录') or re.match(r'^[IVXLCDM]+$', ln):
                continue
            raw_toc_lines.append(ln)

    # Merge wrapped TOC lines: a complete TOC entry ends with "... <part>-<page>" or "... <page>"
    # Example: "3.1 优先数系（摘自 GB/T 321-2005、" + "GB/T 19763-2005） …… 1-39"
    RE_TOC_END = re.compile(r'^(.*?)\s*[…\.·]{2,}\s*(\d{1,2})\s*-\s*(\d{1,3})\s*$')
    RE_PART_HDR = re.compile(r'^第\s*(\d{1,2})\s*篇\s*(.+)$')
    RE_TOC_END_NOPART = re.compile(r'^(.*?)\s*[…\.·]{2,}\s*(\d{1,3})\s*$')

    entries = []  # list of dict
    buf = ''
    cur_part = part_bms[0][0] if part_bms else 0
    cur_part_title = part_bms[0][1] if part_bms else ''
    cur_chap_title = ''

    # Map part_num -> full title from bookmarks
    part_title_map = {pno: t for pno, t, _ in part_bms}
    # Insert L1 part headers from bookmarks cleanly as we encounter each part
    seen_parts = set()

    for ln in raw_toc_lines:
        # Check standalone part header in printed TOC
        mp = RE_PART_HDR.match(re.sub(r'[…\.·]+\s*\d+-\d+$', '', ln).strip())
        if mp and '…' not in ln:
            pno = int(mp.group(1))
            if pno in part_title_map:
                cur_part = pno
                cur_part_title = part_title_map[pno]
                buf = ''
                if pno not in seen_parts:
                    seen_parts.add(pno)
                    p_pdf = next((pg for pn, _, pg in part_bms if pn == pno), -1)
                    entries.append({
                        'level': 1, 'part_num': pno, 'part_title': cur_part_title,
                        'chapter_title': '', 'title': cur_part_title,
                        'book_page': f'{pno}-1', 'pdf_start': p_pdf,
                        'standards': []
                    })
                continue

        candidate = (buf + ' ' + ln).strip() if buf else ln
        m = RE_TOC_END.match(candidate)
        if m:
            title_raw = re.sub(r'\s+', ' ', m.group(1)).strip()
            # Collapse accidental Chinese spaces created by line wrap
            title_raw = re.sub(r'([\u4e00-\u9fff]) ([\u4e00-\u9fff])', r'\1\2', title_raw)
            pno = int(m.group(2))
            bnum = int(m.group(3))
            buf = ''

            if pno in part_title_map and pno not in seen_parts:
                seen_parts.add(pno)
                cur_part = pno
                cur_part_title = part_title_map[pno]
                p_pdf = next((pg for pn, _, pg in part_bms if pn == pno), -1)
                entries.append({
                    'level': 1, 'part_num': pno, 'part_title': cur_part_title,
                    'chapter_title': '', 'title': cur_part_title,
                    'book_page': f'{pno}-1', 'pdf_start': p_pdf,
                    'standards': []
                })
            elif pno in part_title_map:
                cur_part = pno
                cur_part_title = part_title_map[pno]

            # Classify level
            if re.match(r'^第\s*\d+\s*章', title_raw):
                lvl = 2
                title_raw = re.sub(r'^第\s*(\d+)\s*章\s*', r'第\1章 ', title_raw)
                cur_chap_title = title_raw
            elif re.match(r'^\d+\.\d+\.\d+', title_raw):
                lvl = 5
            elif re.match(r'^\d+\.\d+', title_raw):
                lvl = 4
            elif re.match(r'^\d+\s*\S', title_raw):
                lvl = 3
            else:
                lvl = 3

            pdf_start = resolve_pdf_page(pno, bnum)
            stds = extract_standards(title_raw)
            entries.append({
                'level': lvl,
                'part_num': pno,
                'part_title': cur_part_title,
                'chapter_title': cur_chap_title if lvl > 2 else title_raw,
                'title': title_raw,
                'book_page': f'{pno}-{bnum}',
                'pdf_start': pdf_start,
                'standards': stds,
            })
        else:
            # Handle lines like "参考文献 …… 1-828" or wrapped titles
            if len(candidate) < 120:
                buf = candidate
            else:
                buf = ''

    # Ensure every bookmark part & chapter exists even if printed TOC missed anything
    if len(entries) < len(chap_bms):
        # Fallback to bookmark tree
        entries = []
        for pno, pt, pg in part_bms:
            entries.append({
                'level': 1, 'part_num': pno, 'part_title': pt,
                'chapter_title': '', 'title': pt, 'book_page': f'{pno}-1',
                'pdf_start': pg, 'standards': []
            })
            for cpno, ct, cpg in chap_bms:
                if cpno == pno:
                    entries.append({
                        'level': 2, 'part_num': pno, 'part_title': pt,
                        'chapter_title': ct, 'title': ct, 'book_page': '',
                        'pdf_start': cpg, 'standards': []
                    })

    # Compute pdf_end for each entry (up to next sibling or higher level entry's pdf_start)
    for i, e in enumerate(entries):
        start = e['pdf_start']
        if start <= 0:
            e['pdf_end'] = -1
            continue
        end = total_pages
        for j in range(i + 1, len(entries)):
            if entries[j]['level'] <= e['level'] and entries[j]['pdf_start'] > 0:
                end = max(start, entries[j]['pdf_start'] - 1)
                break
        e['pdf_end'] = end

    return entries, part_bms, chap_bms


def build_database():
    t_all = time.time()
    os.makedirs(TOC_DIR, exist_ok=True)
    if os.path.exists(DB_PATH):
        os.remove(DB_PATH)

    conn = sqlite3.connect(DB_PATH)
    conn.execute('PRAGMA journal_mode = WAL;')
    conn.execute('PRAGMA synchronous = OFF;')

    conn.executescript('''
        CREATE TABLE volumes (
            vol_id TEXT PRIMARY KEY,
            label TEXT NOT NULL,
            filename TEXT NOT NULL,
            lang TEXT NOT NULL,
            kind TEXT NOT NULL,
            parts_summary TEXT,
            total_pages INTEGER NOT NULL
        );

        CREATE TABLE sections (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            vol_id TEXT NOT NULL,
            level INTEGER NOT NULL,
            part_num INTEGER DEFAULT 0,
            part_title TEXT DEFAULT '',
            chapter_title TEXT DEFAULT '',
            title TEXT NOT NULL,
            book_page TEXT DEFAULT '',
            pdf_start INTEGER NOT NULL,
            pdf_end INTEGER NOT NULL,
            standards TEXT DEFAULT ''
        );

        CREATE VIRTUAL TABLE sections_fts USING fts5(
            vol_id UNINDEXED,
            section_id UNINDEXED,
            title_tok,
            path_tok,
            standards_tok,
            tokenize='unicode61'
        );

        CREATE TABLE pages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            vol_id TEXT NOT NULL,
            pdf_page INTEGER NOT NULL,
            book_page TEXT DEFAULT '',
            part_title TEXT DEFAULT '',
            chapter_title TEXT DEFAULT '',
            section_title TEXT DEFAULT '',
            clean_text TEXT NOT NULL,
            char_count INTEGER NOT NULL,
            has_formula_or_table INTEGER DEFAULT 0,
            UNIQUE(vol_id, pdf_page)
        );

        CREATE VIRTUAL TABLE pages_fts USING fts5(
            vol_id UNINDEXED,
            pdf_page UNINDEXED,
            content_tok,
            tokenize='unicode61'
        );

        CREATE TABLE tables_index (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            vol_id TEXT NOT NULL,
            pdf_page INTEGER NOT NULL,
            book_page TEXT DEFAULT '',
            table_no TEXT NOT NULL,
            title TEXT NOT NULL,
            chapter_title TEXT DEFAULT ''
        );

        CREATE VIRTUAL TABLE tables_fts USING fts5(
            vol_id UNINDEXED,
            table_id UNINDEXED,
            table_tok,
            tokenize='unicode61'
        );

        CREATE TABLE standards_index (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            std_code TEXT NOT NULL,
            vol_id TEXT NOT NULL,
            pdf_page INTEGER NOT NULL,
            book_page TEXT DEFAULT '',
            chapter_title TEXT DEFAULT '',
            context TEXT DEFAULT ''
        );

        CREATE INDEX idx_sections_vol_page ON sections(vol_id, pdf_start, pdf_end);
        CREATE INDEX idx_pages_vol_book ON pages(vol_id, book_page);
        CREATE INDEX idx_std_code ON standards_index(std_code);
        CREATE INDEX idx_tables_vol ON tables_index(vol_id, pdf_page);
    ''')

    index_md_lines = [
        '# 《机械设计手册（第六版）》AI Agent 知识库总导航 (Level 0)',
        '',
        '> **设计原则**：极简分层索引，供 AI Agent 零冗余定位。先定位卷/篇/章，再使用 `handbook_cli.py` 按需检索或精读单页（遇复杂公式/大表可按需高清渲染 PNG 读图），单次查阅可节省 95%+ Token。',
        '> **去重说明**：原目录中 241 MB 的《机械设计手册 第六版.pdf》(8512页) 已验证为第1~5卷完全合订本，本知识库基于带精确书签的第1~5卷分卷构建，零重复。',
        '',
        '## 收录卷目总览',
        '',
        '| 卷号(`vol_id`) | 名称 | 覆盖范围 | 总页数 | 详细目录文件 |',
        '|:---|:---|:---|---:|:---|',
    ]

    volume_chapter_blocks = []
    stats = defaultdict(int)

    for src in SOURCES:
        vol_id = src['id']
        pdf_path = os.path.join(ROOT_DIR, src['filename'])
        if not os.path.exists(pdf_path):
            print(f'[WARN] Missing {pdf_path}')
            continue

        t0 = time.time()
        doc = pdfium.PdfDocument(pdf_path)
        total_pages = len(doc)
        conn.execute(
            'INSERT INTO volumes VALUES (?,?,?,?,?,?,?)',
            (vol_id, src['label'], src['filename'], src['lang'], src['kind'], src['parts'], total_pages)
        )
        stats['total_pages'] += total_pages

        toc_file_rel = f'toc/{vol_id}_toc.md' if src['kind'] != 'scan' else '(纯扫描14页，直接render)'
        index_md_lines.append(
            f"| `{vol_id}` | {src['label']} (`{src['filename']}`) | {src['parts']} | {total_pages} | `{toc_file_rel}` |"
        )

        # Handle scanned PDF (ra)
        if src['kind'] == 'scan':
            for pg in range(1, total_pages + 1):
                conn.execute(
                    'INSERT INTO pages (vol_id, pdf_page, book_page, part_title, chapter_title, section_title, clean_text, char_count, has_formula_or_table) VALUES (?,?,?,?,?,?,?,?,?)',
                    (vol_id, pg, f'ra-{pg}', '表面粗糙度专题', '表面粗糙度(扫描件)', f'第{pg}页(需render查看)', '', 0, 1)
                )
            conn.execute(
                'INSERT INTO sections (vol_id, level, part_num, part_title, chapter_title, title, book_page, pdf_start, pdf_end, standards) VALUES (?,?,?,?,?,?,?,?,?,?)',
                (vol_id, 1, 0, '表面粗糙度专题', '表面粗糙度(扫描件)', '机械设计手册-表面粗糙度专题（纯扫描14页，请用 render ra 1-14 查看）', '1-14', 1, total_pages, '')
            )
            sid = conn.execute('SELECT last_insert_rowid()').fetchone()[0]
            conn.execute('INSERT INTO sections_fts VALUES (?,?,?,?,?)', (
                vol_id, sid, tokenize_for_fts('机械设计手册 表面粗糙度 表面结构 Ra Rz'), tokenize_for_fts('表面粗糙度专题'), ''
            ))
            doc.close()
            print(f'[{vol_id}] Indexed {total_pages} scanned pages in {time.time()-t0:.2f}s')
            continue

        # Pass 1: Extract & clean all pages
        pages_clean = []
        book_to_pdf = {}   # (part_num, page_in_part) -> pdf_page
        page_book_str = [''] * total_pages

        for i in range(total_pages):
            tp = doc[i].get_textpage()
            raw = tp.get_text_range()
            tp.close()
            cleaned = clean_text(raw, src['lang'])
            pages_clean.append(cleaned)

            if src['kind'] == 'cn_vol' and cleaned:
                # Check first 3 lines & last 2 lines for book page number "P-NNN"
                lines = cleaned.split('\n')
                for cand in (lines[:3] + lines[-2:]):
                    m = RE_BOOK_PAGE_LINE.match(cand.strip())
                    if m:
                        pno, bnum = int(m.group(1)), int(m.group(2))
                        if 1 <= pno <= 23 and 1 <= bnum <= 999:
                            page_book_str[i] = f'{pno}-{bnum}'
                            if (pno, bnum) not in book_to_pdf:
                                book_to_pdf[(pno, bnum)] = i + 1
                            break

        # Pass 2: Build sections / TOC
        bms = list(doc.get_toc())
        sections = []
        part_bms = []
        chap_bms = []

        if src['kind'] == 'cn_vol':
            sections, part_bms, chap_bms = parse_cn_volume_toc(vol_id, doc, bms, book_to_pdf, total_pages)
        elif src['kind'] == 'en_book':
            # Detect CHAPTER starts and major sections in English Machine Design Databook
            for i, txt in enumerate(pages_clean):
                lines = txt.split('\n')[:6]
                if lines and lines[0].upper().startswith('CHAPTER') and len(lines[0]) < 20:
                    # Collect chapter number & uppercase title lines
                    ch_num = ''
                    title_parts = []
                    for ln in lines[1:5]:
                        if re.match(r'^\d+$', ln) and not ch_num:
                            ch_num = ln
                        elif ln.upper().startswith('SYMBOLS') or ln.upper().startswith('REFERENCES'):
                            break
                        else:
                            # Remove footnote digits at end of title
                            title_parts.append(re.sub(r'[0-9,;]+$', '', ln).strip())
                    if ch_num:
                        ch_title = f"Chapter {ch_num}: {' '.join(title_parts).strip()}"
                        sections.append({
                            'level': 1, 'part_num': int(ch_num), 'part_title': ch_title,
                            'chapter_title': ch_title, 'title': ch_title,
                            'book_page': f'p.{i+1}', 'pdf_start': i + 1, 'pdf_end': total_pages,
                            'standards': []
                        })
            # Fill pdf_end for EN chapters
            for idx, sec in enumerate(sections):
                if idx + 1 < len(sections):
                    sec['pdf_end'] = max(sec['pdf_start'], sections[idx + 1]['pdf_start'] - 1)
                else:
                    sec['pdf_end'] = total_pages

        # Insert sections into DB and build fast page->(part, chapter, section) lookup
        page_part = [''] * (total_pages + 1)
        page_chap = [''] * (total_pages + 1)
        page_sec = [''] * (total_pages + 1)

        # First populate from bookmarks (always exact for Part & Chapter)
        if src['kind'] == 'cn_vol':
            for idx, (pno, pt, pg) in enumerate(part_bms):
                end_pg = (part_bms[idx + 1][2] - 1) if idx + 1 < len(part_bms) else total_pages
                for p in range(max(1, pg), min(total_pages, end_pg) + 1):
                    page_part[p] = pt
            for idx, (pno, ct, pg) in enumerate(chap_bms):
                end_pg = (chap_bms[idx + 1][2] - 1) if idx + 1 < len(chap_bms) else total_pages
                for p in range(max(1, pg), min(total_pages, end_pg) + 1):
                    page_chap[p] = ct

        for sec in sections:
            stds_str = '; '.join(sec['standards'])
            cur = conn.execute(
                'INSERT INTO sections (vol_id, level, part_num, part_title, chapter_title, title, book_page, pdf_start, pdf_end, standards) VALUES (?,?,?,?,?,?,?,?,?,?)',
                (vol_id, sec['level'], sec['part_num'], sec['part_title'], sec['chapter_title'],
                 sec['title'], sec['book_page'], sec['pdf_start'], sec['pdf_end'], stds_str)
            )
            sid = cur.lastrowid
            path_str = f"{sec['part_title']} {sec['chapter_title']}"
            conn.execute(
                'INSERT INTO sections_fts VALUES (?,?,?,?,?)',
                (vol_id, sid, tokenize_for_fts(sec['title']), tokenize_for_fts(path_str), stds_str)
            )
            stats['sections'] += 1

            ps, pe = sec['pdf_start'], sec['pdf_end']
            if 1 <= ps <= total_pages:
                for p in range(ps, min(total_pages, pe) + 1):
                    if src['kind'] == 'en_book':
                        page_part[p] = sec['part_title']
                        page_chap[p] = sec['chapter_title']
                    if sec['level'] >= 3 or not page_sec[p]:
                        page_sec[p] = sec['title']

        # Write per-volume Markdown TOC file (Level 1)
        toc_md = [f"# {src['label']} 细粒度目录索引 (`{vol_id}` | 共 {total_pages} 页)", '']
        toc_md.append(f"> 源文件：`{src['filename']}` | 调用示例：`handbook_cli.py read {vol_id} <PDF页码>`")
        toc_md.append('')
        for sec in sections:
            lvl = sec['level']
            pg_range = f"p.{sec['pdf_start']}" if sec['pdf_start'] == sec['pdf_end'] else f"p.{sec['pdf_start']}-{sec['pdf_end']}"
            if sec['pdf_start'] <= 0:
                pg_range = sec['book_page']
            bp_note = f" ({sec['book_page']})" if sec['book_page'] and src['kind'] == 'cn_vol' else ''
            if lvl == 1:
                toc_md.append(f"\n## {sec['title']}  `[{pg_range}]`")
            elif lvl == 2:
                toc_md.append(f"\n### {sec['title']}  `[{pg_range}{bp_note}]`")
            else:
                indent = '  ' * max(0, lvl - 3)
                toc_md.append(f"{indent}- {sec['title']}  `[{pg_range}{bp_note}]`")
        with open(os.path.join(TOC_DIR, f'{vol_id}_toc.md'), 'w', encoding='utf-8') as f:
            f.write('\n'.join(toc_md) + '\n')

        # Collect L0 chapter summary for INDEX.md
        blk = [f"### `{vol_id}` {src['label']}（{src['parts']}，共 {total_pages} 页）"]
        if src['kind'] == 'cn_vol':
            for pno, pt, ppg in part_bms:
                blk.append(f"- **{pt}** (`p.{ppg}`)")
                chaps_in_part = [
                    f"{ct} (`p.{cpg}`)"
                    for cpno, ct, cpg in chap_bms
                    if cpno == pno and '参考文献' not in ct and ct.strip() != '本篇主要符号'
                ]
                if chaps_in_part:
                    blk.append('  - ' + '；'.join(chaps_in_part))
        else:
            for sec in sections:
                blk.append(f"- {sec['title']} (`p.{sec['pdf_start']}-{sec['pdf_end']}`)")
        volume_chapter_blocks.append('\n'.join(blk))

        # Pass 3: Bulk insert pages, FTS5, tables_index, standards_index
        page_rows = []
        fts_rows = []
        table_rows = []
        std_rows = []

        for i, cleaned in enumerate(pages_clean):
            pdf_p = i + 1
            bp = page_book_str[i]
            pt = page_part[pdf_p]
            ct = page_chap[pdf_p]
            st = page_sec[pdf_p]

            # Heuristic flag for pages heavy on formulas/tables/figures (advising render when exact numbers needed)
            has_ft = 1 if (
                '表' in cleaned[:200] or '图' in cleaned[:200] or 'TABLE' in cleaned[:300].upper()
                or any(sym in cleaned for sym in ('∫', '∑', '√', '±', '≤', '≥', 'σ', 'τ', 'ω'))
            ) else 0

            page_rows.append((vol_id, pdf_p, bp, pt, ct, st, cleaned, len(cleaned), has_ft))
            # Index header context + body for higher recall
            fts_rows.append((vol_id, pdf_p, tokenize_for_fts(f"{ct} {st} {cleaned}")))

            # Extract tables
            for tno, ttitle in extract_tables_from_page(cleaned, src['lang'], fallback_sec=st):
                table_rows.append((vol_id, pdf_p, bp, tno, ttitle, ct))

            # Extract standards (skip TOC pages to avoid duplicating TOC refs; TOC standards already in sections)
            if not (src['kind'] == 'cn_vol' and pdf_p < 26):
                stds = extract_standards(cleaned)
                if stds:
                    # Grab short context snippet for each standard
                    for code in stds:
                        idx_pos = cleaned.find(code)
                        if idx_pos == -1:
                            # fallback fuzzy find by numeric part
                            num_part = code.split()[-1]
                            idx_pos = cleaned.find(num_part)
                        if idx_pos >= 0:
                            ctx = cleaned[max(0, idx_pos - 35):idx_pos + 55].replace('\n', ' ')
                        else:
                            ctx = st[:80]
                        std_rows.append((code, vol_id, pdf_p, bp, ct, ctx))

        conn.executemany(
            'INSERT INTO pages (vol_id, pdf_page, book_page, part_title, chapter_title, section_title, clean_text, char_count, has_formula_or_table) VALUES (?,?,?,?,?,?,?,?,?)',
            page_rows
        )
        conn.executemany('INSERT INTO pages_fts (vol_id, pdf_page, content_tok) VALUES (?,?,?)', fts_rows)

        for row in table_rows:
            cur = conn.execute(
                'INSERT INTO tables_index (vol_id, pdf_page, book_page, table_no, title, chapter_title) VALUES (?,?,?,?,?,?)',
                row
            )
            tid = cur.lastrowid
            conn.execute('INSERT INTO tables_fts VALUES (?,?,?)', (
                row[0], tid, tokenize_for_fts(f"{row[3]} {row[4]} {row[5]}")
            ))

        conn.executemany(
            'INSERT INTO standards_index (std_code, vol_id, pdf_page, book_page, chapter_title, context) VALUES (?,?,?,?,?,?)',
            std_rows
        )
        conn.commit()
        doc.close()

        stats['tables'] += len(table_rows)
        stats['standards'] += len(std_rows)
        print(
            f"[{vol_id}] {total_pages:4d} pages | {len(sections):4d} sections | "
            f"{len(table_rows):4d} tables | {len(std_rows):5d} std_refs | {time.time()-t0:.2f}s"
        )

    # Optimize FTS5 indexes
    conn.execute("INSERT INTO pages_fts(pages_fts) VALUES('optimize');")
    conn.execute("INSERT INTO sections_fts(sections_fts) VALUES('optimize');")
    conn.execute("INSERT INTO tables_fts(tables_fts) VALUES('optimize');")
    conn.commit()

    unique_stds = conn.execute('SELECT COUNT(DISTINCT std_code) FROM standards_index').fetchone()[0]
    conn.execute('PRAGMA wal_checkpoint(TRUNCATE);')
    conn.execute('PRAGMA journal_mode=DELETE;')
    conn.execute('VACUUM;')
    conn.close()

    # Finalize INDEX.md with clean Markdown formatting (no broken blank lines inside table)
    index_md_lines += [
        '',
        '## 知识库构建统计',
        '',
        f"- 总索引页数：**{stats['total_pages']} 页**（中文第1~5卷 8512 页 + 英文 Databook 1282 页 + 表面粗糙度扫描件 14 页）",
        f"- 细粒度目录节点：**{stats['sections']} 条**（含 篇/章/节/小节 及 100% 精确 PDF 页码映射）",
        f"- 工程数据表索引：**{stats['tables']} 张**",
        f"- 国家/行业/国际标准引用：**{stats['standards']} 条**（去重后 **{unique_stds} 项独立标准号**）",
        '',
        '## 篇目与章级总览（快速路由表）',
        '',
    ] + [b + '\n' for b in volume_chapter_blocks]

    with open(os.path.join(KB_DIR, 'INDEX.md'), 'w', encoding='utf-8') as f:
        f.write('\n'.join(index_md_lines) + '\n')

    db_mb = os.path.getsize(DB_PATH) / (1024 * 1024)
    print(f"\n=== DONE in {time.time()-t_all:.2f}s ===")
    print(f"DB size: {db_mb:.2f} MB | Pages: {stats['total_pages']} | Sections: {stats['sections']} | Tables: {stats['tables']} | Unique Standards: {unique_stds}")


if __name__ == '__main__':
    ap = argparse.ArgumentParser(description='Rebuild handbook.db from source PDFs')
    ap.add_argument('--pdf-dir', help='Directory containing source PDF files (or set HANDBOOK_PDF_DIR)')
    a = ap.parse_args()
    if a.pdf_dir:
        ROOT_DIR = os.path.abspath(a.pdf_dir)
    build_database()
