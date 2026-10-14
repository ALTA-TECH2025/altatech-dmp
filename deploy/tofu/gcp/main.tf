# OpenTofu（MPL-2.0）：GCP 基礎設施骨架。tofu init && tofu apply -var project=... -var region=asia-east1
terraform { required_providers { google = { source = "hashicorp/google", version = "~> 6.0" }, kubernetes = { source = "hashicorp/kubernetes", version = "~> 2.30" } } }
provider "google" { project = var.project  region = var.region }

variable "project" { type = string }
variable "region"  { type = string  default = "asia-east1" }
variable "env"     { type = string  default = "prod" }

resource "google_compute_network" "vpc" { name = "dmp-${var.env}"  auto_create_subnetworks = false }
resource "google_compute_subnetwork" "gke" {
  name = "dmp-gke"  region = var.region  network = google_compute_network.vpc.id  ip_cidr_range = "10.10.0.0/20"
  private_ip_google_access = true
  secondary_ip_range { range_name = "pods"      ip_cidr_range = "10.20.0.0/16" }
  secondary_ip_range { range_name = "services"  ip_cidr_range = "10.30.0.0/20" }
}

resource "google_kms_key_ring" "dmp" { name = "dmp"  location = var.region }
resource "google_kms_crypto_key" "data" { name = "data"  key_ring = google_kms_key_ring.dmp.id  rotation_period = "7776000s" }

resource "google_container_cluster" "gke" {
  name = "dmp-${var.env}"  location = var.region  network = google_compute_network.vpc.id  subnetwork = google_compute_subnetwork.gke.id
  remove_default_node_pool = true  initial_node_count = 1
  ip_allocation_policy { cluster_secondary_range_name = "pods"  services_secondary_range_name = "services" }
  private_cluster_config { enable_private_nodes = true  enable_private_endpoint = false  master_ipv4_cidr_block = "172.16.0.0/28" }
  workload_identity_config { workload_pool = "${var.project}.svc.id.goog" }
  binary_authorization { evaluation_mode = "PROJECT_SINGLETON_POLICY_ENFORCE" }
  database_encryption { state = "ENCRYPTED"  key_name = google_kms_crypto_key.data.id }
  release_channel { channel = "REGULAR" }
}

# 節點池：預設為 docs/13 的「標準配置」（3 年 300 億事件、每日 1,000 標籤、50 人同時上線）
variable "profile" { type = string  default = "20b" }   # 20b（預設，7 台）| 30b | large
locals {
  profiles = {
    "20b" = {   # docs/13 §4.1：3 個節點池 7 台 + 臨時 Spot
      olap     = { machine = "c3-highmem-22",  min = 2, max = 3,  disk = 100 }   # ClickHouse 1×2；資料盤 Hyperdisk Balanced 4 TB PVC
      general  = { machine = "n2-standard-16", min = 3, max = 4,  disk = 200 }   # Kafka/Keeper/Flink/Airflow/Collector/CDC/Keycloak/Prometheus/Grafana
      app      = { machine = "n2-standard-8",  min = 2, max = 3,  disk = 100 }   # API/語意層/分群/OneID/閘道/Agent/TagSync/Web
      spot     = { machine = "n2-standard-8",  min = 0, max = 4,  disk = 100 }   # 舊資料導入、大批匯入、模型（Spot）
    }
    "30b" = {
      ingest   = { machine = "n2-standard-4",  min = 2, max = 6,  disk = 100 }
      stream   = { machine = "n2-standard-8",  min = 2, max = 4,  disk = 200 }
      batch    = { machine = "n2-standard-8",  min = 2, max = 4,  disk = 200 }
      data     = { machine = "n2-standard-4",  min = 3, max = 5,  disk = 100 }   # Kafka（1 TB PD 由 Strimzi PVC 佈建）+ Keeper
      olap     = { machine = "c3-highmem-22",  min = 4, max = 6,  disk = 100 }   # ClickHouse 資料盤：Hyperdisk Balanced 4 TB PVC
      app      = { machine = "n2-standard-8",  min = 3, max = 6,  disk = 100 }
      platform = { machine = "n2-standard-4",  min = 2, max = 3,  disk = 200 }
    }
    "large" = {
      ingest   = { machine = "c3-standard-8",  min = 3, max = 10, disk = 100 }
      stream   = { machine = "c3-standard-16", min = 4, max = 12, disk = 200 }
      batch    = { machine = "n2-standard-8",  min = 3, max = 6,  disk = 200 }
      data     = { machine = "n2-standard-8",  min = 3, max = 6,  disk = 100 }
      olap     = { machine = "c3-highmem-44",  min = 4, max = 8,  disk = 100 }
      app      = { machine = "n2-standard-8",  min = 3, max = 10, disk = 100 }
      platform = { machine = "n2-standard-4",  min = 2, max = 3,  disk = 200 }
    }
  }
  pools = local.profiles[var.profile]
}

# ClickHouse 資料盤 StorageClass：Hyperdisk Balanced，佈建 15,000 IOPS / 1,200 MB/s（docs/13 §3.1）
resource "kubernetes_storage_class" "hyperdisk_balanced" {
  metadata { name = "hyperdisk-balanced" }
  storage_provisioner = "pd.csi.storage.gke.io"
  parameters = { type = "hyperdisk-balanced", provisioned-iops-on-create = "12000", provisioned-throughput-on-create = "800Mi" }
  volume_binding_mode = "WaitForFirstConsumer"
  allow_volume_expansion = true
}
resource "google_container_node_pool" "pools" {
  for_each = local.pools
  name = each.key  cluster = google_container_cluster.gke.id
  autoscaling { min_node_count = each.value.min  max_node_count = each.value.max }
  node_config {
    spot = each.key == "spot"
    machine_type = each.value.machine  image_type = "COS_CONTAINERD"  labels = { pool = each.key }  disk_size_gb = each.value.disk  disk_type = "pd-balanced"
    shielded_instance_config { enable_secure_boot = true }
    workload_metadata_config { mode = "GKE_METADATA" }
    boot_disk_kms_key = google_kms_crypto_key.data.id
  }
}

resource "google_sql_database_instance" "pg" {
  name = "dmp-pg-${var.env}"  database_version = "POSTGRES_16"  region = var.region  encryption_key_name = google_kms_crypto_key.data.id
  settings { tier = "db-custom-2-8192"  availability_type = "REGIONAL"  disk_size = 200  disk_type = "PD_SSD"
    ip_configuration { ipv4_enabled = false  private_network = google_compute_network.vpc.id }
    database_flags { name = "cloudsql.enable_pgvector"  value = "on" } }
}
resource "google_storage_bucket" "cold" {
  name = "dmp-cold-${var.project}"  location = var.region  uniform_bucket_level_access = true
  encryption { default_kms_key_name = google_kms_crypto_key.data.id }
  versioning { enabled = true }
}
resource "google_artifact_registry_repository" "dmp" { repository_id = "dmp"  location = var.region  format = "DOCKER" }
# Memorystore for Valkey、VPC-SC、Cloud Armor、IAP：見 modules/（略）
output "cluster" { value = google_container_cluster.gke.name }

resource "google_redis_cluster" "valkey" {   # Memorystore for Valkey 8 GB（standard-ha）
  name = "dmp-valkey"  region = var.region  shard_count = 1  replica_count = 1  node_type = "REDIS_STANDARD_SMALL"
  psc_configs { network = google_compute_network.vpc.id }
}
resource "google_storage_bucket" "sync" {   # 標籤／分群批次同步輸出（Parquet + manifest）
  name = "dmp-sync-${var.project}"  location = var.region  uniform_bucket_level_access = true
  encryption { default_kms_key_name = google_kms_crypto_key.data.id }
  lifecycle_rule { condition { age = 30 }  action { type = "Delete" } }
}
