#!/bin/bash
set -euxo pipefail
exec > >(tee -a /var/log/user-data.log) 2>&1

# --- Package updates and install ---
dnf update -y
# AL2023 ships Python 3.9 by default. Install boto3 via dnf (not pip - no NAT
# Gateway means pypi.org is unreachable from this private subnet).
dnf install -y amazon-efs-utils fuse fuse-libs python3-boto3
ln -sf "$(command -v python3)" /usr/local/bin/python3-bench

# --- Install Mountpoint for S3 ---
# AL2023 ships mount-s3 in the default repos after April 2026.
# Fallback to the official signed RPM if the package isn't available.
if ! dnf install -y mount-s3; then
  arch=$(uname -m)
  curl -fSL "https://s3.amazonaws.com/mountpoint-s3-release/latest/$${arch}/mount-s3.rpm" \
    -o /tmp/mount-s3.rpm
  dnf install -y /tmp/mount-s3.rpm
  rm -f /tmp/mount-s3.rpm
fi

# --- Create mount points ---
mkdir -p /mnt/s3files /mnt/mountpoint

# --- Mount S3 Files (NFS via amazon-efs-utils / s3files type) ---
mount -t s3files ${file_system_id}:/ /mnt/s3files
# Persist mount across reboots
grep -q "/mnt/s3files" /etc/fstab || \
  echo "${file_system_id}:/ /mnt/s3files s3files defaults,_netdev 0 0" >> /etc/fstab

# --- Mount Mountpoint for S3 ---
# --allow-delete: lets unlink() work
# --allow-overwrite: lets new writes replace an existing object
# --allow-other: lets non-root processes (the benchmark user) access the mount
mount-s3 ${bucket_name} /mnt/mountpoint \
  --region ${region} \
  --allow-delete \
  --allow-overwrite \
  --allow-other

# --- Fetch the runner script from S3 ---
# The runner is uploaded to s3://${bucket_name}/runner/benchmark.py by `make deploy-runner`.
mkdir -p /opt/runner
aws s3 cp "s3://${bucket_name}/runner/benchmark.py" /opt/runner/benchmark.py || \
  echo "Runner not yet uploaded - upload via 'make deploy-runner'"
chmod +x /opt/runner/benchmark.py 2>/dev/null || true

# --- Bootstrap done marker ---
date -u +%Y-%m-%dT%H:%M:%SZ > /var/log/bootstrap.done
