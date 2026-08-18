"""Commit and synchronize named shared-checkout paths under one git lock."""

import argparse
from pathlib import Path
import subprocess
import sys

import constants, store
REPO_DIR = constants.REPO_DIR
PERSONAL_DIRS = {"memory", "plans", "agents"}
PRIVATE_DIR = ".private"


class RitualError(RuntimeError): pass


def _git(*args, repo=None):
    repo = Path(repo or REPO_DIR)
    private = Path(REPO_DIR) / PRIVATE_DIR
    if repo.resolve() == private.resolve():
        cmd = ["git", "--git-dir", str(private / ".git"),
               "--work-tree", str(REPO_DIR)] + list(args)
    else:
        cmd = ["git", "-C", str(repo)] + list(args)
    try:
        return subprocess.run(cmd, capture_output=True, text=True,
                              timeout=constants.GIT_CMD_TIMEOUT)
    except subprocess.TimeoutExpired:
        raise RitualError("git %s timed out after %ss" %
                          (" ".join(args), constants.GIT_CMD_TIMEOUT))


def _detail(proc):
    return " ".join((proc.stderr or proc.stdout or "failed").split())


def _need(proc, args):
    if proc.returncode:
        raise RitualError("git %s: %s" % (" ".join(args), _detail(proc)))
    return proc


def _paths(raw_paths):
    root = Path(REPO_DIR).resolve()
    paths = []
    for raw in raw_paths:
        path = Path(raw) if Path(raw).is_absolute() else root / raw
        try:
            paths.append(str(path.resolve().relative_to(root)))
        except ValueError:
            raise RitualError("path outside REPO_DIR: %s" % raw)
    return paths


def _repo_for(paths):
    root = Path(REPO_DIR).resolve()
    personal = []
    for path in paths:
        parts = Path(path).parts
        personal.append(bool(parts and parts[0] in PERSONAL_DIRS))
    if any(personal) and not all(personal):
        raise RitualError("paths span public and private repositories")
    if not any(personal):
        return root
    private = root / PRIVATE_DIR
    if (private / ".git").is_dir():
        proc = _need(_git("rev-parse", "--show-toplevel", repo=private),
                     ("rev-parse", "--show-toplevel"))
        if Path(proc.stdout.strip()).resolve() != root:
            raise RitualError("private repository work tree is not REPO_DIR")
        return private
    legacy = _git("ls-files", "--", "memory", "plans", repo=root)
    if legacy.returncode == 0 and legacy.stdout.strip():
        return root
    raise RitualError("private repository missing at .private; run the bootstrap in ADOPTING.md")


def _route(paths):
    repo = _repo_for(paths)
    return repo, paths


def _commit(message, paths):
    repo, paths = _route(paths)
    add = ["add"]
    if Path(paths[0]).parts[0] in PERSONAL_DIRS:
        add.append("-f")
    _need(_git(*add, "--", *paths, repo=repo), ("add",))
    _need(_git("commit", "-m", message, repo=repo), ("commit",))
    sha = _need(_git("rev-parse", "HEAD", repo=repo),
                ("rev-parse", "HEAD")).stdout.strip()
    if _git("push", repo=repo).returncode == 0:
        return "pushed %s" % sha
    _need(_git("fetch", "origin", repo=repo), ("fetch", "origin"))
    upstream = _git("rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}",
                    repo=repo)
    target = upstream.stdout.strip() if upstream.returncode == 0 else "origin/main"
    rebase = _git("-c", "rebase.autoStash=false", "rebase", target, repo=repo)
    if rebase.returncode:
        if "unstaged changes" in (rebase.stderr or ""):
            raise RitualError("committed %s, unpushed, dirty tree blocked rebase" % sha)
        abort = _git("rebase", "--abort", repo=repo)
        if abort.returncode:
            raise RitualError("committed %s, unpushed; rebase and abort failed: %s" %
                              (sha, _detail(abort)))
        raise RitualError("committed %s, unpushed, remote moved" % sha)
    if _git("push", repo=repo).returncode:
        raise RitualError("committed %s, unpushed, remote moved" % sha)
    return "pushed %s" % _need(_git("rev-parse", "HEAD", repo=repo),
                                ("rev-parse", "HEAD")).stdout.strip()


def _pull():
    proc = _git("pull", "--ff-only")
    if proc.returncode: raise RitualError("pull refused: %s" % _detail(proc))
    return "pull complete"


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--pull", action="store_true")
    ap.add_argument("-m", dest="message")
    ap.add_argument("paths", nargs="*")
    args = ap.parse_args(argv)
    if args.selftest:
        return _selftest()
    if args.pull and (args.message is not None or args.paths):
        sys.stderr.write("--pull takes no -m or paths\n")
        return 2
    if not args.pull and args.message is None:
        sys.stderr.write("-m is required\n")
        return 2
    if not args.pull and not args.message.strip():
        sys.stderr.write("-m must be non-empty\n")
        return 2
    if not args.pull and not args.paths:
        sys.stderr.write("at least one path is required\n")
        return 2
    try:
        paths = [] if args.pull else _paths(args.paths)
    except RitualError as exc:
        sys.stderr.write(str(exc) + "\n")
        return 2
    try:
        with store._locked("git", timeout=constants.GIT_LOCK_WAIT):
            result = _pull() if args.pull else _commit(args.message, paths)
        print(result)
        return 0
    except TimeoutError:
        sys.stderr.write("git lock unavailable after %ss\n" % constants.GIT_LOCK_WAIT)
    except RitualError as exc:
        sys.stderr.write(str(exc) + "\n")
    return 1


def _selftest():
    from tests import commit_selftest
    return commit_selftest.run(sys.modules[__name__])

if __name__ == "__main__":
    sys.exit(main())
