#!/usr/bin/env bash
# ============================================================
#  bootstrap.sh - one command install / upgrade:
#
#    curl -fsSL https://raw.githubusercontent.com/gabrielmf1998/Radmin-Linux-KDE/main/bootstrap.sh | bash
#
#  Downloads the latest release (code + VM image), checks every file against
#  the release SHA256SUMS and runs install.sh.
#   * downloads go to ~/.local/share/radmin-linux/.download (real disk, never
#     /tmp, which is RAM on most distros) and RESUME if the connection drops:
#     just run the same command again
#   * an installed VM image is kept (your Radmin identity); upgrades only
#     replace the app
#  Options (put them before "bash": ... | RADMIN_MIRROR=gitlab bash):
#    RADMIN_MIRROR=gitlab    download from the GitLab mirror
#    RADMIN_VERSION=0.3.0    a specific release instead of the latest
#    RADMIN_NEW_IMAGE=1      download a fresh VM image even if one is installed
#    RADMIN_VM_IMAGE=/path   use a local image file, no image download
#    RADMIN_RELEASE_URL / RADMIN_SRC_URL   testing: another place for the
#                            release files / the code tarball
# ============================================================
set -euo pipefail

main() {
  local GH_REPO="gabrielmf1998/Radmin-Linux-KDE"
  local GL_REPO="https://gitlab.com/gabriel17166/radmin-linux"
  local DEST="${RADMIN_HOME:-$HOME/.local/share/radmin-linux}"
  local DL="$DEST/.download"
  local IMG_NAME="radmin-linux-base.qcow2.zst"

  say(){ printf '\033[1;36m>>\033[0m %s\n' "$*"; }
  die(){ printf '\033[1;31m[ERROR]\033[0m %s\n' "$*" >&2; exit 1; }

  [[ $EUID -ne 0 ]] || die "run it as your normal user (no sudo); it asks for the password itself"
  command -v curl >/dev/null || die "curl is missing (install it with your package manager)"
  command -v sha256sum >/dev/null || die "sha256sum is missing (coreutils)"
  command -v tar >/dev/null || die "tar is missing"

  # ---- which release ----
  local ver="${RADMIN_VERSION:-}" mirror="${RADMIN_MIRROR:-github}" base src
  if [[ -z "$ver" ]]; then
    case "$mirror" in
      github) ver="$(curl -fsSIL -o /dev/null -w '%{url_effective}' "https://github.com/$GH_REPO/releases/latest" | sed -n 's|.*/tag/v\{0,1\}||p')" ;;
      gitlab) ver="$(curl -fsSL "https://gitlab.com/api/v4/projects/gabriel17166%2Fradmin-linux/releases/permalink/latest" | sed -n 's/.*"tag_name":"v\{0,1\}\([^"]*\)".*/\1/p')" ;;
      *) die "RADMIN_MIRROR must be github or gitlab" ;;
    esac
    [[ -n "$ver" ]] || die "could not find the latest release (no internet? try RADMIN_MIRROR=gitlab)"
  fi
  ver="${ver#v}"
  case "$mirror" in
    github) base="https://github.com/$GH_REPO/releases/download/v$ver"
            src="https://codeload.github.com/$GH_REPO/tar.gz/refs/tags/v$ver" ;;
    gitlab) base="$GL_REPO/-/releases/v$ver/downloads"
            src="$GL_REPO/-/archive/v$ver/radmin-linux-v$ver.tar.gz" ;;
  esac
  # testing: serve a release from a local folder / other server
  [[ -n "${RADMIN_RELEASE_URL:-}" ]] && base="$RADMIN_RELEASE_URL"
  [[ -n "${RADMIN_SRC_URL:-}" ]] && src="$RADMIN_SRC_URL"
  say "Radmin-Linux $ver ($mirror)"

  # ---- where the downloads go ----
  mkdir -p "$DL"
  local fstype; fstype="$(stat -f -c %T "$DL" 2>/dev/null || echo unknown)"
  [[ "$fstype" == tmpfs || "$fstype" == ramfs ]] && die "$DL is on $fstype (RAM); set RADMIN_HOME to a folder on a real disk"

  fetch(){   # fetch URL FILE: progress bar, resume, retries
    local url="$1" out="$2" n
    for n in 1 2 3 4 5; do
      if curl -fL --retry 5 --retry-delay 3 --retry-all-errors --connect-timeout 20 \
              --speed-limit 1024 --speed-time 60 -C - --progress-bar -o "$out" "$url"; then
        return 0
      fi
      say "   download interrupted, retrying ($n/5)…"; sleep 3
    done
    return 1
  }

  say "Checksums"
  curl -fsSL --retry 5 --retry-all-errors -o "$DL/SHA256SUMS" "$base/SHA256SUMS" \
    || die "could not download SHA256SUMS from $base"

  say "Code"
  rm -f "$DL/src.tar.gz"
  curl -fsSL --retry 5 --retry-all-errors -o "$DL/src.tar.gz" "$src" || die "could not download the code"
  rm -rf "$DL/src"; mkdir -p "$DL/src"
  tar xzf "$DL/src.tar.gz" -C "$DL/src" --strip-components=1 || die "the code archive is damaged"
  rm -f "$DL/src.tar.gz"

  # ---- VM image ----
  local img_arg=""
  if [[ -n "${RADMIN_VM_IMAGE:-}" ]]; then
    img_arg="$RADMIN_VM_IMAGE"
  elif [[ -f "$DEST/vm/bench.qcow2" && "${RADMIN_NEW_IMAGE:-0}" != 1 ]]; then
    say "VM image already installed: kept (RADMIN_NEW_IMAGE=1 to replace it)"
  else
    local parts; parts="$(awk '{print $2}' "$DL/SHA256SUMS" | sed 's/^\*//' | grep -E "^$IMG_NAME(\.part-[a-z]+)?$" | sort)"
    [[ -n "$parts" ]] || die "this release has no VM image listed in SHA256SUMS"
    local need=0 p sz
    declare -A size=()
    for p in $parts; do
      sz="$( { curl -fsIL "$base/$p" || true; } | tr -d '\r' | awk 'tolower($1)=="content-length:"{v=$2} END{print v+0}')"
      size[$p]="${sz:-0}"; need=$(( need + ${sz:-0} ))
    done
    local free; free=$(( $(df -P -B1 "$DL" | awk 'NR==2{print $4}') ))
    # the parts + the unpacked image (about 3x the download) must fit
    if (( need > 0 && free < need * 4 )); then
      die "not enough disk space in $DEST: need about $(( need * 4 / 1073741824 + 1 )) GB, have $(( free / 1073741824 )) GB"
    fi
    say "VM image ($(( need / 1048576 )) MB, resumes if interrupted)"
    for p in $parts; do
      if [[ -f "$DL/$p" ]] && (cd "$DL" && grep -E " \*?$p\$" SHA256SUMS | sha256sum -c --status); then
        say "   $p already downloaded"; continue
      fi
      # a full-size file that fails the checksum cannot be resumed: start over
      if [[ -f "$DL/$p" ]] && (( ${size[$p]} > 0 && $(stat -c %s "$DL/$p") >= ${size[$p]} )); then rm -f "$DL/$p"; fi
      say "   $p"
      fetch "$base/$p" "$DL/$p" || die "download failed; run the same command again to resume"
      if ! (cd "$DL" && grep -E " \*?$p\$" SHA256SUMS | sha256sum -c --status); then
        rm -f "$DL/$p"
        die "$p is corrupted (checksum mismatch) and was deleted; run the command again"
      fi
    done
    say "Unpacking the image"
    command -v zstd >/dev/null || NEED_ZSTD=1
    if [[ "${NEED_ZSTD:-0}" == 1 ]]; then
      # install.sh installs zstd; unpack after it with the parts list
      img_arg=""
    else
      (cd "$DL" && cat $parts) | zstd -d -q --long=31 -o "$DL/bench.qcow2" || die "could not unpack the image (disk full?)"
      (cd "$DL" && rm -f $parts)
      img_arg="$DL/bench.qcow2"
    fi
  fi

  say "Installing"
  if [[ "${NEED_ZSTD:-0}" == 1 ]]; then
    bash "$DL/src/install.sh"                     # installs the packages incl. zstd
    (cd "$DL" && cat $parts) | zstd -d -q --long=31 -o "$DL/bench.qcow2" || die "could not unpack the image"
    (cd "$DL" && rm -f $parts)
    img_arg="$DL/bench.qcow2"
  fi
  if [[ -n "$img_arg" ]]; then
    RADMIN_IMAGE_MOVE=1 bash "$DL/src/install.sh" "$img_arg"
  else
    bash "$DL/src/install.sh"
  fi

  # downloads are only needed until the install succeeded
  rm -rf "$DL"
  say "Done. Open 'Radmin VPN' from the menu, or run: radmin-linux"
}

# everything runs from main, so a download cut in the middle of this script
# never executes half of it
main "$@"
