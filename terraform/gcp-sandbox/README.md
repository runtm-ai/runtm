# runtm/gcp-sandbox

Terraform module for running Runtm Cloud sandboxes in your own Google Cloud
project. Each sandbox is a gVisor pod on a GKE Autopilot cluster in your
project. The module creates the cluster and its network, a bucket for
workspace archives and snapshots, an Artifact Registry repository for the
sandbox image, a preview router that only Runtm can reach, and the grants
your organization's Runtm service account needs. Nothing else.

## One command (Cloud Shell)

Open [Cloud Shell](https://shell.cloud.google.com/?show=terminal) in the
project you want to use and paste the command the Runtm dialog shows
(**Integrations → Google Cloud Sandboxes**). It has your organization id and
region filled in:

```sh
PROJECT_ID=$(gcloud config get-value project) && \
git clone --depth 1 --branch main https://github.com/runtm-ai/runtm.git runtm-gcp-sandbox && \
cd runtm-gcp-sandbox/terraform/gcp-sandbox && \
terraform init -input=false && \
terraform apply -auto-approve \
  -var "project_id=$PROJECT_ID" \
  -var runtm_organization_id=<org id> \
  -var region=us-west1 && \
terraform output -raw cluster
```

It takes about 15 minutes (most of it is GKE creating the cluster). Then
paste the printed **cluster** (its resource name,
`projects/<project>/locations/<region>/clusters/runtm-sandbox-<label>`) into
the dialog and press **Save & test**. It names this stack's cluster even when
the project holds others; Runtm derives the stack's other names from it and
reads the cluster's address from `gs://<bucket>/runtm/cluster.json`.
Terraform state stays in `~/runtm-gcp-sandbox/terraform/gcp-sandbox` in your
Cloud Shell home (`~/runtm-gcp-sandbox-<suffix>/…` for a stack with a label,
so each stack keeps its own state); keep it to update or delete the stack
later.

## Usage (Terraform)

```hcl
module "runtm_sandbox" {
  source = "git::https://github.com/runtm-ai/runtm.git//terraform/gcp-sandbox?ref=main"

  runtm_organization_id = "<org id>"
  project_id            = "<your project id>"
  region                = "us-west1"
}

# Paste this output into Runtm (Integrations -> Google Cloud).
output "cluster" {
  value = module.runtm_sandbox.cluster
}
```

Every resource name carries a slug derived from the organization id, so several
organizations (or several Runtm environments) can share one project. Add an
optional `suffix` for a human-readable label, and enter the same value in the
dialog:

```hcl
module "runtm_sandbox" {
  source = "git::https://github.com/runtm-ai/runtm.git//terraform/gcp-sandbox?ref=main"

  runtm_organization_id = "<org id>"
  project_id            = "<your project id>"
  region                = "us-west1"
  suffix                = "pci" # cluster runtm-sandbox-pci-<slug>
}
```

One module instance per region. Pin `ref=` to a tag for reproducible
applies. The machine running Terraform needs only Terraform and Google
credentials (whatever the google provider uses: a service account, Workload
Identity, `gcloud auth application-default login`). The in-cluster objects are
applied by the [`alekc/kubectl`](https://registry.terraform.io/providers/alekc/kubectl)
provider through the cluster's DNS endpoint with those same credentials, in
the same apply that creates the cluster. The module configures that provider
itself, so it cannot be used with `count` or `for_each`.

### Identity

Runtm operates one Google service account per organization:
`runtm-aws-<first 20 hex of sha256(org id)>@kuimjyxqkrbfvalw-orgids.iam.gserviceaccount.com`
(the same account the AWS module trusts). The module computes it from the org
id and grants it access in your project; Runtm impersonates it for
short-lived access tokens. No key or secret is created anywhere. Open the
Runtm dialog before you apply: it creates that account, and Google refuses a
grant to an account that does not exist yet.

That account gets **no project-level role**. It may use objects in the
bucket, push to the repository, and use the cluster through Kubernetes RBAC
(see [What Runtm can do in the cluster](#what-runtm-can-do-in-the-cluster)).

## What it creates

| Resource | Name | Notes |
|---|---|---|
| APIs | container, compute, storage, artifactregistry, iam, cloudresourcemanager | Never disabled on destroy. |
| VPC network + subnet | `runtm-sandbox-<label>-<region>` / `runtm-sandbox-<label>` | Custom mode, Private Google Access on. Nodes `subnet_cidr` (`10.77.0.0/20`), pods `pods_cidr` (`10.77.128.0/17`), Services `services_cidr` (`10.77.16.0/22`). Skipped when you [bring your own network](#bring-your-own-network). |
| Cloud Router + Cloud NAT | `runtm-sandbox-nat-<label>` | Outbound internet for the private nodes and the sandboxes. `nat = "existing"` or `"none"` skips it. |
| GKE Autopilot cluster | `runtm-sandbox-<label>` (regional) | RAPID channel, Pod Snapshots on, private nodes. The Kubernetes API's IP endpoint admits only `runtm_ingress_cidrs`; its DNS endpoint is Google IAM-authorized (the module's kubectl provider uses it). |
| Node service account | `rtm-node-<slug><disc>` | Kubernetes Engine Default Node Service Account (project) and Artifact Registry Reader (repository). |
| Static IP | `runtm-sandbox-preview-<label>` (regional) | The preview router's load balancer. With [`preview`](#previews-on-your-own-domain), the Gateway's address `runtm-sandbox-gw-<label>` instead. |
| Bucket | `runtm-sandbox-<label>-<disc>` | Hierarchical namespace, uniform access, public access prevented, soft delete off (Pod Snapshots requirements). `fs-snapshots/sessions/` expire after 90 days, `podsnapshots/backup/` after 30. Not deleted while it has objects. |
| Artifact Registry repository | `runtm-sandbox-<label>` (Docker, in `region`) | Runtm copies its sandbox image here. |
| Bucket grants to GKE | GKE node service agent: Storage Admin; GKE service agent: Storage Object User | Pod Snapshots write with short-lived per-path tokens (`federatedP4SA`) and delete expired snapshots. |
| Grants to Runtm's service account | Storage Object Admin (bucket), Artifact Registry Writer (repository) | Each scoped to the one resource it is needed on. |
| In the cluster | see below | Applied by the kubectl provider (`k8s.tf`), one resource per object. |
| `runtm/cluster.json` in the bucket | | `endpoint`, `ca`, `cluster`, `namespace`, `router_ip`: how Runtm finds the cluster. With `preview`, `preview` (domain, exposure, address) replaces `router_ip`; with `control_plane_access = "dns"`, `dns_endpoint` is added. Written last. |

`<label>` is `<suffix>-<slug>` or `<slug>`; `<slug>` is the first 12 hex
characters of `sha256(organization id)`, the same slug the organization has on
AWS and Azure; `<disc>` is the first 9 hex characters of
`sha256("<org>|<suffix>|<region>|<project>")`, needed because a service
account id cannot hold the label and the region, and bucket names are global
and the bucket outlives the stack.

## Name lengths

Worst case: `suffix` 10, slug 12, region 23 (`northamerica-northeast1`). The
region is in a name only where the name's scope spans regions (network,
service account, bucket); the cluster is named within its location and the
other resources are regional.

| Name | Formula | Worst case | GCP limit |
|---|---|---|---|
| GKE cluster | `runtm-sandbox-<label>` | 37 | **40** |
| VPC network | `runtm-sandbox-<label>-<region>` | 61 | 63 |
| Subnet | `runtm-sandbox-<label>` | 37 | 63 |
| Cloud Router / NAT | `runtm-sandbox-nat-<label>` | 41 | 63 |
| Static IP | `runtm-sandbox-preview-<label>` | 45 | 63 |
| Artifact Registry repository | `runtm-sandbox-<label>` | 37 | 63 |
| Node service account | `rtm-node-<slug><disc>` | **30** | 30 |
| Bucket | `runtm-sandbox-<label>-<disc>` | 47 | 63 |
| Gateway address (`preview`) | `runtm-sandbox-gw-<label>` | 40 | 63 |
| Proxy-only subnet (`preview`) | `runtm-sandbox-proxy-<label>` | 43 | 63 |
| Firewall rule (`preview`) | `rtm-sbx-gw-<label>-<region>` | 58 | 63 |
| Certificate / DNS authorization (`preview`) | `runtm-sandbox-<label>` | 37 | 63 |

The `suffix` cap is 10, the same as `aws-sandbox` and `azure-sandbox`, so one
value entered in the Runtm dialog works on every cloud.

In the cluster:

| Object | Notes |
|---|---|
| [agent-sandbox](https://github.com/kubernetes-sigs/agent-sandbox) v1.0.9 | The sandbox controller (pinned release, checksum-verified). Its label allow-list (`agent-sandbox-system/agent-sandbox-config`) admits `runtm.com` and `podsnapshot.gke.io`. |
| Namespace `sandboxes` | Sandboxes run here. |
| `PodSnapshotStorageConfig` `runtm-snapshots` | `gs://<bucket>/podsnapshots`, `federatedP4SA`. |
| `PodSnapshotPolicy` `runtm-rootfs` | Root-filesystem snapshots on Runtm's trigger only, grouped per session, deleted after 90 days without a restore. |
| Preview router `agent-sandbox-system/sandbox-router` | Runtm's public preview proxy image (`RUNTM_PROXY_MODE=edge`) behind a `LoadBalancer` Service on the static IP, `loadBalancerSourceRanges` = `runtm_ingress_cidrs`. Forwards a request to `<sandbox>.sandboxes.svc.cluster.local:<port>` named by two headers Runtm's preview edge sets and the router validates. With `preview`, see [Previews on your own domain](#previews-on-your-own-domain). |
| `NetworkPolicy` `sandboxes/runtm-egress-allowed` | Only with `sandbox_egress_allowed_cidrs`. See [Sandbox egress](#sandbox-egress). |
| RBAC for Runtm's service account | Below. |

### What Runtm can do in the cluster

| Where | Resources | Verbs |
|---|---|---|
| namespace `sandboxes` | sandboxclaims | get, list, watch, create, delete, patch |
| | sandboxtemplates, sandboxwarmpools | get, create, patch |
| | sandboxes | get |
| | pods | get, list, delete |
| | pods/exec | create, get |
| | events | list |
| | services | get |
| | podsnapshots | get, list, delete |
| | podsnapshotmanualtriggers | create, get, delete |
| | podsnapshottokenrequests | patch, delete |
| cluster | nodes | get, list |
| | podsnapshotstorageconfigs | get |
| namespace `agent-sandbox-system` | configmaps | get |

No cluster-admin, no Secrets, nothing outside these namespaces. Runtm gets no
access to the GKE-managed namespace `gke-managed-pod-snapshots` (Autopilot
refuses any write there), so after a GKE upgrade it restores paused sessions
from the workspace archive.

## Bring your own network

By default the module creates a dedicated VPC, subnet and Cloud NAT. To run
the cluster in a network you already operate:

| You have | Set | The module |
|---|---|---|
| Nothing (default) | | creates the VPC, subnet and NAT |
| A VPC | `network` | creates its subnet (`subnet_cidr`, `pods_cidr`, `services_cidr`) in your VPC |
| A subnet | `subnetwork`, `pods_range_name`, `services_range_name` | uses it; creates no subnet |
| A Shared VPC | the above plus `network_project_id` (the host project) | looks the network up in the host project |

`network` and `subnetwork` take a name, an id (`projects/<p>/regions/<r>/subnetworks/<n>`)
or a self link. An existing subnet must be in `region`, have **Private Google
Access** on (nodes pull the sandbox image and write snapshots over it) and
have the two named secondary ranges (about /17 for pods, /22 for Services).
The plan fails with a clear message otherwise.

**Outbound internet (`nat`).** Sandboxes need it to install packages and reach
Runtm.

- `create` (default): a Cloud Router and NAT. In your VPC it covers only the
  sandbox subnet, all of its ranges, so it never claims a subnet another NAT
  serves.
- `existing`: your Cloud NAT already covers this subnet. It must cover the
  **pods range, not only the primary range** (otherwise nodes are healthy but
  sandboxes have no internet), and needs enough ports: many sandboxes share a
  node, so use dynamic port allocation with a minimum of 256 ports per VM.
- `none`: egress leaves through your own appliance or proxy.

**Shared VPC prerequisites** (the module cannot grant these in a host project
it does not own): on the subnet, `roles/compute.networkUser` for the cluster
project's GKE service agent (`service-<number>@container-engine-robot.iam.gserviceaccount.com`)
and Google APIs service agent (`<number>@cloudservices.gserviceaccount.com`); on
the host project, `roles/container.hostServiceAgentUser` for the GKE service
agent. GKE cannot create firewall rules in a host network: create the rule for
the control plane to reach nodes and, with `preview`, the one below, then set
`manage_firewall_rules = false`.

**Kubernetes API (`control_plane_access`).** `ip` (default): the IP endpoint,
admitting only `runtm_ingress_cidrs`. `dns`: the IP endpoint is turned off and
Runtm uses the DNS endpoint (`*.gke.goog`), authorized by Google IAM, so no
public IP or allowlist remains. `dns` requires Runtm's support for DNS
endpoints.

## Sandbox egress

Sandboxes reach only public addresses: the NetworkPolicy agent-sandbox manages
for every sandbox blocks `10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16` and
link-local. Running in your VPC therefore does not expose your other
workloads. To let agents reach specific internal services, list their ranges
in `sandbox_egress_allowed_cidrs`; the module adds one NetworkPolicy allowing
them (all ports) on top.

## Previews on your own domain

By default Runtm's preview edge reaches the router through an external load
balancer allowlisted to Runtm, and previews are served on Runtm's domain. Set
`preview` to serve them from your project instead:

```hcl
preview = {
  domain   = "runtm.example.com"                 # https://<port>-<sandbox>.runtm.example.com
  exposure = "internal"                          # internal (VPN / Interconnect) | external
  dns_zone = "projects/net-prj/managedZones/example" # optional
}
```

- **Load balancer:** a regional GKE Gateway, `gke-l7-rilb` (internal, on an
  address in the sandbox subnet) or `gke-l7-regional-external-managed`
  (external, Standard tier). TLS ends at the load balancer. Long-lived
  connections (WebSocket HMR, SSE) get a one-hour backend timeout. An external
  Gateway can take a regional Cloud Armor policy (`security_policy`).
- **Proxy-only subnet:** regional load balancers need one per region per VPC
  (`proxy_only_subnet_cidr`, default `10.77.24.0/23`). If your VPC already has
  one in the region, set `proxy_only_subnet = "existing"` and its range in
  `proxy_only_subnet_cidr`.
- **Firewall:** the module allows the proxy-only subnet and Google's health
  checkers (`35.191.0.0/16`, `130.211.0.0/22`) to reach the router pods on TCP
  8080 (destination: the pods range). `manage_firewall_rules = false` leaves it
  to you.
- **DNS:** with `dns_zone` (public or private, any project), the module creates
  `*.<domain>` and the certificate's authorization record. Without it, create
  the records listed in the `preview_dns_records` output.
- **Certificate (`certificate.mode`):**
  - `google_managed` (default): Certificate Manager issues `*.<domain>`. It is
    validated by a DNS authorization record that must resolve on the **public**
    internet, so a domain only in a private zone needs the record published
    publicly too, or a private CA: set `issuance_config` to a Certificate Manager
    issuance config backed by Certificate Authority Service.
  - `certificate_manager`: an existing regional certificate you manage
    (uploaded, or issued by your CA), by name or id in `project_id` / `region`.
  - `pem`: pass `pem_certificate` and `pem_private_key`. The key ends up in
    Terraform state; prefer `certificate_manager` when the key must stay out
    of it.
- **Router:** two replicas of Runtm's preview proxy image, the one Runtm runs
  for its own sandboxes (Caddy with caddy-jwt), from Runtm's public
  repository `us-west1-docker.pkg.dev/kuimjyxqkrbfvalw-public/runtm-public/sandbox-preview-proxy:latest`. It is
  pulled on every start, so a restart picks up Runtm's current build, and is
  reachable over Private Google Access (no NAT needed). The module runs it in
  its customer mode (`RUNTM_PROXY_MODE=customer`) and configures it with
  environment variables only: no config file in this module. `router_image`
  overrides the image, e.g. with a copy in your own registry.
- **Access:** nothing upstream vouches for the visitor, so the router checks
  Runtm's preview token itself (the image's customer mode,
  `proxy-gke/Caddyfile.customer` in Runtm's repository): an ES256/RS256 JWT
  verified against Runtm's published keys (`runtm_preview_jwks_url`), whose
  audience is your domain and whose `lbl` claim must equal the host's
  `<port>-<sandbox>`. No secret is shared with your cluster. A visitor without
  a valid token is sent to Runtm to sign in and comes back through
  `/__runtm/auth`, which stores the token in a cookie for that one host. Only
  `runtm_app_origin` may embed a preview.

`preview` requires Runtm's support for customer preview domains (signing
preview tokens with a published key). Until Runtm publishes those keys,
requests that carry a token wait while the router retries fetching them.

## Deployer permissions and prerequisites

- **Owner** on the project (or Kubernetes Engine Admin + Compute Network
  Admin + Service Account Admin + Service Account User + Project IAM Admin +
  Storage Admin + Artifact Registry Admin + Service Usage Admin).
- Billing enabled on the project.
- Enough **CPU quota** in the region for your concurrency (Autopilot sizes
  nodes to the sandboxes). Request more under *IAM & Admin → Quotas* before
  go-live.
- IAM grants take 1-2 minutes to propagate; Runtm's test retries.
- With `preview`: Certificate Manager Editor and, with `dns_zone`, DNS
  Administrator on the zone's project (Owner covers both in one project).

## Inputs

| Name | Default | Description |
|---|---|---|
| `runtm_organization_id` | — | Your Runtm organization id. |
| `project_id` | — | Project to run sandboxes in. A dedicated project is recommended. |
| `region` | — | GCP region, e.g. `us-west1`. |
| `suffix` | `""` | Optional label in front of the org slug in every resource name (`pci`, `ops`); 1-10 lowercase chars. |
| `runtm_google_project` | Runtm production | Leave unset. Runtm staff only, for testing a stack against Runtm staging. |
| `runtm_ingress_cidrs` | Runtm production's egress IPs | The only sources admitted to the Kubernetes API's IP endpoint and to the edge preview router. `/0` is refused. Override only for Runtm staging. Unused with `control_plane_access = "dns"` and `preview`. |
| `network` | `null` | Existing VPC for the module's subnet. See [Bring your own network](#bring-your-own-network). |
| `subnetwork` | `null` | Existing subnet for the cluster. |
| `network_project_id` | `project_id` | Shared VPC host project. |
| `pods_range_name` / `services_range_name` | `pods` / `services` | Secondary range names (yours, with `subnetwork`). |
| `nat` | `create` | `create`, `existing` or `none`. |
| `control_plane_access` | `ip` | `ip` (allowlisted IP endpoint) or `dns` (DNS endpoint only). |
| `subnet_cidr` | `10.77.0.0/20` | Node range (subnet the module creates). |
| `pods_cidr` | `10.77.128.0/17` | Pod range (subnet the module creates). |
| `services_cidr` | `10.77.16.0/22` | Service range (subnet the module creates). |
| `sandbox_egress_allowed_cidrs` | `[]` | Private ranges sandboxes may reach. See [Sandbox egress](#sandbox-egress). |
| `agent_sandbox_manifest` | v1.0.9 release on GitHub | URL and sha256 of the agent-sandbox release, fetched when planning. Point it at a mirror if the runner cannot reach GitHub. |
| `preview` | `null` | Previews on your own domain. See [Previews on your own domain](#previews-on-your-own-domain). |
| `manage_firewall_rules` | `true` | Create the preview load balancer's firewall rule. |
| `router_image` | per mode | Preview router image. |
| `runtm_app_origin` | Runtm production | Where the preview router sends visitors to sign in. Override only for Runtm staging. |
| `runtm_preview_jwks_url` | `<runtm_app_origin>/.well-known/runtm-preview-jwks.json` | Runtm's preview-token keys. |
| `session_artifact_retention_days` | `90` | Lifetime of paused-session workspace archives. |
| `labels` | `{}` | Extra labels. |

## Outputs

`cluster` is the value to paste into Runtm: the cluster's resource name,
`projects/<project>/locations/<region>/clusters/<name>`. `runtm_connection`
bundles every name the stack uses (Runtm derives all of it from `cluster` and
reads the address from `runtm/cluster.json`): `cluster`, `project_id`,
`region`, `suffix`, `cluster_name`, `network`, `subnet`, `nat_router`,
`router_ip_name`, `node_service_account`, `bucket`, `ar_repo`. Individual
outputs `project_id`, `region`, `bucket`, `ar_repo`, `cluster_name`,
`router_ip`, `runtm_service_account` and `dns_endpoint`; with `preview`, also
`preview_url_pattern` and `preview_dns_records`.

## Deleting the stack

Delete your Runtm sandboxes first (or disconnect in Runtm), then
`terraform destroy`. It deletes the router's load balancer (Service or Gateway), the cluster and
everything else except the bucket, which is not deleted while it holds
objects (the twin of CloudFormation's `Retain`); empty it first if you mean
it.

## Requirements

Terraform ≥ 1.7, hashicorp/google ≥ 7.33, alekc/kubectl 3.x, and Google
credentials on the machine running Terraform. No `gcloud`, `kubectl` or auth
plugin. The identity applying needs `roles/container.admin` (creating the
cluster takes it anyway): it reaches the DNS endpoint and creates RBAC in the
cluster.
