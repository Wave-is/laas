#!/usr/bin/env bash
# ==============================================================================
# LAAS Cluster — Linux Node Telegraf Setup Script
# Standardized setup for NVIDIA GPU, CPU, RAM metrics over Prometheus HTTP (:9273)
# ==============================================================================
set -euo pipefail

echo "==> [LAAS] Checking NVIDIA GPU drivers and nvidia-smi..."
if ! command -v nvidia-smi &>/dev/null; then
    echo "ERROR: nvidia-smi not found. Ensure NVIDIA proprietary drivers are installed." >&2
    exit 1
fi
nvidia-smi --query-gpu=name,driver_version --format=csv,noheader

echo "==> [LAAS] Checking Telegraf..."
if ! command -v telegraf &>/dev/null; then
    echo "==> [LAAS] Installing Telegraf via InfluxData repository..."
    if command -v apt-get &>/dev/null; then
        sudo apt-get update && sudo apt-get install -y wget gnupg2
        wget -q https://repos.influxdata.com/influxdata-archive_compat.key
        echo '393eaddb627ccbd51a96f85e14ac5813e45ed723fadb0f7250b9864ad808bc1b influxdata-archive_compat.key' | sha256sum -c
        cat influxdata-archive_compat.key | gpg --dearmor | sudo tee /etc/apt/trusted.gpg.d/influxdata-archive_compat.gpg > /dev/null
        rm -f influxdata-archive_compat.key
        echo 'deb [signed-by=/etc/apt/trusted.gpg.d/influxdata-archive_compat.gpg] https://repos.influxdata.com/debian stable main' | sudo tee /etc/apt/sources.list.d/influxdata.list
        sudo apt-get update && sudo apt-get install -y telegraf
    else
        echo "ERROR: Package manager not supported automatically. Install telegraf manually." >&2
        exit 1
    fi
fi

echo "==> [LAAS] Creating /etc/telegraf/telegraf.d/nvidia.conf..."
sudo mkdir -p /etc/telegraf/telegraf.d
cat <<'EOF' | sudo tee /etc/telegraf/telegraf.d/nvidia.conf > /dev/null
# LAAS Cluster — NVIDIA GPU Telemetry Plugin
[[inputs.nvidia_smi]]
  # timeout = "5s"
EOF

echo "==> [LAAS] Ensuring Prometheus client output in /etc/telegraf/telegraf.d/prometheus.conf..."
cat <<'EOF' | sudo tee /etc/telegraf/telegraf.d/prometheus.conf > /dev/null
# LAAS Cluster — Prometheus Exporter Endpoint
[[outputs.prometheus_client]]
  listen = ":9273"
  path = "/metrics"
  collectors_exclude = ["gocollector", "process"]
EOF

echo "==> [LAAS] Testing Telegraf configuration..."
sudo telegraf --test --input-filter nvidia_smi

echo "==> [LAAS] Restarting and enabling telegraf service..."
sudo systemctl enable telegraf
sudo systemctl restart telegraf

echo "==> [LAAS] Verifying endpoint http://localhost:9273/metrics..."
sleep 2
if curl -s http://localhost:9273/metrics | grep -q "nvidia_smi"; then
    echo "SUCCESS: Telegraf is running and exposing NVIDIA GPU metrics on port 9273!"
    echo "Endpoint: http://<NODE_IP>:9273/metrics"
else
    echo "NOTE: Metrics will appear after first 10-20s collection interval."
fi
