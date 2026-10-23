"""Install each artifact in a fresh environment and run the public API example."""

import argparse
import json
import os
import subprocess
import tarfile
import tempfile
import venv
import zipfile
from pathlib import Path


def check(dist: Path) -> None:
    repo = Path(__file__).resolve().parents[1]
    artifacts = sorted([*dist.glob('*.whl'), *dist.glob('*.tar.gz')])
    if not any(p.suffix == '.whl' for p in artifacts) or not any(p.name.endswith('.tar.gz') for p in artifacts):
        raise SystemExit('Build a wheel and sdist before checking distributions')
    for artifact in artifacts:
        with zipfile.ZipFile(artifact) if artifact.suffix == '.whl' else tarfile.open(artifact) as package:
            names = package.namelist() if artifact.suffix == '.whl' else package.getnames()
            assert not any('/.env' in n or '/.firecrawl/' in n or '/reports/' in n
                           or '/source-snapshots/' in n or n.endswith('.db') for n in names), names
            assert any(n.endswith('/LICENSE') for n in names)
            assert any(n.endswith('prism_crawler/py.typed') for n in names)
            assert any(n.endswith('optimal_crawler/py.typed') for n in names)
        with tempfile.TemporaryDirectory(prefix='prism-dist-') as directory:
            root = Path(directory)
            venv.EnvBuilder(with_pip=True).create(root/'env')
            python = root/'env'/'bin'/'python'
            env = {k: v for k, v in os.environ.items() if k not in {'PYTHONPATH', 'PYTHONHOME'}}
            subprocess.run([str(python), '-m', 'pip', 'install', '--quiet', str(artifact.resolve())],
                           cwd=root, env=env, check=True)
            probe = 'import importlib.metadata as m, pathlib, prism_crawler; ' \
                    'assert pathlib.Path(prism_crawler.__file__).is_relative_to(pathlib.Path(__import__("sys").prefix)); ' \
                    'assert m.version("prism-crawler")==prism_crawler.__version__; ' \
                    'assert m.metadata("prism-crawler")["License-Expression"]=="MIT"; ' \
                    'print(prism_crawler.__file__)'
            subprocess.run([str(python), '-I', '-c', probe], cwd=root, env=env, check=True)
            subprocess.run([str(python), '-I', '-m', 'prism_crawler', '--help'], cwd=root,
                           env=env, check=True, stdout=subprocess.DEVNULL)
            subprocess.run([str(root/'env'/'bin'/'prism'), 'crawl', '--help'], cwd=root,
                           env=env, check=True, stdout=subprocess.DEVNULL)
            subprocess.run([str(python), '-I', str(repo/'examples'/'local_crawl.py'),
                            '--output', str(root/'result.jsonl')], cwd=root, env=env, check=True)
            documents = [json.loads(line) for line in (root/'result.jsonl').read_text().splitlines()]
            assert len(documents) == 3
        print(f'Installed import, CLI and offline HTTP/browser/robots example passed: {artifact.name}', flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dist', type=Path, required=True)
    check(parser.parse_args().dist)
