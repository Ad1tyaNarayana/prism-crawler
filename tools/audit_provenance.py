"""Compare local Python source with pinned upstream checkouts without executing them.

This finds exact-file, normalized ten-line and structurally identical Python
function matches. It is a scoped similarity check, not proof of originality.
"""

import argparse
import ast
import hashlib
import json
import re
from collections import defaultdict
from pathlib import Path


class Structure(ast.NodeTransformer):
    def visit_Name(self, node):
        return ast.copy_location(ast.Name(id='_', ctx=node.ctx), node)

    def visit_Constant(self, node):
        return ast.copy_location(ast.Constant(value=type(node.value).__name__), node)

    def visit_arg(self, node):
        node.arg = '_'
        return self.generic_visit(node)

    def visit_Attribute(self, node):
        node.attr = '_'
        return self.generic_visit(node)

    def visit_FunctionDef(self, node):
        node.name = '_'
        return self.generic_visit(node)

    visit_AsyncFunctionDef = visit_FunctionDef


def fingerprints(path):
    text = path.read_text(encoding='utf-8', errors='replace')
    lines = [re.sub(r'\s+', '', line) for line in text.splitlines()
             if line.strip() and not line.lstrip().startswith(('#', '//'))]
    windows = {hashlib.sha256(''.join(lines[i:i+10]).encode()).hexdigest()
               for i in range(max(0, len(lines)-9)) if len(''.join(lines[i:i+10])) >= 200}
    functions = []
    if path.suffix == '.py':
        try:
            tree = ast.parse(text)
        except SyntaxError:
            return windows, functions
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                count = sum(1 for _ in ast.walk(node))
                if count < 60:
                    continue
                name, line = node.name, node.lineno
                normalized = Structure().visit(ast.parse(ast.unparse(node)))
                digest = hashlib.sha256(ast.dump(normalized).encode()).hexdigest()
                functions.append((digest, name, line, count))
    return windows, functions


def audit(manifest, output, reference_root, local_roots):
    upstreams = json.loads(manifest.read_text())
    exact, windows, shapes = defaultdict(list), defaultdict(list), defaultdict(list)
    counts = {}
    for reference in upstreams:
        root = reference_root/reference['name']
        files = [p for p in root.rglob('*') if p.suffix in {'.py', '.ts', '.js'}
                 and '.git' not in p.parts and p.is_file()]
        counts[reference['name']] = len(files)
        for path in files:
            label = reference['name']+'/'+str(path.relative_to(root))
            data = path.read_bytes()
            if len(data) < 200:
                continue
            exact[hashlib.sha256(data).hexdigest()].append(label)
            chunks, functions = fingerprints(path)
            for digest in chunks:
                if len(windows[digest]) < 3:
                    windows[digest].append(label)
            for digest, name, line, _ in functions:
                shapes[digest].append({'path': label, 'function': name, 'line': line})
    matches = {'exact_files': [], 'normalized_line_windows': [], 'function_shapes': []}
    local = sorted({p for root in local_roots for p in
                    ([root] if root.is_file() else root.rglob('*.py'))})
    for path in local:
        data = path.read_bytes()
        if len(data) < 200:
            continue
        file_matches = exact.get(hashlib.sha256(data).hexdigest(), [])
        if file_matches:
            matches['exact_files'].append({'local': str(path), 'upstream': file_matches})
        chunks, functions = fingerprints(path)
        overlap = sorted({file for digest in chunks for file in windows.get(digest, [])})
        if overlap:
            matches['normalized_line_windows'].append({'local': str(path), 'upstream': overlap})
        for digest, name, line, count in functions:
            if digest in shapes:
                matches['function_shapes'].append({'local': str(path), 'function': name,
                    'line': line, 'nodes': count, 'upstream': shapes[digest]})
    result = {'scope': 'Selected local Python roots against pinned upstream Python/JS/TS checkouts. '
              'Only selected source/test directories were checked out. No upstream code executed.',
              'limitations': 'Not an exhaustive internet search, license certification, or proof of '
              'independent authorship. Common algorithms, unexamined versions and transformed code '
              'can evade matching. Structural matches require human review.',
              'upstreams': upstreams, 'upstream_source_files': counts,
              'local_source_files': len(local), 'local_roots': [str(p) for p in local_roots],
              'local_sha256': [{'path': str(p), 'sha256': hashlib.sha256(p.read_bytes()).hexdigest()}
                               for p in local], 'matches': matches}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2))
    print(json.dumps({k: len(v) for k, v in matches.items()}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--upstreams', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--reference-root', type=Path, required=True)
    parser.add_argument('--local-root', type=Path, action='append')
    args = parser.parse_args()
    audit(args.upstreams, args.output, args.reference_root, args.local_root or [Path('src')])
