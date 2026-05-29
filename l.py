import re
import sys
import ast
import io
import tokenize
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


def _docstring_positions_from_ast(text: str):
    positions = set()
    tree = ast.parse(text)

    def add_if_docstring(body):
        if not body:
            return
        first = body[0]
        if isinstance(first, ast.Expr) and isinstance(getattr(first, "value", None), ast.Constant):
            if isinstance(first.value.value, str):
                positions.add((first.lineno, first.col_offset, first.end_lineno, first.end_col_offset))

    add_if_docstring(tree.body)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            add_if_docstring(node.body)

    return positions


def _lower_string_literal_content(token_text: str) -> str:
    i = 0
    while i < len(token_text) and token_text[i] in "rRuUbBfF":
        i += 1
    prefix = token_text[:i]
    rest = token_text[i:]

    quote = ""
    if rest.startswith('"""') or rest.startswith("'''"):
        quote = rest[:3]
    elif rest.startswith('"') or rest.startswith("'"):
        quote = rest[:1]
    else:
        return token_text

    if not rest.endswith(quote):
        return token_text

    content = rest[len(quote) : -len(quote)]
    return f"{prefix}{quote}{content.lower()}{quote}"


def lower_python_comments_and_docstrings(text: str) -> str:
    doc_pos = _docstring_positions_from_ast(text)
    out_tokens = []

    for tok in tokenize.generate_tokens(io.StringIO(text).readline):
        tok_type, tok_str, start, end, line = tok

        if tok_type == tokenize.COMMENT:
            if tok_str.startswith("#!"):
                out_tokens.append(tok)
            else:
                out_tokens.append((tok_type, tok_str.lower(), start, end, line))
            continue

        if tok_type == tokenize.STRING:
            key = (start[0], start[1], end[0], end[1])
            if key in doc_pos:
                lowered = _lower_string_literal_content(tok_str)
                out_tokens.append((tok_type, lowered, start, end, line))
            else:
                out_tokens.append(tok)
            continue

        out_tokens.append(tok)

    return tokenize.untokenize(out_tokens)

def should_skip(path: Path) -> bool:
    if any(part in SKIP_DIRS for part in path.parts):
        return True
    if any(str(path).endswith(suffix) for suffix in SKIP_NAMES):
        return True
    return False

def debug_changes(filename, text_before, text_after):
    print(f"\nChecking {filename}")
    count = 0
    for i, (a, b) in enumerate(zip(text_before.splitlines(), text_after.splitlines()), start=1):
        if a != b:
            print(f"  L{i:>4}: {a.strip()}  ->  {b.strip()}")
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
    if f.suffix == '.py':
        text = lower_python_comments_and_docstrings(text)
    else:
        text = PAT_HTML.sub(lower_html, text)
        text = PAT_BLOCK.sub(lower_block, text)
    if f.suffix in ('.js', '.ts', '.jsx', '.tsx'):
        text = PAT_SLASH.sub(lower_slash, text)
    if f.suffix in ('.sh', '.rb'):
        text = PAT_HASH.sub(lower_hash, text)

    if text != orig:
        debug_changes(f, orig, text)
        if not DRY_RUN:
            f.write_text(text, encoding='utf-8')
            print(f"Saved {f}")
        else:
            print(f"Dry-run: would modify {f}")
    else:
        print(f"No changes in {f}")

# run
if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python l.py <file_path>")
        sys.exit(1)

    target = Path(sys.argv[1])
    if not target.exists() or not target.is_file():
        print(f"File not found: {target}")
        sys.exit(1)

    process_file(target)

    if DRY_RUN:
        print("\nDry-run mode ON — set DRY_RUN = False to actually save changes.")
