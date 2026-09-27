"""Deploy Aperture to the local minikube cluster, offline.

    uv run python scripts/k8s.py up      copy images into the cluster, helm upgrade --install, wait
    uv run python scripts/k8s.py down    helm uninstall (keeps the cluster and the database volume)

One-time bootstrap (networked, pulls the node image, Kubernetes and Calico):
    minikube start -p aperture --driver=docker --cpus=6 --memory=12g --cni=calico

Images come from the local Docker store (`docker compose build`) and are copied into the node with
`minikube image load`; nothing is ever pulled. Each is tagged with its content digest first, so a
changed image gets a new tag (its pods roll) and an unchanged one is never copied twice.
"""

import ipaddress
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CHART = ROOT / "helm" / "aperture"
PROFILE = NAMESPACE = RELEASE = "aperture"
# values.yaml `images.<key>` -> local image
IMAGES = {
    "db": "timescale/timescaledb-ha:pg17",
    "loader": "aperture/loader:dev",
    "backend": "aperture/backend:dev",
    "mcpServer": "aperture/mcp-server:dev",
    "llmRelay": "aperture/llm-relay:dev",
    "tiles": "aperture/tiles:dev",
    "frontend": "aperture/frontend:dev",
}
DEPLOYMENTS = ("backend", "mcp-server", "llm-relay", "tiles", "frontend")
PROBE_IMAGE = "curlimages/curl:latest"  # sha256:58adaa4e... (bootstrap/images.lock)
INSTALL_HINT = {
    "minikube": "winget install Kubernetes.minikube",
    "helm": "winget install Helm.Helm",
    "kubectl": "ships with Docker Desktop",
}


def tool(name: str) -> str:
    path = shutil.which(name)
    if not path:
        sys.exit(
            f"{name} not found on PATH ({INSTALL_HINT[name]}; open a new shell after installing)"
        )
    return path


def kubectl(*args: str) -> list[str]:
    return [tool("kubectl"), "--context", PROFILE, "-n", NAMESPACE, *args]


def minikube(*args: str) -> list[str]:
    return [tool("minikube"), "-p", PROFILE, *args]


def out(cmd: list[str]) -> str:
    return subprocess.run(cmd, capture_output=True, text=True, check=False).stdout.strip()


def run(cmd: list[str]) -> bool:
    print("$ " + " ".join([Path(cmd[0]).stem, *cmd[1:]]), flush=True)
    return subprocess.run(cmd, cwd=ROOT, check=False).returncode == 0


def content_tag(ref: str) -> str:
    """Tag the local image with its content id: <repo>:<tag>-<id12>. Drops older such tags.

    The id is the linux/amd64 manifest digest, not the image id: BuildKit's provenance
    attestation (with a build timestamp) is part of the index, so every rebuild changes the image
    id even when the image itself is byte-identical."""
    repo, _, tag = ref.rpartition(":")
    fmt = "{{.Descriptor.Digest}}"
    image_id = out(
        ["docker", "image", "inspect", "--platform", "linux/amd64", "--format", fmt, ref]
    )
    if not image_id.startswith("sha256:"):
        sys.exit(
            f"local image {ref} missing: run `docker compose build` (or bootstrap for base images)"
        )
    pinned = f"{tag}-{image_id.removeprefix('sha256:')[:12]}"
    subprocess.run(["docker", "tag", ref, f"{repo}:{pinned}"], check=True)
    stale = re.compile(rf"^{re.escape(tag)}-[0-9a-f]{{12}}$")
    for t in out(["docker", "image", "ls", repo, "--format", "{{.Tag}}"]).split():
        if stale.match(t) and t != pinned:
            subprocess.run(["docker", "rmi", f"{repo}:{t}"], capture_output=True, check=False)
    return f"{repo}:{pinned}"


def node_images() -> set[str]:
    listed = json.loads(out(minikube("image", "ls", "--format", "json")) or "[]")
    return {t.removeprefix("docker.io/") for i in listed for t in i["repoTags"]}


def load_images(refs: list[str]) -> dict[str, str]:
    """Copy each local image into the node (if not already there) under its content tag."""
    present = node_images()
    pinned = {}
    for ref in refs:
        tagged = content_tag(ref)
        if tagged in present:
            print(f"  {tagged}: already in the cluster")
        else:
            print(f"  {tagged}: loading into the cluster...", flush=True)
            subprocess.run(minikube("image", "load", tagged), check=True)
        pinned[ref] = tagged
    return pinned


def host_ip() -> str:
    """LM Studio's host as the node sees it (Docker Desktop's host gateway)."""
    line = out(minikube("ssh", "--", "getent hosts host.minikube.internal"))
    ip = line.split()[0] if line else ""
    ipaddress.IPv4Address(ip)
    return ip


def up() -> bool:
    if "Running" not in out(minikube("status", "--format", "{{.APIServer}}")):
        sys.exit(f"minikube profile {PROFILE} is not running: minikube start -p {PROFILE}")
    print("images:", flush=True)
    images = load_images([*IMAGES.values(), PROBE_IMAGE])
    sets = [f"images.{key}={images[ref]}" for key, ref in IMAGES.items()]
    sets.append(f"llm.hostIP={host_ip()}")
    ok = run([
        tool("helm"), "upgrade", "--install", RELEASE, str(CHART), "--kube-context", PROFILE,
        "-n", NAMESPACE, "--create-namespace", *(a for s in sets for a in ("--set", s)),
    ])  # fmt: skip
    if not ok:
        return False
    # The database first, then this revision's one-shot load (~3 min on an empty volume; seconds
    # when the data is already there), then every Deployment's new ReplicaSet fully rolled out.
    status = json.loads(out([tool("helm"), "status", RELEASE, "--kube-context", PROFILE,
                             "-n", NAMESPACE, "-o", "json"]) or "{}")  # fmt: skip
    ok &= run(kubectl("rollout", "status", "statefulset/db", "--timeout=10m"))
    ok &= run(kubectl("wait", "--for=condition=complete", f"job/loader-r{status.get('version')}",
                      "--timeout=45m"))  # fmt: skip
    for deploy in DEPLOYMENTS:
        ok &= run(kubectl("rollout", "status", f"deployment/{deploy}", "--timeout=10m"))
    return ok


def down() -> bool:
    return run([tool("helm"), "uninstall", RELEASE, "--kube-context", PROFILE, "-n", NAMESPACE])


if __name__ == "__main__":
    commands = {"up": up, "down": down}
    if len(sys.argv) != 2 or sys.argv[1] not in commands:
        sys.exit(__doc__)
    sys.exit(0 if commands[sys.argv[1]]() else 1)
