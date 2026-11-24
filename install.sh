#!/usr/bin/env bash

set -euo pipefail

if [[ "$(uname -s)" != Linux || "$(uname -m)" != x86_64 ]]; then
    printf 'This installer supports x86_64 Linux, including Ubuntu on WSL.\n' >&2
    exit 1
fi

for dependency in curl git python3 sha256sum tar; do
    if ! command -v "$dependency" >/dev/null; then
        printf 'Install %s and run this installer again.\n' "$dependency" >&2
        exit 1
    fi
done

repo_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
bin_dir="$HOME/.local/bin"
plugin_dir="$HOME/.local/share/antigravity-herdr-workbench/plugin"
stage=$(mktemp -d)
trap 'rm -rf -- "$stage"' EXIT
mkdir -p -- "$bin_dir"
export PATH="$bin_dir:$PATH"

download_verified() {
    local url=$1 expected=$2 output=$3
    curl -fLsS --retry 2 --connect-timeout 15 --max-time 180 "$url" -o "$output"
    printf '%s  %s\n' "$expected" "$output" | sha256sum --check --status
}

if [[ ! -x "$bin_dir/herdr" ]]; then
    download_verified 'https://github.com/herdrdev/herdr/releases/download/v0.9.5/herdr-linux-x86_64' \
        '14d9e8bc8b4a9e0118d77c32266f76d461f09b40342eb23752960bd89f59929d' "$stage/herdr"
    install -m 0755 "$stage/herdr" "$bin_dir/herdr"
fi

if [[ ! -x "$bin_dir/agys" ]]; then
    download_verified 'https://github.com/quaywin/agys/releases/download/v0.2.40/agys_0.2.40_linux_amd64.tar.gz' \
        'e064673326f5caa5a9b1741897ba85d70ad3e69d3e4d18772a82faf3418b7f02' "$stage/agys.tar.gz"
    tar -xzf "$stage/agys.tar.gz" -C "$stage" agys
    install -m 0755 "$stage/agys" "$bin_dir/agys"
fi

if ! command -v agy >/dev/null; then
    curl -fLsS --retry 2 --connect-timeout 15 --max-time 90 \
        https://antigravity.google/cli/install.sh -o "$stage/install-agy.sh"
    bash "$stage/install-agy.sh"
fi

install -m 0755 "$repo_dir/scripts/agy-herdr-projects.py" "$bin_dir/agy-herdr-projects"
plugin_list=$("$bin_dir/herdr" plugin list)
if [[ "$plugin_list" != *quaywin.agys* ]]; then
    install -D -m 0644 "$repo_dir/upstream/agys/herdr-plugin.toml" "$plugin_dir/herdr-plugin.toml"
    "$bin_dir/herdr" plugin link "$plugin_dir"
fi

"$bin_dir/herdr" --version
"$bin_dir/agys" --version
agy --version
printf '\nOpen the workbench with %s/agy-herdr-projects --choose\n' "$bin_dir"
