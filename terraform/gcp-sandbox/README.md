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
terraform output -raw project_id
```

It takes about 15 minutes (most of it is GKE creating the cluster). Then
paste the **project id** into the dialog and press **Save & test**. Runtm
derives the bucket name from it and reads everything else from
`gs://<bucket>/runtm/cluster.json`. Terraform state stays in
`~/runtm-gcp-sandbox/terraform/gcp-sandbox` in your Cloud Shell home; keep it
to update or delete the stack later.

## Usage (Terraform)

```hcl
module "runtm_sandbox" {
  source = "git::https://github.com/runtm-ai/runtm.git//terraform/gcp-sandbox?ref=main"

  runtm_organization_id = "<org id>"
  project_id            = "<your project id>"
  region                = "us-west1"
}

output "runtm_connection" {
  value = module.runtm_sandbox.runtm_connection
}
```

One module instance per project. Pin `ref=` to a tag for reproducible
applies. The machine running Terraform needs `gcloud` (authenticated as you),
`kubectl`, `gke-gcloud-auth-plugin` and `curl` (Cloud Shell has all four): the
in-cluster setup runs `kubectl` through the cluster's DNS endpoint with your
credentials.

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
| VPC network + subnet | `runtm-sandbox` / `runtm-sandbox-<region>` | Custom mode, Private Google Access on. Nodes `subnet_cidr` (`10.77.0.0/20`), pods `pods_cidr` (`10.77.128.0/17`), Services `services_cidr` (`10.77.16.0/22`). |
| Cloud Router + Cloud NAT | `runtm-sandbox-nat` | Outbound internet for the private nodes and the sandboxes. |
| GKE Autopilot cluster | `runtm-sandbox` (regional) | RAPID channel, Pod Snapshots on, private nodes. The Kubernetes API's IP endpoint admits only `runtm_ingress_cidrs`; its DNS endpoint is Google IAM-authorized (the module's own setup uses it). |
| Node service account | `runtm-sandbox-gke-node` | Kubernetes Engine Default Node Service Account (project) and Artifact Registry Reader (repository). |
| Static IP | `runtm-sandbox-preview` (regional) | The preview router's load balancer. |
| Bucket | `runtm-sbx-<slug>-<project id>` | Hierarchical namespace, uniform access, public access prevented, soft delete off (Pod Snapshots requirements). `fs-snapshots/sessions/` expire after 90 days, `podsnapshots/backup/` after 30. Not deleted while it has objects. |
| Artifact Registry repository | `runtm-sandbox` (Docker, in `region`) | Runtm copies its sandbox image here. |
| Bucket grants to GKE | GKE node service agent: Storage Admin; GKE service agent: Storage Object User | Pod Snapshots write with short-lived per-path tokens (`federatedP4SA`) and delete expired snapshots. |
| Grants to Runtm's service account | Storage Object Admin (bucket), Artifact Registry Writer (repository) | Each scoped to the one resource it is needed on. |
| In the cluster | see below | Applied by `scripts/cluster-setup.sh`. |
| `runtm/cluster.json` in the bucket | | `endpoint`, `ca`, `cluster`, `namespace`, `router_ip`: how Runtm finds the cluster. Written last. |

`<slug>` is the first 12 hex characters of `sha256(organization id)`, the
same slug the organization has on AWS and Azure.

In the cluster:

| Object | Notes |
|---|---|
| [agent-sandbox](https://github.com/kubernetes-sigs/agent-sandbox) v1.0.9 | The sandbox controller (pinned release, checksum-verified). Its label allow-list (`agent-sandbox-system/agent-sandbox-config`) admits `runtm.com` and `podsnapshot.gke.io`. |
| Namespace `sandboxes` | Sandboxes run here. |
| `PodSnapshotStorageConfig` `runtm-snapshots` | `gs://<bucket>/podsnapshots`, `federatedP4SA`. |
| `PodSnapshotPolicy` `runtm-rootfs` | Root-filesystem snapshots on Runtm's trigger only, grouped per session, deleted after 90 days without a restore. |
| Preview router `agent-sandbox-system/sandbox-router` | Caddy (`router/Caddyfile`) behind a `LoadBalancer` Service on the static IP, `loadBalancerSourceRanges` = `runtm_ingress_cidrs`. Forwards a request to `<sandbox>.sandboxes.svc.cluster.local:<port>` named by two headers Runtm's preview edge sets and the router validates. |
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
| namespace `gke-managed-pod-snapshots` | pods | list |
| | pods/log | get |

No cluster-admin, no Secrets, nothing outside these namespaces. The last row
lets Runtm read each node's Pod Snapshot agent version after a GKE upgrade; if
your cluster refuses that grant the setup warns and continues, and Runtm
restores paused sessions from the workspace archive instead.

## Deployer permissions and prerequisites

- **Owner** on the project (or Kubernetes Engine Admin + Compute Network
  Admin + Service Account Admin + Service Account User + Project IAM Admin +
  Storage Admin + Artifact Registry Admin + Service Usage Admin).
- Billing enabled on the project.
- Enough **CPU quota** in the region for your concurrency (Autopilot sizes
  nodes to the sandboxes). Request more under *IAM & Admin → Quotas* before
  go-live.
- IAM grants take 1-2 minutes to propagate; Runtm's test retries.

## Inputs

| Name | Default | Description |
|---|---|---|
| `runtm_organization_id` | — | Your Runtm organization id. |
| `project_id` | — | Project to run sandboxes in. A dedicated project is recommended. |
| `region` | — | GCP region, e.g. `us-west1`. |
| `runtm_google_project` | Runtm production's | Project of Runtm's per-organization service accounts. Override only for Runtm staging. |
| `runtm_ingress_cidrs` | Runtm production's egress IPs | The only sources admitted to the Kubernetes API's IP endpoint and to the preview router. `/0` is refused. Override only for Runtm staging. |
| `subnet_cidr` | `10.77.0.0/20` | Node range. |
| `pods_cidr` | `10.77.128.0/17` | Pod range. |
| `services_cidr` | `10.77.16.0/22` | Service range. |
| `session_artifact_retention_days` | `90` | Lifetime of paused-session workspace archives. |
| `labels` | `{}` | Extra labels. |

## Outputs

`runtm_connection` bundles what Runtm uses (it derives all of it from the
project id and `runtm/cluster.json`): `project_id`, `region`, `bucket`,
`ar_repo`, `cluster`. Individual outputs of the same names, plus `router_ip`
and `runtm_service_account`.

## Deleting the stack

Delete your Runtm sandboxes first (or disconnect in Runtm), then
`terraform destroy`. It deletes the router's load balancer, the cluster and
everything else except the bucket, which is not deleted while it holds
objects (the twin of CloudFormation's `Retain`); empty it first if you mean
it.

## Requirements

Terraform ≥ 1.5, hashicorp/google ≥ 7.33, and `gcloud`, `kubectl`,
`gke-gcloud-auth-plugin`, `curl` on the machine running Terraform (Cloud
Shell has all of them).
