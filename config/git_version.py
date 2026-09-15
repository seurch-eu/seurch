"""Local-dev fallback for GIT_REF/GIT_SHA (see settings.py)."""

import subprocess


def _git(base_dir, *args):
    try:
        return subprocess.run(
            ['git', *args],
            cwd=base_dir,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError, OSError):
        return ''


def get_git_ref(base_dir):
    return (
        _git(base_dir, 'symbolic-ref', '--short', '-q', 'HEAD')
        or _git(base_dir, 'describe', '--tags', '--exact-match')
    )


def get_git_sha(base_dir):
    return _git(base_dir, 'rev-parse', '--short', 'HEAD')
