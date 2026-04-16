"""
Populate card_rulings from official Bandai Q&A PDFs.

Downloads PDFs from en.onepiece-cardgame.com, extracts Q&A tables
using pdfplumber, validates card codes, and inserts into card_rulings.
One transaction per PDF file; rollback on any failure within a batch.
"""

import os
import re
import sqlite3
import sys
import urllib.request
from datetime import datetime

import pdfplumber

DB_PATH = r'D:\dev\tcg-watcher-worktree\data\card_catalog.db'
PDF_DIR = r'D:\dev\tcg-watcher-worktree\tools\ruling_pdfs'
BASE_URL = 'https://en.onepiece-cardgame.com/pdf'

CARD_CODE_RE = re.compile(
    r'^(?:OP\d{2}-\d{3}|EB\d{2}-\d{3}|ST\d{2}-\d{3}|PRB\d{2}-\d{3}|P-\d{3})$'
)

CARD_SPECIFIC_PDFS = [
    'qa_op01', 'qa_op02', 'qa_op03', 'qa_op04', 'qa_op05',
    'qa_op06', 'qa_op07', 'qa_op08', 'qa_op09', 'qa_op10',
    'qa_op11', 'qa_op12', 'qa_op13',
    'qa_eb01', 'qa_eb02', 'qa_eb03',
]

PROBE_PDFS = [
    'qa_op14', 'qa_op15', 'qa_eb04',
    'qa_st01-st04', 'qa_st05-st09', 'qa_st10-st14', 'qa_st15-st22',
]

SET_RELEASE_DATES = {
    'qa_op01': '2022-12-02', 'qa_op02': '2023-03-10', 'qa_op03': '2023-06-30',
    'qa_op04': '2023-10-13', 'qa_op05': '2024-02-09', 'qa_op06': '2024-05-24',
    'qa_op07': '2024-09-27', 'qa_op08': '2024-11-22', 'qa_op09': '2025-02-28',
    'qa_op10': '2025-05-30', 'qa_op11': '2025-05-30', 'qa_op12': '2025-08-29',
    'qa_op13': '2025-10-24',
    'qa_eb01': '2024-02-09', 'qa_eb02': '2024-09-27', 'qa_eb03': '2025-10-24',
}


def classify_ruling(question: str, answer: str) -> str:
    q_lower = question.lower()
    a_lower = answer.lower()
    combined = q_lower + ' ' + a_lower

    if any(w in combined for w in ['trigger', '[trigger]']):
        if 'timing' in combined or 'when' in q_lower[:30] or 'activate' in q_lower:
            return 'trigger'
        return 'trigger'

    if any(w in q_lower for w in ['at what time', 'when does', 'when can', 'timing',
                                   'before or after', 'during which step',
                                   'at the same time', 'simultaneously']):
        return 'timing'

    if any(w in q_lower for w in ['don!!', 'don! !', 'cost area', 'rest the specified',
                                   'don!! card', 'don!! -', 'don!! x']):
        if 'cost' in q_lower or 'pay' in q_lower or 'rest' in q_lower:
            return 'cost'

    if any(w in combined for w in ['instead', 'would be', 'replacement',
                                    'cannot be k.o.', 'would be removed']):
        if 'instead' in combined:
            return 'replacement_effect'

    if any(w in q_lower for w in ['target', 'choose', 'select', 'can i choose',
                                   'which character', 'valid target']):
        return 'targeting'

    second_code = re.search(
        r'(?:OP|EB|ST|PRB)\d{2}-\d{3}', question
    )
    if second_code:
        return 'interaction'

    if any(w in q_lower for w in ['opponent', 'other card', 'together with',
                                   'at the same time as', 'both']):
        if any(w in q_lower for w in ['effect', 'activate', 'play', 'attack']):
            return 'interaction'

    return 'general'


def extract_related_code(question: str, primary_code: str) -> str | None:
    codes = re.findall(r'(?:OP|EB|ST|PRB)\d{2}-\d{3}', question)
    for c in codes:
        if c != primary_code:
            return c
    return None


def parse_pdf_date(metadata: dict) -> str | None:
    for key in ('ModDate', 'CreationDate'):
        raw = metadata.get(key, '')
        if not raw:
            continue
        match = re.match(r"D:(\d{4})(\d{2})(\d{2})", raw)
        if match:
            return f'{match.group(1)}-{match.group(2)}-{match.group(3)}'
    return None


def download_pdf(name: str) -> tuple[str | None, str]:
    url = f'{BASE_URL}/{name}.pdf'
    local_path = os.path.join(PDF_DIR, f'{name}.pdf')

    if os.path.exists(local_path) and os.path.getsize(local_path) > 1000:
        return local_path, 'CACHED'

    try:
        urllib.request.urlretrieve(url, local_path)
        return local_path, 'FETCHED'
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None, 'NOT_FOUND'
        return None, f'HTTP_{e.code}'
    except Exception as e:
        return None, f'ERROR: {e}'


def extract_rulings_from_pdf(local_path: str, pdf_name: str,
                              valid_codes: set) -> tuple[list, list, str]:
    rulings = []
    skipped_codes = []
    method = 'pdfplumber_table'
    url = f'{BASE_URL}/{pdf_name}.pdf'

    pdf = pdfplumber.open(local_path)
    pdf_date = parse_pdf_date(pdf.metadata or {})
    fallback_date = SET_RELEASE_DATES.get(pdf_name, '')

    ruling_date = pdf_date or fallback_date
    date_note = ''
    if not pdf_date and fallback_date:
        date_note = 'date estimated from set release'

    for page_idx, page in enumerate(pdf.pages):
        tables = page.extract_tables()
        if not tables:
            continue

        for table in tables:
            for row in table:
                if not row or len(row) < 4:
                    continue

                card_no = (row[0] or '').strip()
                question = (row[2] or '').strip()
                answer = (row[3] or '').strip()

                if not card_no or card_no == 'Card No.' or not question or not answer:
                    continue

                card_no = card_no.replace('\n', '').strip()

                if not CARD_CODE_RE.match(card_no):
                    continue

                if card_no not in valid_codes:
                    skipped_codes.append(card_no)
                    continue

                question_clean = question.replace('\n', ' ').replace('<br>', ' ').strip()
                answer_clean = answer.replace('\n', ' ').replace('<br>', ' ').strip()
                question_clean = re.sub(r'\s+', ' ', question_clean)
                answer_clean = re.sub(r'\s+', ' ', answer_clean)

                ruling_type = classify_ruling(question_clean, answer_clean)
                related = extract_related_code(question_clean, card_no)

                if related and related not in valid_codes:
                    related = None

                ruling_text = f'Q: {question_clean} A: {answer_clean}'

                notes_parts = []
                if date_note:
                    notes_parts.append(date_note)
                notes = '; '.join(notes_parts)

                rulings.append((
                    card_no, ruling_text, ruling_date, 'EN',
                    'bandai_official', url, related, ruling_type, notes,
                ))

    pdf.close()
    return rulings, skipped_codes, method


def run():
    sys.stdout.reconfigure(encoding='utf-8')
    os.makedirs(PDF_DIR, exist_ok=True)

    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()

    cur.execute("SELECT COUNT(*) FROM card_rulings")
    existing = cur.fetchone()[0]
    if existing > 0:
        print(f"STOP: card_rulings already has {existing} rows.")
        con.close()
        sys.exit(1)

    cur.execute("SELECT source_key, ethics_status FROM data_sources "
                "WHERE source_key = 'bandai_official'")
    row = cur.fetchone()
    if not row or row[1] != 'approved':
        print("STOP: bandai_official not approved.")
        con.close()
        sys.exit(1)

    cur.execute("SELECT canonical_code FROM cards")
    valid_codes = {r[0] for r in cur.fetchall()}
    print(f"Loaded {len(valid_codes)} canonical card codes.")

    pdf_results = {}
    total_inserted = 0
    total_parsed = 0
    all_skipped = []

    all_pdfs = CARD_SPECIFIC_PDFS + PROBE_PDFS
    for pdf_name in all_pdfs:
        local_path, status = download_pdf(pdf_name)
        if local_path is None:
            pdf_results[pdf_name] = {'status': status, 'method': 'N/A',
                                      'parsed': 0, 'inserted': 0}
            print(f"  {pdf_name}: {status}")
            continue

        pdf_results[pdf_name] = {'status': status}
        rulings, skipped, method = extract_rulings_from_pdf(
            local_path, pdf_name, valid_codes
        )
        pdf_results[pdf_name]['method'] = method
        pdf_results[pdf_name]['parsed'] = len(rulings)
        all_skipped.extend([(pdf_name, c) for c in skipped])

        if not rulings:
            pdf_results[pdf_name]['inserted'] = 0
            print(f"  {pdf_name}: {status}, {method}, 0 rulings found")
            continue

        try:
            cur.execute("BEGIN")
            ins = 0
            for ruling in rulings:
                cur.execute("""
                    INSERT INTO card_rulings
                        (card_code, ruling_text, ruling_date, region,
                         source_key, source_url, related_card_code,
                         ruling_type, notes)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, ruling)
                ins += 1
            cur.execute("COMMIT")
            pdf_results[pdf_name]['inserted'] = ins
            total_inserted += ins
            total_parsed += len(rulings)
            print(f"  {pdf_name}: {status}, {method}, "
                  f"{len(rulings)} parsed, {ins} inserted")
        except Exception as e:
            cur.execute("ROLLBACK")
            pdf_results[pdf_name]['inserted'] = 0
            pdf_results[pdf_name]['error'] = str(e)
            print(f"  {pdf_name}: ROLLBACK - {e}")

    # === VERIFICATION ===
    print(f"\n{'='*60}")
    print("VERIFICATION")
    print(f"{'='*60}")

    cur.execute("SELECT COUNT(*) FROM card_rulings")
    total = cur.fetchone()[0]
    print(f"\nTotal rows in card_rulings: {total}")

    cur.execute("""
        SELECT ruling_type, COUNT(*) as count
        FROM card_rulings GROUP BY ruling_type ORDER BY count DESC
    """)
    print("\nBy ruling_type:")
    for row in cur.fetchall():
        print(f"  {row[0]}: {row[1]}")

    cur.execute("""
        SELECT source_url, COUNT(*) as count
        FROM card_rulings GROUP BY source_url ORDER BY count DESC
    """)
    print("\nBy source PDF:")
    for row in cur.fetchall():
        short = row[0].split('/')[-1] if row[0] else '?'
        print(f"  {short}: {row[1]}")

    cur.execute("""
        SELECT cr.card_code, cr.ruling_type
        FROM card_rulings cr
        LEFT JOIN cards c ON cr.card_code = c.canonical_code
        WHERE c.canonical_code IS NULL
    """)
    orphans = cur.fetchall()
    print(f"\nOrphan card_codes (not in cards): {len(orphans)}")
    for o in orphans[:5]:
        print(f"  {o[0]} ({o[1]})")

    cur.execute("SELECT card_code, ruling_type, ruling_date, ruling_text FROM card_rulings LIMIT 5")
    print("\nSample rows:")
    for row in cur.fetchall():
        print(f"  {row[0]} [{row[1]}] ({row[2]}): {row[3][:100]}...")

    con.close()

    # === REPORT ===
    print(f"\n{'='*60}")
    print("PDF STATUS REPORT")
    print(f"{'='*60}")
    for name in all_pdfs:
        r = pdf_results.get(name, {})
        status = r.get('status', '?')
        method = r.get('method', '?')
        parsed = r.get('parsed', 0)
        inserted = r.get('inserted', 0)
        error = r.get('error', '')
        line = f"  {name}: {status} | {method} | {parsed} parsed | {inserted} inserted"
        if error:
            line += f" | ERROR: {error}"
        print(line)

    if all_skipped:
        unique_skipped = sorted(set(c for _, c in all_skipped))
        print(f"\nSkipped card codes ({len(unique_skipped)} unique):")
        for c in unique_skipped[:20]:
            print(f"  {c}")

    print(f"\n=== SUMMARY ===")
    print(f"  Total parsed: {total_parsed}")
    print(f"  Total inserted: {total_inserted}")
    print(f"  Total skipped codes: {len(set(c for _, c in all_skipped))}")
    print(f"  card_rulings DEFERRED: qa_rules.pdf (general rules, no card codes)")


if __name__ == '__main__':
    run()
