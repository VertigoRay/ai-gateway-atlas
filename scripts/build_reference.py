"""Prepare an allowlisted configuration snapshot and render it with Jekyll.

Only the two reference HTML pages are published. This build has no vault client,
network health probe or access to the deployed broker's credential files.
"""

import argparse
import base64
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
from datetime import UTC, datetime
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit

REPOSITORY = Path(__file__).resolve().parents[1]
REFERENCE = Path("docs/reference")
INPUTS = {
    "catalog": REFERENCE / "contracts.json",
    "access": Path("gateway/access-groups.json"),
    "devices": REFERENCE / "device-access.json",
    "presentation": REFERENCE / "presentation.json",
}
OUTPUTS = ("index.html", "examples.html")


def display_value(value):
    return {
        "text": value,
        "placeholder": value.startswith("<") and value.endswith(">"),
        "template": "your-" in value or "<" in value or "example.com" in value,
    }


def field_data(field):
    # Contract files contain field definitions, never vault values. Fail closed
    # if a secret field is accidentally given a copyable example or exact value.
    if field.get("secret") and (
        not re.fullmatch(r"<[^<>\r\n]+>", field["example"])
        or "exact" in field or field.get("alternatives")
    ):
        raise ValueError("Secret field documentation must contain only a placeholder")
    result = {key: field[key] for key in (
        "location", "name", "type", "required", "label", "description"
    )}
    result["example"] = display_value(field["example"])
    result["format_notes"] = (["Use this exact value."] if "exact" in field else [])
    if "format" in field:
        result["format_notes"].append(field["format"])
    result["alternatives"] = [display_value(value) for value in field.get("alternatives", [])]
    return result


def group_allows_service(access, group_id, service_id):
    group = access["groups"][group_id]
    if not set(access["service_tags"].get(service_id, [])) & set(group["allow_tags"]):
        return False
    restriction = access.get("service_access", {}).get(service_id)
    return restriction is None or (
        group["owner"] == restriction["owner"] and group_id in restriction["allow_groups"]
    )


def credential_access(catalog, access, service_id):
    restriction = access.get("service_access", {}).get(service_id)
    if restriction is None:
        return None
    owner = access["owners"][restriction["owner"]]["label"]
    groups = ", ".join(access["groups"][key]["label"] for key in restriction["allow_groups"])
    exceptions = ", ".join(
        catalog.get("clientLabels", {}).get(client["name"], client["name"])
        for client in restriction["allow_clients"]
    )
    categories = ", ".join("#" + tag for tag in access["service_tags"].get(service_id, [])[:1])
    return {
        "owner": restriction["owner"],
        "note": (
            f"Credential owner: {owner} (personal credentials). "
            + (f"Group access requires both a selected tool route and membership in {groups} "
               f"with owner {owner}. " if groups else "No device group is approved for this credential. ")
            + f"The {categories} category alone does not grant access to these credentials."
            + (f" Explicit client exceptions: {exceptions}; each is approved for its exact enrolled key."
               if exceptions else " No additional client exceptions are approved.")
        ),
    }


def service_data(catalog, access, service):
    if not re.fullmatch(r"[a-z][a-z0-9-]*", service["id"]):
        raise ValueError("Invalid service reference ID")
    model = service["kind"] in ("model", "connection")
    result = {key: service[key] for key in ("id", "kind", "name", "note", "rules")}
    result.update({
        "subtitle": (service.get("hostname", "") + " · " if "hostname" in service else "")
                    + service["summary"],
        "badge": f"CT {service['ct']}" if "ct" in service else (
            "Pending" if service["kind"] == "pending" else "Example"),
        "tags": access["service_tags"].get(service["id"], []),
        "credential_access": credential_access(catalog, access, service["id"]),
        "fields": [field_data(field) for field in service["fields"]],
        "table_label": "Connection setting" if model else "Bitwarden field",
        "middle_label": "How it connects" if model else "What to provide",
        "value_label": "Configured value / reference" if model else "Value / expected format",
        "table_purpose": "connection reference" if model else "Bitwarden fields",
    })
    return result


def source_files(repository=REPOSITORY, input_directory=None):
    files = {
        key: (input_directory / path.name if input_directory else repository / path)
        for key, path in INPUTS.items()
    }
    for key in ("style.css", "copy.js"):
        files[key] = repository / REFERENCE / key
    files["build_reference.py"] = repository / "scripts/build_reference.py"
    site = repository / REFERENCE / "site"
    for path in sorted(site.rglob("*")):
        if path.is_file():
            files["site/" + path.relative_to(site).as_posix()] = path
    return files


def prepare_data(files):
    catalog, access, devices, presentation = (
        json.loads(files[key].read_text()) for key in INPUTS
    )
    if (catalog["schemaVersion"], access["version"], devices["version"], presentation["version"]) != (1, 1, 1, 1):
        raise ValueError("Unsupported reference schema")
    tool_services = {s["id"]: s for s in catalog["services"] if s["kind"] == "service"}
    if set(access["service_tags"]) - set(tool_services):
        raise ValueError("Only configured tool services may carry access tags")
    if set(access.get("service_access", {})) - set(tool_services):
        raise ValueError("Credential access restrictions must name configured tool services")
    for device in devices["devices"]:
        group = access["groups"][device["group"]]
        if device["owner"] != group["owner"] or device["owner"] not in access["owners"]:
            raise ValueError("Device ownership must match its access group")
        if device.get("reply") and not re.fullmatch(
            r"/downloads/replies/codex-[A-Za-z0-9][A-Za-z0-9_.-]*\.reply\.json", device["reply"]
        ):
            raise ValueError("Only named public certificate reply links are supported")
    monitoring = urlsplit(presentation["monitoring_url"])
    if monitoring.scheme != "https" or not monitoring.hostname or monitoring.username or monitoring.password or monitoring.query or monitoring.fragment:
        raise ValueError("Monitoring URL must be HTTPS without credentials, query or fragment")
    if presentation["setup_url"] and not re.fullmatch(r"/[A-Za-z0-9/_-]+", presentation["setup_url"]):
        raise ValueError("Device setup link must be a local path")
    monitoring_links = []
    for link in presentation["monitoring_links"]:
        target = urlsplit(link["url"])
        if (target.scheme != "https" or target.hostname != monitoring.hostname
                or target.port != monitoring.port or target.username or target.password or target.query):
            raise ValueError("Monitoring links must stay on the configured HTTPS monitoring host")
        monitoring_links.append({"label": link["label"], "url": link["url"]})
    groups = []
    for group_id, group in access["groups"].items():
        groups.append({
            "id": group_id, "label": group["label"],
            "owner_label": access["owners"][group["owner"]]["label"],
            "tags": ", ".join("#" + tag for tag in group["allow_tags"]),
            "services": [{"id": key, "name": tool_services[key]["name"]}
                         for key in access["service_tags"] if group_allows_service(access, group_id, key)],
            "devices": [{key: device[key] for key in ("hostname", "status", "note", "models", "reply")
                         if key in device} for device in devices["devices"] if device["group"] == group_id],
        })
    script = files["copy.js"].read_text()
    return {
        "organization": catalog["organization"], "collection": catalog["collection"],
        "presentation": {key: presentation[key] for key in (
            "brand", "brand_mark", "eyebrow", "monitoring_url", "monitoring_label", "permissions_note", "domain_template_prefix", "setup_url"
        )},
        "services": [service_data(catalog, access, s) for s in catalog["services"]
                     if s["kind"] in ("service", "pending")],
        "examples": [service_data(catalog, access, s) for s in catalog["services"] if s["kind"] == "example"],
        "models": [service_data(catalog, access, s) for s in catalog.get("models", [])],
        "connections": [service_data(catalog, access, s) for s in catalog.get("connections", [])],
        "groups": groups, "devices_updated": devices["updated"],
        "monitoring_links": monitoring_links,
        "css": files["style.css"].read_text(), "copy_script": script,
        "script_hash": base64.b64encode(hashlib.sha256(script.encode()).digest()).decode(),
    }


def source_digest(files):
    digest = hashlib.sha256()
    for name, path in sorted(files.items()):
        if path.is_symlink():
            raise ValueError("Reference inputs must be regular files, not links")
        digest.update(name.encode() + b"\0" + path.read_bytes() + b"\0")
    return digest.hexdigest()


def renderer_image(repository=REPOSITORY):
    site = repository / REFERENCE / "site"
    digest = hashlib.sha256(b"".join((site / f).read_bytes() for f in ("Dockerfile", "Gemfile", "Gemfile.lock")))
    return "ai-gateway-atlas:" + digest.hexdigest()[:16]


def run(command, **kwargs):
    executable = shutil.which(command[0])
    if executable is None:
        raise RuntimeError(f"Required build tool is missing: {command[0]}")
    # All commands are fixed argv lists; no shell or data-file content is executed.
    return subprocess.run([executable, *command[1:]], **kwargs)  # noqa: S603


def ensure_renderer(repository=REPOSITORY):
    image = renderer_image(repository)
    exists = run(["docker", "image", "inspect", image], capture_output=True, check=False)
    if exists.returncode:
        run(["docker", "build", "-t", image, str(repository / REFERENCE / "site")], check=True)
    return image


def render(data, repository=REPOSITORY):
    image = ensure_renderer(repository)
    with tempfile.TemporaryDirectory(prefix="broker-reference-") as temporary:
        staging = Path(temporary)
        source = staging / "source"
        source.mkdir()
        # Copy only Jekyll templates and sanitized JSON. The renderer never sees
        # the checkout, private credential files, git history or raw policy.
        for path in (repository / REFERENCE / "site").rglob("*"):
            if path.is_file() and (path.suffix == ".html" or path.name == "_config.yml"):
                target = source / path.relative_to(repository / REFERENCE / "site")
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(path, target)
        (source / "_data").mkdir()
        (source / "_data/reference.json").write_text(json.dumps(data))
        output = staging / "output"
        output.mkdir()
        run([
            "docker", "run", "--rm", "--network", "none", "--read-only",
            "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
            "--pids-limit", "128", "--memory", "512m",
            "--tmpfs", "/tmp:rw,noexec,nosuid,size=32m",  # noqa: S108 - isolated container tmpfs
            "-e", "HOME=/tmp", "--user", f"{os.getuid()}:{os.getgid()}",
            "-v", f"{source}:/source:ro", "-v", f"{output}:/output:rw", "-w", "/source",
            image, "jekyll", "build", "--safe", "--disable-disk-cache",
            "--source", "/source", "--destination", "/output", "--quiet",
        ], check=True)
        if {path.name for path in output.iterdir()} != set(OUTPUTS):
            raise ValueError("Jekyll produced unexpected output; refusing to publish")
        return {name: (output / name).read_text() for name in OUTPUTS}


class ProvenanceParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.values = {}

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "meta" and attrs.get("name", "").startswith("reference-"):
            self.values[attrs["name"].removeprefix("reference-").replace("-", "_")] = attrs["content"]


def current_revision(repository):
    result = run(["git", "rev-parse", "HEAD"], cwd=repository, text=True,
                            capture_output=True, check=False)
    if result.returncode:
        return "unversioned"
    dirty = run(["git", "status", "--porcelain"], cwd=repository,
                           capture_output=True, check=True).stdout
    return result.stdout.strip() + ("-dirty" if dirty else "")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-directory", type=Path, default=REPOSITORY / REFERENCE)
    parser.add_argument("--input-directory", type=Path, help="Explicit alternate four-file input set, e.g. demo")
    parser.add_argument("--generated-at", help="UTC ISO8601 build time; defaults to now")
    parser.add_argument("--revision", help="Source revision for an archive build (normally git HEAD)")
    parser.add_argument("--check", action="store_true", help="Verify pages without changing their build metadata")
    parser.add_argument("--audit", action="store_true", help="Audit locked build gems against Ruby advisory DB")
    args = parser.parse_args()
    if args.audit:
        run(["docker", "run", "--rm", ensure_renderer(),
                        "bundler-audit", "check", "--update"], check=True)
        return
    files = source_files(input_directory=args.input_directory)
    digest = source_digest(files)
    data = prepare_data(files)
    if args.check:
        provenance = ProvenanceParser()
        provenance.feed((args.output_directory / "index.html").read_text())
        metadata = provenance.values
        if set(metadata) != {"generated_at", "revision", "source_sha256"}:
            raise ValueError("Reference build provenance missing; regenerate the pages")
    else:
        generated = datetime.fromisoformat(args.generated_at.replace("Z", "+00:00")) if args.generated_at else datetime.now(UTC)
        if generated.tzinfo is None:
            raise ValueError("Build timestamp must include a timezone")
        metadata = {
            "generated_at": generated.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
            "revision": args.revision or current_revision(REPOSITORY),
        }
    metadata["source_sha256"] = digest
    data["input_hashes"] = {path.name: hashlib.sha256(files[key].read_bytes()).hexdigest() for key, path in INPUTS.items()}
    data["provenance"] = metadata
    pages = render(data)
    if args.check:
        for name, content in pages.items():
            if (args.output_directory / name).read_text() != content:
                raise ValueError(f"Generated reference is stale: {name}")
        print("Jekyll reference matches its configuration and template sources")
    else:
        args.output_directory.mkdir(parents=True, exist_ok=True)
        for name, content in pages.items():
            target = args.output_directory / name
            temporary = target.with_suffix(".html.tmp")
            temporary.write_text(content)
            temporary.replace(target)
        print("Built two static Jekyll reference pages; no vault access or health checks")


if __name__ == "__main__":
    main()
