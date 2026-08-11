#!/bin/sh
# Trusted wrapper for a bubblewrap sandbox inside WSL2. The untrusted command
# sees a minimal filesystem, no Windows drives, no host network, and only the
# current task worktree as writable.
set -eu

workspace=${1:?missing workspace}
command_text=${2:?missing command}
git_common=${3:-}
git_worktree_rel=${4:-}

command -v bwrap >/dev/null 2>&1 || {
  echo "bubblewrap is required inside the selected WSL2 distro" >&2
  exit 125
}

case "$workspace" in
  /mnt/[a-zA-Z]/*|/home/*|/tmp/*) ;;
  *) echo "unsupported WSL workspace path" >&2; exit 125 ;;
esac

git_pointer=""
git_directory=""
if [ -d "$workspace/.git" ]; then
  git_directory="$workspace/.git"
fi
cleanup() {
  [ -z "$git_pointer" ] || rm -f "$git_pointer" 2>/dev/null || true
}
trap cleanup EXIT HUP INT TERM

set -- \
  --die-with-parent --new-session --unshare-all --cap-drop ALL --clearenv \
  --ro-bind /usr /usr --ro-bind /etc /etc \
  --proc /proc --dev /dev \
  --tmpfs /tmp --tmpfs /run --tmpfs /home --dir /home/sandbox \
  --bind "$workspace" /workspace --chdir /workspace \
  --setenv HOME /home/sandbox --setenv TMPDIR /tmp \
  --setenv PATH /usr/local/bin:/usr/bin:/bin \
  --setenv GIT_OPTIONAL_LOCKS 0

# Preserve merged-/usr symlinks without exposing the old root filesystem.
for item in bin sbin lib lib64; do
  if [ -L "/$item" ]; then
    set -- "$@" --symlink "$(readlink "/$item")" "/$item"
  elif [ -d "/$item" ]; then
    set -- "$@" --ro-bind "/$item" "/$item"
  fi
done

# Windows Git worktrees contain an absolute Windows gitdir pointer. Mount only
# the common repository metadata and overlay a Linux-readable pointer.
if [ -n "$git_common" ] && [ -n "$git_worktree_rel" ]; then
  git_pointer=$(mktemp /tmp/wenmo-git-pointer.XXXXXX)
  printf 'gitdir: /repo.git/%s\n' "$git_worktree_rel" > "$git_pointer"
  set -- "$@" --ro-bind "$git_common" /repo.git \
    --ro-bind "$git_pointer" /workspace/.git
elif [ -n "$git_directory" ]; then
  set -- "$@" --ro-bind "$git_directory" /workspace/.git
fi

exec bwrap "$@" /bin/sh -lc "$command_text"
