import re
from pathlib import Path

# tool to lowercase all comments in source file, with dry-run mode to avoid accidental changes.

# file extensions to scan
EXTS = {'.html', '.css', '.js', '.py', '.sh', '.rb'}

# directories and file patterns to skip
SKIP_DIRS = {'node_modules', 'dist', 'build', '.git', '.venv', 'venv'}
SKIP_NAMES = {'.min.js', '.min.css'}

# dry-run mode (true = only print what would change)
DRY_RUN = False


# regex patterns (safe)
PAT_HTML = re.compile(r'<!--(.*?)-->', re.DOTALL)
PAT_BLOCK = re.compile(r'/\*(.*?)\*/', re.DOTALL)
# match // comments, but ignore urls like http:// or https://
PAT_SLASH = re.compile(r'(^|[^:])//(?![a-zA-Z0-9])(.*)$', re.MULTILINE)
PAT_HASH  = re.compile(r'^[ \t]*#(?![0-9A-Fa-f]{3,6})(.*)$', re.MULTILINE)

def lower_html(m):  return '<!--' + m.group(1).lower() + '-->'
def lower_block(m): return '/*'   + m.group(1).lower() + '*/'
def lower_slash(m): return re.sub(r'//(?![a-zA-Z0-9]).*', lambda x: x.group(0).lower(), m.group(0))
def lower_hash(m):  return re.sub(r'#(?![0-9A-Fa-f]{3,6}).*', lambda x: x.group(0).lower(), m.group(0))

def should_skip(path: Path) -> bool:
    if any(part in SKIP_DIRS for part in path.parts):
        return True
    if any(str(path).endswith(suffix) for suffix in SKIP_NAMES):
        return True
    return False

def debug_changes(filename, text_before, text_after):
    print(f"\n🔍 Checking {filename}")
    count = 0
    for i, (a, b) in enumerate(zip(text_before.splitlines(), text_after.splitlines()), start=1):
        if a != b:
            print(f"  L{i:>4}: {a.strip()}  →  {b.strip()}")
            count += 1
            if count > 5:
                print("  ...more changes hidden...")
                break
    if count == 0:
        print("  No visible comment changes.")
    return count

def process_file(f: Path):
    if should_skip(f) or f.suffix.lower() not in EXTS or not f.is_file():
        return

    text = f.read_text(encoding='utf-8')
    orig = text

    # apply transformations based on file type
    text = PAT_HTML.sub(lower_html, text)
    text = PAT_BLOCK.sub(lower_block, text)
    if f.suffix in ('.js', '.ts', '.jsx', '.tsx'):
        text = PAT_SLASH.sub(lower_slash, text)
    if f.suffix in ('.py', '.sh', '.rb'):
        text = PAT_HASH.sub(lower_hash, text)

    if text != orig:
        debug_changes(f, orig, text)
        if not DRY_RUN:
            f.write_text(text, encoding='utf-8')
            print(f"✔ Saved {f}")
        else:
            print(f"💡 Dry-run: would modify {f}")
    else:
        print(f"✔ No changes in {f}")

# run
if __name__ == "__main__":
    for f in Path('.').rglob('*'):
        process_file(f)

    if DRY_RUN:
        print("\n💭 Dry-run mode ON — set DRY_RUN = False to actually save changes.")
