resource "google_compute_network" "nw1-vpc" {
  project                 = var.project_id
  name                    = "nw1-vpc"
  auto_create_subnetworks = false
  mtu                     = 1460
}


resource "google_compute_subnetwork" "nw1-subnet1" {
  name                     = "nw1-vpc-sub1-${var.region}"
  network                  = google_compute_network.nw1-vpc.id
  ip_cidr_range            = "10.10.1.0/24"
  region                   = var.region
  private_ip_google_access = true


}


resource "google_compute_subnetwork" "nw1-subnet2" {
  name                     = "nw1-vpc-sub3-us-west1"
  network                  = google_compute_network.nw1-vpc.id
  ip_cidr_range            = "10.10.2.0/24"
  region                   = var.sec_region
  private_ip_google_access = true


}


resource "google_compute_firewall" "nw1-ssh-icmp-allow" {
  name    = "nw1-vpc-ssh-allow"
  network = google_compute_network.nw1-vpc.id
  allow {
    protocol = "icmp"
  }
  allow {
    protocol = "tcp"
    ports    = ["22"]
  }
  source_ranges = ["39.33.11.48/32"]
  target_tags   = ["nw1-vpc-ssh-allow"]
  priority      = 1000
}



resource "google_compute_firewall" "nw1-internal-allow" {
  name    = "nw1-vpc-internal-allow"
  network = google_compute_network.nw1-vpc.id

  allow {
    protocol = "icmp"
  }


  allow {
    protocol = "udp"
    ports    = ["0-65535"]


  }
  allow {
    protocol = "tcp"
    ports    = ["0-65535"]
  }
  source_ranges = ["10.10.0.0/16"]
  priority      = 1100
}


resource "google_compute_firewall" "nw1-iap-allow" {
  name    = "nw1-vpc-iap-allow"
  network = google_compute_network.nw1-vpc.id

  allow {
    protocol = "icmp"
  }


  allow {
    protocol = "tcp"
    ports    = ["0-65535"]
  }
  source_ranges = ["35.235.240.0/20"]
  priority      = 1200
}



resource "google_compute_address" "natpip" {
  name   = "ipv4-address"
  region = var.sec_region
}


resource "google_compute_router" "router1" {
  name    = "nat-router1"
  region  = var.sec_region
  network = google_compute_network.nw1-vpc.id


  bgp {
    asn = 64514
  }
}


resource "google_compute_router_nat" "nat1" {
  name                               = "natgw1"
  router                             = google_compute_router.router1.name
  region                             = var.sec_region
  nat_ip_allocate_option             = "MANUAL_ONLY"
  nat_ips                            = [google_compute_address.natpip.self_link]
  source_subnetwork_ip_ranges_to_nat = "ALL_SUBNETWORKS_ALL_IP_RANGES"
  min_ports_per_vm                   = 256
  max_ports_per_vm                   = 512


  log_config {
    enable = true
    filter = "ERRORS_ONLY"
  }
}



resource "google_compute_global_address" "private_ip_address" {
  name          = google_compute_network.nw1-vpc.name
  purpose       = "VPC_PEERING"
  address_type  = "INTERNAL"
  prefix_length = 16
  network       = google_compute_network.nw1-vpc.name

}


resource "google_service_networking_connection" "private_vpc_connection" {


  network                 = google_compute_network.nw1-vpc.id
  service                 = "servicenetworking.googleapis.com"
  reserved_peering_ranges = [google_compute_global_address.private_ip_address.name]


}

#****************************Serverless VPC access FOR private ip address (VPC)************
#*************************************************Is need for Cloud Run*****************************************************
resource "google_vpc_access_connector" "private-cloud-sql" {
  project       = var.project_id
  name          = "private-cloud-sql"
  region        = var.region
  network       = google_compute_network.nw1-vpc.id
  machine_type  = "e2-micro"
  ip_cidr_range = "10.10.3.0/28"
  #min_instances = 2
  #max_instances = 10
  #subnet {
  #  name = google_compute_subnetwork.nw1-subnet1.name
  #}
}