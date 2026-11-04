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
