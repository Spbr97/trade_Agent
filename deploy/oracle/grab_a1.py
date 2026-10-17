"""Keep asking Oracle for a free Ampere A1 VM until a host has capacity.

The console wizard fails with "Out of capacity for shape VM.Standard.A1.Flex" whenever the
Mumbai availability domain has no free hosts. Capacity frees up unpredictably, so this
retries every couple of minutes and stops once the instance exists. It also builds the
network (VCN, internet gateway, public subnet, SSH open) if it is not there yet, and never
creates a second instance with the same name.

Credentials: it uses the OCI API key you create yourself (console > profile > My profile >
API keys > Add API key; save the config it shows to ~/.oci/config). This script only reads
that file through the official SDK; it never prints or stores it.

Run:  uv run --with oci python deploy/oracle/grab_a1.py --ssh-public-key ~/.ssh/oracle_tradedesk.pub
"""

from __future__ import annotations

import argparse
import random
import sys
import time
from pathlib import Path

import oci

SHAPE = "VM.Standard.A1.Flex"
NAME = "tradedesk"
VCN_CIDR = "10.0.0.0/16"
SUBNET_CIDR = "10.0.0.0/24"


def ensure_network(vn: oci.core.VirtualNetworkClient, compartment: str) -> str:
    """Id of a public subnet for the VM, creating VCN + gateway + route + subnet if needed."""

    for vcn in vn.list_vcns(compartment, display_name=f"{NAME}-vcn").data:
        if vcn.lifecycle_state == "AVAILABLE":
            subnets = vn.list_subnets(compartment, vcn_id=vcn.id).data
            for subnet in subnets:
                if not subnet.prohibit_public_ip_on_vnic:
                    return subnet.id
    print("creating network ...", flush=True)
    models = oci.core.models
    vcn = vn.create_vcn(
        models.CreateVcnDetails(
            cidr_block=VCN_CIDR, compartment_id=compartment, display_name=f"{NAME}-vcn"
        )
    ).data
    vcn = oci.wait_until(vn, vn.get_vcn(vcn.id), "lifecycle_state", "AVAILABLE").data
    gateway = vn.create_internet_gateway(
        models.CreateInternetGatewayDetails(
            compartment_id=compartment, vcn_id=vcn.id, is_enabled=True, display_name=f"{NAME}-igw"
        )
    ).data
    vn.update_route_table(
        vcn.default_route_table_id,
        models.UpdateRouteTableDetails(
            route_rules=[
                models.RouteRule(
                    destination="0.0.0.0/0",
                    destination_type="CIDR_BLOCK",
                    network_entity_id=gateway.id,
                )
            ]
        ),
    )
    # The VCN's default security list already allows inbound SSH (22) from anywhere.
    subnet = vn.create_subnet(
        models.CreateSubnetDetails(
            cidr_block=SUBNET_CIDR,
            compartment_id=compartment,
            vcn_id=vcn.id,
            display_name=f"{NAME}-subnet",
            prohibit_public_ip_on_vnic=False,
        )
    ).data
    return oci.wait_until(vn, vn.get_subnet(subnet.id), "lifecycle_state", "AVAILABLE").data.id


def existing_instance(compute: oci.core.ComputeClient, compartment: str):
    for inst in compute.list_instances(compartment, display_name=NAME).data:
        if inst.lifecycle_state not in {"TERMINATED", "TERMINATING"}:
            return inst
    return None


def public_ip(compute, vn, compartment: str, instance_id: str) -> str:
    for attachment in compute.list_vnic_attachments(compartment, instance_id=instance_id).data:
        ip = vn.get_vnic(attachment.vnic_id).data.public_ip
        if ip:
            return ip
    return "(not assigned yet; check the instance page)"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--ssh-public-key", type=Path, required=True)
    parser.add_argument("--ocpus", type=float, default=2)
    parser.add_argument("--memory-gb", type=float, default=12)
    parser.add_argument("--boot-gb", type=int, default=100)
    parser.add_argument("--every-seconds", type=int, default=120)
    parser.add_argument("--profile", default="DEFAULT")
    args = parser.parse_args(argv)

    config = oci.config.from_file(profile_name=args.profile)
    oci.config.validate_config(config)
    compartment = config["tenancy"]
    identity = oci.identity.IdentityClient(config)
    compute = oci.core.ComputeClient(config)
    vn = oci.core.VirtualNetworkClient(config)
    pub_key = args.ssh_public_key.expanduser().read_text(encoding="utf-8").strip()

    found = existing_instance(compute, compartment)
    if found:
        print(f"already exists: {found.id} ({found.lifecycle_state})")
        print("public IP:", public_ip(compute, vn, compartment, found.id))
        return 0

    domains = [d.name for d in identity.list_availability_domains(compartment).data]
    image = compute.list_images(
        compartment,
        operating_system="Canonical Ubuntu",
        operating_system_version="24.04",
        shape=SHAPE,
        sort_by="TIMECREATED",
        sort_order="DESC",
    ).data[0]
    subnet_id = ensure_network(vn, compartment)
    models = oci.core.models
    print(f"image {image.display_name}; trying {domains} every ~{args.every_seconds}s", flush=True)

    attempt = 0
    while True:
        attempt += 1
        for domain in domains:
            details = models.LaunchInstanceDetails(
                availability_domain=domain,
                compartment_id=compartment,
                display_name=NAME,
                shape=SHAPE,
                shape_config=models.LaunchInstanceShapeConfigDetails(
                    ocpus=args.ocpus, memory_in_gbs=args.memory_gb
                ),
                source_details=models.InstanceSourceViaImageDetails(
                    image_id=image.id, boot_volume_size_in_gbs=args.boot_gb
                ),
                create_vnic_details=models.CreateVnicDetails(
                    subnet_id=subnet_id, assign_public_ip=True
                ),
                metadata={"ssh_authorized_keys": pub_key},
            )
            try:
                instance = compute.launch_instance(details).data
            except oci.exceptions.ServiceError as exc:
                busy = exc.status in {429, 500} or "capacity" in (exc.message or "").lower()
                stamp = time.strftime("%H:%M:%S")
                print(f"{stamp} attempt {attempt} {domain}: {exc.status} {exc.code}", flush=True)
                if not busy:
                    print(exc.message)
                    return 1
                continue
            instance = oci.wait_until(
                compute, compute.get_instance(instance.id), "lifecycle_state", "RUNNING"
            ).data
            print(f"CREATED {instance.id}")
            print("public IP:", public_ip(compute, vn, compartment, instance.id))
            return 0
        time.sleep(args.every_seconds + random.uniform(0, 30))


if __name__ == "__main__":
    sys.exit(main())
