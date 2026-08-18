"""Offline git and lock fixtures for commit.py."""

import argparse
import contextlib
import io
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import constants
import store
import tests.cases as cases


def run(commit_mod):
    passed = failed = 0
    results = {}
    old_repo = commit_mod.REPO_DIR
    old_state, old_wait = store.STATE_DIR, constants.GIT_LOCK_WAIT

    def check(ok, label, detail=""):
        nonlocal passed, failed
        if ok:
            passed += 1
        else:
            failed += 1
            print("FAIL %s%s" % (label, (": " + detail) if detail else ""))

    def raw(cwd, *args):
        return subprocess.run(["git", "-C", str(cwd)] + list(args), capture_output=True,
                              text=True, timeout=constants.GIT_CMD_TIMEOUT, check=True)

    def overlay(git_dir, worktree, *args):
        return subprocess.run(["git", "--git-dir", str(git_dir), "--work-tree",
                               str(worktree)] + list(args), capture_output=True, text=True,
                              timeout=constants.GIT_CMD_TIMEOUT, check=True)

    def invoke(argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = commit_mod.main(argv)
        return code, out.getvalue(), err.getvalue()

    def write(repo, name, body):
        (repo / name).write_text(body)

    def clean_stash(repo):
        return not raw(repo, "stash", "list").stdout.strip()

    def holder(state, marker):
        code = ("import pathlib,store,time; store.STATE_DIR=pathlib.Path(%r); "
                "c=store._locked('git'); c.__enter__(); pathlib.Path(%r).write_text('held'); "
                "time.sleep(30)" % (str(state), str(marker)))
        return subprocess.Popen([sys.executable, "-c", code], env=dict(
            os.environ, PYTHONPATH=str(Path(__file__).parents[1])), stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL)

    child = ("import pathlib,sys,commit,store; commit.REPO_DIR=pathlib.Path(sys.argv[1]); "
             "store.STATE_DIR=pathlib.Path(sys.argv[2]); sys.exit(commit.main(sys.argv[3:]))")
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).parents[1]))

    try:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            remote, repo, state, marker = root / "remote.git", root / "repo", root / "state", root / "held"
            raw(root, "init", "--bare", str(remote)); raw(root, "init", str(repo))
            raw(repo, "config", "user.email", "selftest@example.com")
            raw(repo, "config", "user.name", "Selftest")
            (repo / "memory").mkdir(); (repo / "plans").mkdir()
            write(repo, ".gitattributes",
                  (Path(__file__).resolve().parents[2] / ".gitattributes").read_text())
            write(repo, "base.txt", "base\n")
            write(repo, "memory/concurrent.md", "base\n")
            write(repo, "memory/same-line.md", "original\n")
            write(repo, "memory/deletion.md", "first\nremove\nlast\n")
            write(repo, "plans/base.md", "base\n")
            raw(repo, "add", "--", ".gitattributes", "base.txt", "memory", "plans")
            raw(repo, "commit", "-m", "base"); raw(repo, "branch", "-M", "main")
            raw(repo, "remote", "add", "origin", str(remote)); raw(repo, "push", "-u", "origin", "main")
            raw(remote, "symbolic-ref", "HEAD", "refs/heads/main")
            commit_mod.REPO_DIR, store.STATE_DIR = repo, state

            for argv, needle in cases.COMMIT_REFUSALS:
                code, _, err = invoke(argv)
                check(code == 2 and needle in err and clean_stash(repo),
                      "refusal %r" % argv, err.strip())

            write(repo, "public.txt", "public\n")
            public_head = raw(repo, "rev-parse", "HEAD").stdout.strip()
            code, _, err = invoke(["-m", "public", "public.txt"])
            results["public path names the PR route"] = (
                code == 1 and "worktree PR" in err and clean_stash(repo)
                and public_head == raw(repo, "rev-parse", "HEAD").stdout.strip())

            write(repo, "plans/clean.md", "clean\n")
            code, out, _ = invoke(["-m", "clean", "plans/clean.md"])
            results["clean push"] = code == 0 and "pushed " in out and clean_stash(repo)

            other = root / "other"; raw(root, "clone", str(remote), str(other))
            raw(other, "config", "user.email", "other@example.com"); raw(other, "config", "user.name", "Other")
            write(other, "remote.txt", "remote\n"); raw(other, "add", "--", "remote.txt")
            raw(other, "commit", "-m", "remote"); raw(other, "push")
            write(repo, "plans/local.md", "local\n"); code, _, _ = invoke(["-m", "local", "plans/local.md"])
            results["rejected push rebases and pushes"] = code == 0 and (repo / "remote.txt").is_file() and clean_stash(repo)

            raw(other, "pull", "--ff-only"); write(other, "plans/conflict.md", "remote\n")
            raw(other, "add", "--", "plans/conflict.md")
            raw(other, "commit", "-m", "remote conflict"); raw(other, "push")
            write(repo, "plans/conflict.md", "local\n")
            code, _, err = invoke(["-m", "local conflict", "plans/conflict.md"])
            sha = raw(repo, "rev-parse", "HEAD").stdout.strip()
            results["rebase conflict aborts and names sha"] = code == 1 and sha in err and not (repo / ".git/rebase-merge").exists() and clean_stash(repo)

            dirty = root / "dirty"; raw(root, "clone", str(remote), str(dirty))
            raw(dirty, "config", "user.email", "dirty@example.com"); raw(dirty, "config", "user.name", "Dirty")
            write(dirty, "base.txt", "dirty\n"); write(other, "dirty-remote.txt", "remote\n")
            raw(other, "add", "--", "dirty-remote.txt"); raw(other, "commit", "-m", "move remote"); raw(other, "push")
            write(dirty, "plans/dirty-local.md", "local\n"); commit_mod.REPO_DIR = dirty
            code, _, err = invoke(["-m", "dirty local", "plans/dirty-local.md"])
            sha = raw(dirty, "rev-parse", "HEAD").stdout.strip()
            results["dirty tree refusal skips abort"] = (
                code == 1 and err == "committed %s, unpushed, dirty tree blocked rebase\n" % sha
                and (dirty / "base.txt").read_text() == "dirty\n" and clean_stash(dirty))

            proc = holder(state, marker)
            for _ in range(100):
                if marker.exists():
                    break
                time.sleep(0.01)
            constants.GIT_LOCK_WAIT = 0.05
            code, _, err = invoke(["--pull"])
            results["lock wait is bounded"] = code == 1 and "lock unavailable" in err and clean_stash(repo)
            proc.kill(); proc.wait(timeout=2)
            with store._locked("git", timeout=0.5):
                results["killed holder frees lock"] = clean_stash(repo)
            constants.GIT_LOCK_WAIT = old_wait

            pullrepo = root / "pull"; raw(root, "clone", str(remote), str(pullrepo))
            raw(pullrepo, "config", "user.email", "pull@example.com"); raw(pullrepo, "config", "user.name", "Pull")
            raw(other, "pull", "--ff-only"); write(other, "ff.txt", "ff\n")
            raw(other, "add", "--", "ff.txt"); raw(other, "commit", "-m", "ff"); raw(other, "push")
            commit_mod.REPO_DIR = pullrepo; before = raw(pullrepo, "rev-parse", "HEAD").stdout.strip()
            code, _, _ = invoke(["--pull"]); after = raw(pullrepo, "rev-parse", "HEAD").stdout.strip()
            results["pull fast-forwards"] = code == 0 and before != after and clean_stash(pullrepo)

            write(pullrepo, "local-only.txt", "local\n"); raw(pullrepo, "add", "--", "local-only.txt")
            raw(pullrepo, "commit", "-m", "local only"); before = raw(pullrepo, "rev-parse", "HEAD").stdout.strip()
            raw(other, "pull", "--ff-only"); write(other, "remote-only.txt", "remote\n")
            raw(other, "add", "--", "remote-only.txt"); raw(other, "commit", "-m", "remote only"); raw(other, "push")
            code, _, _ = invoke(["--pull"]); after = raw(pullrepo, "rev-parse", "HEAD").stdout.strip()
            results["pull non-ff no-ops"] = code == 1 and before == after and clean_stash(pullrepo)

            serial = root / "serial"; raw(root, "clone", str(remote), str(serial))
            raw(serial, "config", "user.email", "serial@example.com"); raw(serial, "config", "user.name", "Serial")
            write(serial, "plans/one.md", "one\n"); write(serial, "plans/two.md", "two\n")
            with store._locked("git"):
                ps = [subprocess.Popen([sys.executable, "-c", child, str(serial), str(state), "-m", name,
                                        "plans/" + name + ".md"],
                                       env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                      for name in ("one", "two")]
                time.sleep(0.1)
                waited = all(proc.poll() is None for proc in ps)
            codes = [proc.wait(timeout=10) for proc in ps]
            results["concurrent commits serialize"] = waited and codes == [0, 0] and clean_stash(serial)

            append_a, append_b = root / "append-a", root / "append-b"
            for clone in (append_a, append_b):
                raw(root, "clone", str(remote), str(clone))
                raw(clone, "config", "user.email", "memory@example.com")
                raw(clone, "config", "user.name", "Memory")
            write(append_a, "memory/concurrent.md", "base\nleft\n")
            write(append_b, "memory/concurrent.md", "base\nright\n")
            ps = [subprocess.Popen(
                [sys.executable, "-c", child, str(clone), str(state), "-m", name,
                 "memory/concurrent.md"], env=env, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL)
                for clone, name in ((append_a, "left append"), (append_b, "right append"))]
            codes = [proc.wait(timeout=10) for proc in ps]
            raw(append_a, "pull", "--ff-only")
            concurrent_lines = (append_a / "memory/concurrent.md").read_text().splitlines()
            results["concurrent memory appends union"] = (
                codes == [0, 0] and concurrent_lines[0] == "base"
                and set(concurrent_lines[1:]) == {"left", "right"}
                and clean_stash(append_a) and clean_stash(append_b))

            line_a, line_b = root / "line-a", root / "line-b"
            for clone in (line_a, line_b):
                raw(root, "clone", str(remote), str(clone))
                raw(clone, "config", "user.email", "memory@example.com")
                raw(clone, "config", "user.name", "Memory")
            write(line_a, "memory/same-line.md", "left version\n")
            write(line_b, "memory/same-line.md", "right version\n")
            commit_mod.REPO_DIR = line_a
            left_code, _, _ = invoke(["-m", "left line", "memory/same-line.md"])
            commit_mod.REPO_DIR = line_b
            right_code, _, _ = invoke(["-m", "right line", "memory/same-line.md"])
            line_text = (line_b / "memory/same-line.md").read_text()
            results["same-line memory edits stack"] = (
                left_code == right_code == 0 and "left version" in line_text
                and "right version" in line_text and "<<<<<<<" not in line_text
                and clean_stash(line_a) and clean_stash(line_b))

            delete_a, delete_b = root / "delete-a", root / "delete-b"
            for clone in (delete_a, delete_b):
                raw(root, "clone", str(remote), str(clone))
                raw(clone, "config", "user.email", "memory@example.com")
                raw(clone, "config", "user.name", "Memory")
            write(delete_a, "memory/deletion.md", "first\nlast\n")
            write(delete_b, "memory/deletion.md", "first\nremove\nadded\nlast\n")
            commit_mod.REPO_DIR = delete_a
            delete_code, _, _ = invoke(["-m", "delete line", "memory/deletion.md"])
            commit_mod.REPO_DIR = delete_b
            append_code, _, _ = invoke(["-m", "adjacent append", "memory/deletion.md"])
            deletion_text = (delete_b / "memory/deletion.md").read_text()
            results["memory deletion race resurrects"] = (
                delete_code == append_code == 0 and "remove\n" in deletion_text
                and "added\n" in deletion_text and "<<<<<<<" not in deletion_text
                and clean_stash(delete_a) and clean_stash(delete_b))

            absolute = root / "absolute"
            raw(root, "clone", str(remote), str(absolute))
            raw(absolute, "config", "user.email", "absolute@example.com")
            raw(absolute, "config", "user.name", "Absolute")
            alias = root / "absolute-link"
            alias.symlink_to(absolute, target_is_directory=True)
            write(absolute, "plans/absolute.md", "absolute\n")
            commit_mod.REPO_DIR = absolute
            code, _, _ = invoke(["-m", "absolute path", str(alias / "plans/absolute.md")])
            results["absolute symlink path commits"] = (
                code == 0 and raw(absolute, "ls-files", "--", "plans/absolute.md").stdout.strip()
                == "plans/absolute.md" and clean_stash(absolute))

            nested = root / "nested"; raw(root, "init", str(nested))
            raw(nested, "config", "user.email", "public@example.com")
            raw(nested, "config", "user.name", "Public")
            write(nested, ".gitignore", ".private/\nmemory/\nplans/\nagents/*\n")
            write(nested, ".gitattributes",
                  (Path(__file__).resolve().parents[2] / ".gitattributes").read_text())
            write(nested, "public.txt", "public\n")
            raw(nested, "add", "--", ".gitattributes", ".gitignore", "public.txt")
            raw(nested, "commit", "-m", "public base")
            commit_mod.REPO_DIR = nested
            (nested / "memory").mkdir(); write(nested, "memory/refused.txt", "refused\n")
            public_before = raw(nested, "rev-parse", "HEAD").stdout.strip()
            code, _, err = invoke(["-m", "refuse private", "memory/refused.txt"])
            results["missing private repo refuses personal path"] = (
                code == 1 and "private repository missing" in err
                and public_before == raw(nested, "rev-parse", "HEAD").stdout.strip()
                and not raw(nested, "ls-files", "--", "memory/refused.txt").stdout.strip())

            private_remote = root / "private.git"
            private_git = nested / ".private/.git"
            raw(root, "init", "--bare", str(private_remote))
            raw(root, "init", "--bare", str(private_git))
            overlay(private_git, nested, "config", "core.bare", "false")
            overlay(private_git, nested, "config", "user.email", "private@example.com")
            overlay(private_git, nested, "config", "user.name", "Private")
            overlay(private_git, nested, "remote", "add", "origin", str(private_remote))
            write(nested, "memory/base.txt", "base\n")
            overlay(private_git, nested, "add", "-f", "--", "memory/base.txt")
            overlay(private_git, nested, "commit", "-m", "private base")
            overlay(private_git, nested, "branch", "-M", "private-overlay")
            overlay(private_git, nested, "push", "-u", "origin", "private-overlay")
            private_before = overlay(private_git, nested, "rev-parse", "HEAD").stdout.strip()
            (nested / "plans").mkdir(); (nested / "agents").mkdir()
            write(nested, "memory/routed.txt", "routed\n")
            write(nested, "plans/routed.txt", "plan\n")
            write(nested, "agents/routed.txt", "agent\n")
            code, out, _ = invoke([
                "-m", "route private", "memory/routed.txt", "plans/routed.txt",
                "agents/routed.txt"])
            public_after = raw(nested, "rev-parse", "HEAD").stdout.strip()
            private_after = overlay(private_git, nested, "rev-parse", "HEAD").stdout.strip()
            results["personal paths route to private overlay"] = (
                code == 0 and "pushed " in out and public_before == public_after
                and private_before != private_after
                and overlay(private_git, nested, "ls-files", "--", "memory/routed.txt").stdout.strip()
                == "memory/routed.txt"
                and overlay(private_git, nested, "ls-files", "--", "plans/routed.txt").stdout.strip()
                == "plans/routed.txt"
                and overlay(private_git, nested, "ls-files", "--", "agents/routed.txt").stdout.strip()
                == "agents/routed.txt"
                and overlay(private_git, nested, "check-attr", "merge", "--",
                            "memory/base.txt").stdout.strip()
                == "memory/base.txt: merge: union"
                and not raw(nested, "ls-files", "--", "memory/routed.txt").stdout.strip())

            for label, expected in cases.COMMIT_SCENARIOS:
                check(results.get(label) is expected, label, repr(results.get(label)))
    finally:
        commit_mod.REPO_DIR, store.STATE_DIR = old_repo, old_state
        constants.GIT_LOCK_WAIT = old_wait
    print("commit.py selftest: %d PASS, %d FAIL" % (passed, failed))
    return 1 if failed else 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()
    if not args.selftest:
        ap.error("nothing to do; commit_selftest.py is a test module")
    import commit
    return run(commit)


if __name__ == "__main__":
    sys.exit(main())
