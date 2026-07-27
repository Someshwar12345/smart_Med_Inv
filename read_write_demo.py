import os
import sys


def main():
    base = os.path.dirname(os.path.abspath(__file__))
    src = os.path.join(base, 'database', 'smart_medical_inventory.sql')
    out = os.path.join(base, 'database', 'sql_report.txt')

    if not os.path.exists(src):
        print(f"Source SQL file not found: {src}")
        sys.exit(1)

    try:
        with open(src, 'r', encoding='utf-8') as f:
            content = f.read()
    except Exception as e:
        print(f"Failed to read source file: {e}")
        sys.exit(1)

    lines = content.splitlines()
    num_lines = len(lines)
    num_create = sum(1 for l in lines if l.strip().upper().startswith('CREATE'))
    num_insert = sum(1 for l in lines if l.strip().upper().startswith('INSERT'))
    num_comments = sum(1 for l in lines if l.strip().startswith('--'))
    sample_lines = lines[:50]

    try:
        with open(out, 'w', encoding='utf-8') as f:
            f.write(f"SQL Report for: {os.path.basename(src)}\n")
            f.write(f"Total lines: {num_lines}\n")
            f.write(f"CREATE statements (lines starting with CREATE): {num_create}\n")
            f.write(f"INSERT statements (lines starting with INSERT): {num_insert}\n")
            f.write(f"Comment lines: {num_comments}\n\n")
            f.write("----- First 50 lines -----\n")
            f.write('\n'.join(sample_lines))
    except Exception as e:
        print(f"Failed to write report file: {e}")
        sys.exit(1)

    print(f"Wrote report to: {out}")


if __name__ == '__main__':
    main()
