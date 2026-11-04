# The network, NAT and edge router address became optional (bring your own
# network, previews on your own domain). Existing stacks keep them at index 0,
# so upgrading the module plans no changes to them.

moved {
  from = google_compute_network.sandboxes
  to   = google_compute_network.sandboxes[0]
}

moved {
  from = google_compute_subnetwork.sandboxes
  to   = google_compute_subnetwork.sandboxes[0]
}

moved {
  from = google_compute_router.nat
  to   = google_compute_router.nat[0]
}

moved {
  from = google_compute_router_nat.nat
  to   = google_compute_router_nat.nat[0]
}

moved {
  from = google_compute_address.router
  to   = google_compute_address.router[0]
}

# The in-cluster setup moved from scripts/cluster-setup.sh (local-exec) to the
# kubectl provider (k8s.tf). Forget the old runs without their destroy-time
# cleanup, which called the deleted script; the provider adopts the objects
# they created (server-side apply, conflicts forced).

removed {
  from = terraform_data.cluster_setup
  lifecycle {
    destroy = false
  }
}

removed {
  from = terraform_data.router_lb_release
  lifecycle {
    destroy = false
  }
}
