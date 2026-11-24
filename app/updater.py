"""
updater.py - checks for a newer Radmin-Linux release and installs it.

The check is one small HTTPS request to the GitHub releases API (GitLab when
RADMIN_MIRROR=gitlab or GitHub does not answer). The update runs the
bootstrap.sh of that exact tag, the same script as the curl one-liner: it
downloads only the code (the installed VM image is kept), runs install.sh and
logs to install.log. The in-VM agent upgrades itself on the next refresh.
"""
from __future__ import annotations
import json, os, re, subprocess, urllib.request

import config

GITHUB = "gabrielmf1998/Radmin-Linux-KDE"
GITLAB = "gabriel17166/radmin-linux"
GH_API = f"https://api.github.com/repos/{GITHUB}/releases/latest"
GL_API = f"https://gitlab.com/api/v4/projects/{GITLAB.replace('/', '%2F')}/releases/permalink/latest"
UA = f"radmin-linux/{config.VERSION}"


def _vtuple(v: str) -> tuple[int, ...]:
    return tuple(int(x) for x in re.findall(r"\d+", v)[:3]) or (0,)


def _get_json(url: str, timeout: float) -> dict | None:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read(512 * 1024).decode("utf-8", "replace"))
    except (OSError, ValueError):
        return None


def _mirror() -> str:
    return os.environ.get("RADMIN_MIRROR") or config._FILE.get("RADMIN_MIRROR", "") or "github"


def check(timeout: float = 10) -> dict:
    """{'ok', 'current', 'latest', 'newer', 'notes', 'url', 'mirror'} or {'ok': False, 'error'}."""
    order = ["gitlab", "github"] if _mirror() == "gitlab" else ["github", "gitlab"]
    for m in order:
        d = _get_json(GL_API if m == "gitlab" else GH_API, timeout)
        if not isinstance(d, dict) or not d.get("tag_name"):
            continue
        tag = str(d["tag_name"])
        latest = tag.lstrip("v")
        notes = str(d.get("body") or d.get("description") or "")
        url = d.get("html_url") or (d.get("_links") or {}).get("self") or ""
        return {"ok": True, "current": config.VERSION, "latest": latest, "tag": tag,
                "newer": _vtuple(latest) > _vtuple(config.VERSION),
                "notes": notes, "url": url, "mirror": m}
    return {"ok": False, "error": "no answer from GitHub or GitLab"}


def managed_by_package() -> bool:
    """Installed from a distro package (/usr/share/...): the package manager
    updates it, not the in-app updater."""
    return str(config.CODE_ROOT).startswith(("/usr/", "/opt/"))


def bootstrap_url(tag: str, mirror: str) -> str:
    if mirror == "gitlab":
        return f"https://gitlab.com/{GITLAB}/-/raw/{tag}/bootstrap.sh"
    return f"https://raw.githubusercontent.com/{GITHUB}/{tag}/bootstrap.sh"


def short_notes(notes: str, limit: int = 900) -> str:
    """Release notes as plain text for a dialog."""
    t = re.sub(r"`+", "", notes)
    t = re.sub(r"\*\*(.+?)\*\*", r"\1", t)
    t = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", t)
    t = re.sub(r"^#+\s*", "", t, flags=re.M)
    t = re.sub(r"\n{3,}", "\n\n", t).strip()
    return t if len(t) <= limit else t[:limit].rsplit("\n", 1)[0] + "\n…"


def install(tag: str, mirror: str, timeout: int = 3600) -> tuple[bool, str]:
    """Download the bootstrap of `tag` and run it (code only, image kept).
    Blocking; run it in a worker thread. Output goes to install.log too."""
    import backend
    home = config.HOME
    os.makedirs(home, exist_ok=True)
    script = os.path.join(home, "update-bootstrap.sh")
    req = urllib.request.Request(bootstrap_url(tag, mirror), headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            body = r.read(1024 * 1024)
    except OSError as e:
        return False, f"could not download the installer: {e}"
    if not body.startswith(b"#!") or b"radmin" not in body.lower():
        return False, "the downloaded installer does not look right; not running it"
    with open(script, "wb") as f:
        f.write(body)
    env = dict(os.environ, RADMIN_VERSION=tag.lstrip("v"), RADMIN_MIRROR=mirror)
    env.pop("RADMIN_NEW_IMAGE", None)
    rc, out = backend.run_capture(["bash", script], timeout=timeout, env=env)
    try:
        os.remove(script)
    except OSError:
        pass
    tail = "\n".join(l for l in out.splitlines() if l.strip())[-1500:]
    return rc == 0, tail


def relaunch() -> None:
    """Start the (updated) app again a moment after this one quits."""
    launcher = os.path.join(config.HOME, "radmin-linux.sh")
    if not os.path.exists(launcher):
        launcher = str(config.CODE_ROOT / "radmin-linux.sh")
    subprocess.Popen(["sh", "-c", 'sleep 2; exec "$0"', launcher],
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, start_new_session=True)


if __name__ == "__main__":
    print(json.dumps(check(), indent=1)[:1500])
