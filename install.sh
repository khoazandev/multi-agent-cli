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
library="$HOME/.local/lib/multi-agent-cli"
stage=$(mktemp -d)
trap 'rm -rf -- "$stage"' EXIT
mkdir -p -- "$bin_dir" "$library"
export PATH="$bin_dir:$PATH"

download_verified() {
    local url=$1 expected=$2 output=$3
    curl -fLsS --retry 2 --connect-timeout 15 --max-time 180 "$url" -o "$output"
    if ! has_digest "$expected" "$output"; then
        printf 'Checksum mismatch for %s.\n' "$url" >&2
        exit 1
    fi
}

has_digest() {
    [[ -r "$2" ]] && printf '%s  %s\n' "$1" "$2" | sha256sum --check --status
}

replace_file() {
    local source=$1 mode=$2 destination=$3 temporary
    temporary=$(mktemp -- "$destination.new.XXXXXX")
    install -m "$mode" "$source" "$temporary"
    mv -fT -- "$temporary" "$destination"
}

herdr_sha='14d9e8bc8b4a9e0118d77c32266f76d461f09b40342eb23752960bd89f59929d'
if ! has_digest "$herdr_sha" "$library/herdr"; then
    if has_digest "$herdr_sha" "$bin_dir/herdr"; then
        replace_file "$bin_dir/herdr" 0755 "$library/herdr"
    else
        download_verified 'https://github.com/herdrdev/herdr/releases/download/v0.9.5/herdr-linux-x86_64' \
            "$herdr_sha" "$stage/herdr"
        replace_file "$stage/herdr" 0755 "$library/herdr"
    fi
fi

go_version='go1.26.8'
go_command=$(command -v go || true)
if [[ -z "$go_command" || "$(GOTOOLCHAIN=local "$go_command" version)" != "go version $go_version linux/amd64" ]]; then
    download_verified "https://go.dev/dl/$go_version.linux-amd64.tar.gz" \
        'd0f743b33e8d8945e6b1f432edd15785c70507121d6e2a723b21285eddf8b57b' "$stage/go.tar.gz"
    tar -xzf "$stage/go.tar.gz" -C "$stage"
    go_command="$stage/go/bin/go"
fi

(
    cd "$repo_dir/upstream/agys"
    GOTOOLCHAIN=local CGO_ENABLED=0 "$go_command" build -trimpath -buildvcs=false -mod=readonly \
        -ldflags '-X github.com/quaywin/agys/pkg/version.Version=0.2.40-multi-agent-cli' \
        -o "$stage/agys" .
)

if ! command -v agy >/dev/null; then
    curl -fLsS --retry 2 --connect-timeout 15 --max-time 90 \
        https://antigravity.google/cli/install.sh -o "$stage/install-agy.sh"
    bash "$stage/install-agy.sh"
fi

replace_file "$stage/agys" 0755 "$library/agys"
replace_file "$repo_dir/scripts/desktop_api.py" 0644 "$library/desktop_api.py"
replace_file "$repo_dir/scripts/herdr_layout.py" 0644 "$library/herdr_layout.py"
replace_file "$repo_dir/scripts/agy-herdr-projects.py" 0755 "$library/agy-herdr-projects.py"
launcher_stage=$(mktemp -d "$bin_dir/.multi-agent-cli.XXXXXX")
ln -s -- "$library/agy-herdr-projects.py" "$launcher_stage/agy-herdr-projects"
mv -fT -- "$launcher_stage/agy-herdr-projects" "$bin_dir/agy-herdr-projects"
rmdir -- "$launcher_stage"

"$library/herdr" --version
"$library/agys" --version
agy --version
printf '\nOpen the workbench with %s/agy-herdr-projects --manage\n' "$bin_dir"
