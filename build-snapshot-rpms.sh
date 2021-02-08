#!/bin/bash

# NOTES TO MYSELF(kwk):
# ---------------------
# Login to build host (e.g. tofan)
#  
#       ssh tofan
#
# Start of recover a previous session:
#
#       screen or screen -dr
#
# Work on non-NFS drive:
#
#       cd /opt/notnfs/$USER
#
# Clone this repo:
#
#       git clone --recurse-submodules https://github.com/kwk/llvm-daily-fedora-rpms.git
#
# Ensure %{_sourcdir} points to a writable location
#
#       mkdir -p /opt/notnfs/$USER/rpmbuild/SOURCES
#       echo '%_topdir /opt/notnfs/$USER/rpmbuild' >> ~/.rpmmacros
#
# The following should show /opt/notnfs/$USER/rpmbuild/SOURCES
#
#       rpm --eval '%{_sourcedir}'
#

set -eux

# Ensure Bash pipelines (e.g. cmd | othercmd) return a non-zero status if any of
# the commands fail, rather than returning the exit status of the last command
# in the pipeline.
set -o pipefail

# Define for which projects we want to build RPMS.
# See https://github.com/tstellar/llvm-project/blob/release-automation/llvm/utils/release/export.sh#L16
# projects=${projects:-"llvm clang test-suite compiler-rt libcxx libcxxabi clang-tools-extra polly lldb lld openmp libunwind"}
projects=${projects:-"llvm"}
# TODO(kwk): Projects not covered yet: clang-tools-extra and openmp

cur_dir=$(pwd)
out_dir=${cur_dir}/out
mkdir -pv $out_dir/{rpms,srpms}

# Get LLVM's latest git version and shorten it for the snapshot name
# NOTE(kwk): By specifying latest_git_sha=<git_sha> on the cli, this can be overwritten.  
latest_git_sha=${latest_git_sha:-}
if [ -z "${latest_git_sha}"]; then
    latest_git_sha=$(curl -s -H "Accept: application/vnd.github.v3+json" https://api.github.com/repos/llvm/llvm-project/commits | jq -r '.[].sha' | head -1)
fi
latest_git_sha_short=${latest_git_sha:0:8}

# Get the UTC date in yyyymmdd format
yyyymmdd=$(date --date='TZ="UTC"' +'%Y%m%d')

# For snapshot naming, see https://docs.fedoraproject.org/en-US/packaging-guidelines/Versioning/#_snapshots 
snapshot_name="${yyyymmdd}.${latest_git_sha_short}"

# TODO(kwk): How to integrate the snapshot_name into the RELEASE below?
# RELEASE="%{?rc_ver:0.}%{baserelease}%{?rc_ver:.rc%{rc_ver}}.${snapshot_name}%{?dist}"

# Get LLVM version from CMakeLists.txt
wget -O tmp/CMakeLists.txt https://raw.githubusercontent.com/llvm/llvm-project/${LATEST_GIT_SHA}/llvm/CMakeLists.txt
llvm_version_major=$(grep --regexp="set(\s*LLVM_VERSION_MAJOR" tmp/CMakeLists.txt | tr -d -c '[0-9]')
llvm_version_minor=$(grep --regexp="set(\s*LLVM_VERSION_MINOR" tmp/CMakeLists.txt | tr -d -c '[0-9]')
llvm_version_patch=$(grep --regexp="set(\s*LLVM_VERSION_PATCH" tmp/CMakeLists.txt | tr -d -c '[0-9]')
llvm_version="${llvm_version_major}.${llvm_version_minor}.${llvm_version_patch}"

# Extract for which Fedora Core version (e.g. fc34) we build packages.
# This is like the ongoing version number for the rolling Fedora "rawhide" release.
fc_version=$(grep -F "config_opts['releasever'] = " /etc/mock/templates/fedora-rawhide.tpl | tr -d -c '0-9')

# Create a changelog entry for all packages
changelog_date=$(date --date='TZ="UTC"' +'%a %b %d %Y')
cat <<EOF > ${out_dir}/changelog_entry
* ${changelog_date} Konrad Kleine <kkleine@redhat.com> ${llvm_version_major}.${llvm_version_minor}.${llvm_version_patch}-0.${snapshot_name}
- Daily build of ${llvm_version_major}.${llvm_version_minor}.${llvm_version_patch}-0.${snapshot_name}
EOF

# Get and extract the tarball of the latest LLVM version
# -R is for preserving the upstream timestamp (https://docs.fedoraproject.org/en-US/packaging-guidelines/#_timestamps)
llvm_src_dir=${out_dir}/llvm_project
mkdir -pv ${llvm_src_dir}
curl -R -L https://github.com/llvm/llvm-project/archive/${latest_git_sha}.tar.gz \
  | tar -C ${llvm_src_dir} --strip-components=1 -xzf -


for proj in $projects; do
    tarball_path=${out_dir}/$proj-${snapshot_name}.src.tar.xz
    project_src_dir=${llvm_scr_dir}/$proj-${snapshot_name}.src
    echo "Creating tarball for $proj in $tarball_path from $project_src_dir ..."
    mv $llvm_scr_dir/$proj $project_src_dir
    tar -C $llvm_scr_dir -cJf $tarball_path $project_src_dir

    # For envsubst to work below, we need to export variables as environment variables.
    export project_src_dir=$(basename $project_src_dir)
    export latest_git_sha
    export llvm_version_major
    export llvm_version_minor
    export llvm_version_patch
    export project_archive_url=$(basename $tarball_path)
    export changelog_entry=$(cat ${out_dir}/changelog_entry)

    # Resolve spec file and project if it's a link
    # TODO(kwk): We wouldn't need this if the openmp was called libomp 
    spec_file="spec-files/$proj.spec"
    if [ -L $spec_file ]; then
        spec_file=$(readlink $spec_file)
    fi
    spec_file=$(basename $spec_file)
    proj_sanitized=$(basename $spec_file .spec)

    envsubst '${project_src_dir} \
        ${latest_git_sha} \
        ${llvm_version_major} \
        ${llvm_version_minor} \
        ${llvm_version_patch} \
        ${project_archive_url} \
        ${changelog_entry} \ 
        ${snapshot_name}' < spec_files/$spec_file > rpms/$proj_sanitized/$proj_sanitized.spec

    # Download files from the specfile into the project directory
    spectool -R -g -A -C rpms/$proj_sanitized/ $proj_sanitized.spec

    # Build SRPM
    time mock -r rawhide-mock.cfg \
        --spec=$proj_sanitized.spec \
        --sources=rpms/$proj_sanitized/ \
        --buildsrpm \
        --resultdir=$out_dir/srpms \
        --no-cleanup-after \
        --isolation=simple

    # Build RPM
    time mock -r rawhide-mock.cfg \
        --rebuild $out_dir/srpms/${proj_sanitized}-${llvm_version}-0.${snapshot_name}.fc${fc_version}.src.rpm \
        --resultdir=$out_dir/rpms \
        --no-cleanup-after \
        --isolation=simple
done