#!/usr/bin/env python3
"""Copy every GitHub release of an upstream repo to our GitHub fork and GitLab mirror."""
import hashlib, json, os, pathlib, subprocess, sys, tempfile, time

up, name = sys.argv[1], sys.argv[2]
gh_repo, gl_repo = f"gabrielmf1998/{name}", f"gabriel17166/{name}"
work = pathlib.Path(os.path.expanduser("~/Documents/radmin-ports-mirror/assets")) / name

def run(*cmd, check=True, **kw):
    for attempt in range(4):
        p = subprocess.run(cmd, capture_output=True, text=True, **kw)
        if p.returncode == 0 or not check:
            return p
        time.sleep(5 * (attempt + 1))
    raise SystemExit(f"FAILED {' '.join(cmd[:4])}: {p.stderr.strip()[:300]}")

rels = json.loads(run("gh", "api", "--paginate", "--slurp", f"repos/{up}/releases?per_page=100").stdout)
rels = [r for page in rels for r in page if not r["draft"]]
latest = json.loads(run("gh", "api", f"repos/{up}/releases/latest", check=False).stdout or "{}").get("tag_name")
have_gh = set(run("gh", "release", "list", "-R", gh_repo, "-L", "500", "--json", "tagName", "-q", ".[].tagName").stdout.split())
gl = json.loads(run("glab", "api", f"projects/{gl_repo.replace('/', '%2F')}/releases?per_page=100").stdout)
have_gl = {r["tag_name"] for r in gl}

for r in sorted(rels, key=lambda r: r["created_at"]):
    tag = r["tag_name"]
    d = work / tag
    d.mkdir(parents=True, exist_ok=True)
    files = []
    for a in r["assets"]:
        f = d / a["name"]
        if not (f.exists() and f.stat().st_size == a["size"]):
            run("curl", "-fsSL", "--retry", "3", "-o", str(f), a["browser_download_url"])
        dig = (a.get("digest") or "")
        if dig.startswith("sha256:"):
            got = hashlib.sha256(f.read_bytes()).hexdigest()
            if got != dig[7:]:
                raise SystemExit(f"digest mismatch {tag}/{a['name']}")
        files.append(str(f))
    note = (f"> Mirror of the upstream release [{tag}]({r['html_url']}) of "
            f"[{up}](https://github.com/{up}), published by @{r['author']['login']} on "
            f"{r['published_at'][:10]}. Files copied unchanged"
            + (" and checked against the upstream sha256 digests" if any(a.get('digest') for a in r['assets']) else "")
            + ". Original authors and license apply.\n\n")
    note = note.replace("@", "")  # no GitHub mentions
    body = note + (r["body"] or "")
    nf = d.parent / f".{tag}.notes.md"
    nf.write_text(body)
    title = r["name"] or tag
    if tag not in have_gh:
        cmd = ["gh", "release", "create", tag, "-R", gh_repo, "--verify-tag", "--title", title,
               "--notes-file", str(nf), "--latest=" + ("true" if tag == latest else "false")]
        if r["prerelease"]:
            cmd.append("--prerelease")
        run(*cmd, *files)
    if tag not in have_gl:
        run("glab", "release", "create", tag, "-R", gl_repo, "--name", title, "--notes-file", str(nf), *files)
    print(f"{name} {tag}: {len(files)} files ok", flush=True)
