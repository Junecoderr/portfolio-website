#!/usr/bin/env bash
# Install TB's .deb and .rpm in clean containers and run it (needs Docker).
#
#   bash packaging/test_linux_packages.sh dist
#
# dist/ holds tb_<version>_<arch>.deb, tb-<version>-1.<arch>.rpm and the flat
# apt index (Packages, Release: packaging/release_manifests.py --only apt).
# Docker pulls images for this computer's CPU, so an arm64 runner tests the
# arm64 packages. Each container: install, `tb --version`, welcome.tb, an
# example, and uninstall (which must remove /usr/bin/tb and /usr/lib/tb).
set -euo pipefail

dist="$(cd "${1:-dist}" && pwd)"
ls "$dist"/tb_*.deb "$dist"/tb-*.rpm "$dist"/Packages >/dev/null

# Runs inside every container once tb is installed.
check='set -e
test "$(readlink -f /usr/bin/tb)" = /usr/lib/tb/tb
tb --version
tb /usr/lib/tb/welcome.tb </dev/null | grep -q "Welcome to TB"
cd /tmp && printf "say(6 * 7)\n" > answer.tb && test "$(tb answer.tb)" = 42
tb /usr/lib/tb/examples/hello.tb
echo "tb works"'

run() {
  local image="$1" script="$2"
  echo "::group::$image"
  docker run --rm -v "$dist:/dist:ro" "$image" bash -c "$script"
  echo "::endgroup::"
}

# 1. apt install ./tb_<version>_<arch>.deb
for image in ubuntu:22.04 debian:12; do
  run "$image" "set -e
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
cp /dist/tb_*_\$(dpkg --print-architecture).deb /tmp/
apt-get install -y -qq /tmp/tb_*.deb >/dev/null
$check
apt-get remove -y -qq tb >/dev/null
test ! -e /usr/bin/tb && test ! -e /usr/lib/tb"
done

# 2. apt install tb, from a flat apt repository laid out as code.tanisha.ceo/apt/
#    serves it ([trusted=yes] because the CI index is unsigned).
run ubuntu:24.04 "set -e
export DEBIAN_FRONTEND=noninteractive
mkdir /srv/repo && ln -s /dist /srv/repo/apt
echo 'deb [trusted=yes] file:/srv/repo apt/' > /etc/apt/sources.list.d/tb.list
apt-get update -qq
apt-cache policy tb
apt-get install -y -qq tb >/dev/null
$check"

# 3. dnf install ./tb-<version>-1.<arch>.rpm
run fedora:42 "set -e
dnf install -y -q /dist/tb-*.\$(uname -m).rpm
$check
rpm -V tb
dnf remove -y -q tb
test ! -e /usr/bin/tb && test ! -e /usr/lib/tb"
