"""
Executors de remédiation infrastructure (Ticket "Remédiations infra
Ansible/Terraform").

Décisions validées avec l'encadrant :
  1. Pas de vrais playbooks/fichiers Terraform disponibles pour l'instant
     -> exemples fournis (dossier ansible/), à compléter avec les vraies
     ressources AWS (cf. eks.tf, rds.tf... du dossier infra Terraform).
  2. dry_run=True PAR DÉFAUT (contrairement à k8s_executor, où le risque
     minikube est nul) — mais changeable consciemment, jamais figé.
  3. Vérification post-action = interroger AWS réellement (via boto3),
     pas seulement le code retour de la commande Ansible/Terraform.
"""

import json
import subprocess
import time

import boto3
from botocore.exceptions import BotoCoreError, ClientError, NoCredentialsError

from k8s_executor import ActionResult
from logging_setup import get_logger

logger = get_logger(__name__)

DEFAULT_TIMEOUT_SECONDS = 300


class InfraExecutor:
    def __init__(self, dry_run: bool = True, ansible_dir: str = "ansible",
                 terraform_dir: str = "terraform", aws_region: str = "us-west-2",
                 ec2_client=None, autoscaling_client=None, route53_client=None, acm_client=None):
        self.dry_run = dry_run
        self.ansible_dir = ansible_dir
        self.terraform_dir = terraform_dir
        self._ec2 = ec2_client
        self._autoscaling = autoscaling_client
        self._route53 = route53_client
        self._acm = acm_client
        self._aws_region = aws_region

    def _client(self, existing, service_name):
        if existing is not None:
            return existing
        return boto3.client(service_name, region_name=self._aws_region)

    # ------------------------------------------------------------------
    def resize_instance(self, instance_id: str, new_instance_type: str) -> ActionResult:
        exec_result = self._terraform_apply_targeted(
            action_id="resize_instance", resource_address=f"aws_instance.{instance_id}",
            tf_vars={"instance_type": new_instance_type},
        )
        if self.dry_run or not exec_result.success:
            return exec_result
        verified = self._verify_instance_type(instance_id, new_instance_type)
        return self._merge_verification(exec_result, verified)

    def add_capacity(self, asg_name: str, desired_capacity: int) -> ActionResult:
        exec_result = self._terraform_apply_targeted(
            action_id="add_capacity", resource_address=f"aws_autoscaling_group.{asg_name}",
            tf_vars={"desired_capacity": str(desired_capacity)},
        )
        if self.dry_run or not exec_result.success:
            return exec_result
        verified = self._verify_asg_capacity(asg_name, desired_capacity)
        return self._merge_verification(exec_result, verified)

    def failover_dns(self, record_name: str, new_target: str) -> ActionResult:
        exec_result = self._run_ansible_playbook(
            action_id="failover_dns", target=record_name, playbook="failover_dns.yml",
            extra_vars={"record_name": record_name, "new_target": new_target},
        )
        if self.dry_run or not exec_result.success:
            return exec_result
        verified = self._verify_dns_record(record_name, new_target)
        return self._merge_verification(exec_result, verified)

    def rotate_certificates(self, cert_arn: str) -> ActionResult:
        exec_result = self._run_ansible_playbook(
            action_id="rotate_certificates", target=cert_arn, playbook="rotate_certificates.yml",
            extra_vars={"cert_name": cert_arn},
        )
        if self.dry_run or not exec_result.success:
            return exec_result
        verified = self._verify_certificate_valid(cert_arn)
        return self._merge_verification(exec_result, verified)

    def cleanup_resources(self, resource_filter: str) -> ActionResult:
        # Pas de vérification AWS générique possible ici (le "succès" dépend
        # entièrement du filtre) -> on s'appuie sur le code retour Ansible.
        return self._run_ansible_playbook(
            action_id="cleanup_resources", target=resource_filter, playbook="cleanup_resources.yml",
            extra_vars={"filter": resource_filter},
        )

    # ------------------------------------------------------------------
    # Vérification réelle post-action (critère 3, décidé avec l'encadrant)
    # ------------------------------------------------------------------
    def _verify_instance_type(self, instance_id: str, expected_type: str) -> bool | None:
        try:
            ec2 = self._client(self._ec2, "ec2")
            resp = ec2.describe_instances(InstanceIds=[instance_id])
            actual_type = resp["Reservations"][0]["Instances"][0]["InstanceType"]
            return actual_type == expected_type
        except (BotoCoreError, ClientError, NoCredentialsError, KeyError, IndexError) as e:
            logger.warning("Vérification AWS impossible (resize_instance) : %s", e)
            return None

    def _verify_asg_capacity(self, asg_name: str, expected_capacity: int) -> bool | None:
        try:
            asg = self._client(self._autoscaling, "autoscaling")
            resp = asg.describe_auto_scaling_groups(AutoScalingGroupNames=[asg_name])
            actual = resp["AutoScalingGroups"][0]["DesiredCapacity"]
            return actual == expected_capacity
        except (BotoCoreError, ClientError, NoCredentialsError, KeyError, IndexError) as e:
            logger.warning("Vérification AWS impossible (add_capacity) : %s", e)
            return None

    def _verify_dns_record(self, record_name: str, expected_value: str) -> bool | None:
        try:
            route53 = self._client(self._route53, "route53")
            resp = route53.test_dns_answer(
                HostedZoneId="REMPLACE_PAR_TA_ZONE_ID", RecordName=record_name, RecordType="A",
            )
            return expected_value in resp.get("RecordData", [])
        except (BotoCoreError, ClientError, NoCredentialsError, KeyError) as e:
            logger.warning("Vérification AWS impossible (failover_dns) : %s", e)
            return None

    def _verify_certificate_valid(self, cert_arn: str) -> bool | None:
        try:
            acm = self._client(self._acm, "acm")
            resp = acm.describe_certificate(CertificateArn=cert_arn)
            return resp["Certificate"]["Status"] == "ISSUED"
        except (BotoCoreError, ClientError, NoCredentialsError, KeyError) as e:
            logger.warning("Vérification AWS impossible (rotate_certificates) : %s", e)
            return None

    @staticmethod
    def _merge_verification(exec_result: ActionResult, verified: bool | None) -> ActionResult:
        """
        Combine le résultat de la commande avec la vérification AWS réelle.
        `verified is None` = vérification impossible (pas de credentials,
        API injoignable) -> on le dit explicitement plutôt que de
        prétendre à tort que c'est confirmé.
        """
        if verified is None:
            exec_result.message += " | vérification AWS indisponible (credentials/API)"
            exec_result.verified_ready = False
        else:
            exec_result.verified_ready = verified
            if not verified:
                exec_result.message += " | l'état réel sur AWS ne correspond pas au changement attendu"
        return exec_result

    # ------------------------------------------------------------------
    # Exécution des commandes (Ansible / Terraform)
    # ------------------------------------------------------------------
    def _run_ansible_playbook(self, action_id: str, target: str, playbook: str, extra_vars: dict) -> ActionResult:
        start = time.time()
        cmd = ["ansible-playbook", f"{self.ansible_dir}/{playbook}", "-e", json.dumps(extra_vars)]
        if self.dry_run:
            cmd.append("--check")

        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=DEFAULT_TIMEOUT_SECONDS)
        except (subprocess.SubprocessError, FileNotFoundError, OSError) as e:
            return ActionResult(action_id, target, False, f"exécution Ansible impossible : {e}")

        success = proc.returncode == 0
        tail = (proc.stdout if success else proc.stderr)[-500:]
        prefix = "[DRY-RUN --check] " if self.dry_run else ""
        return ActionResult(action_id, target, success, f"{prefix}{tail}",
                             verified_ready=success, duration_seconds=time.time() - start)

    def _terraform_apply_targeted(self, action_id: str, resource_address: str, tf_vars: dict) -> ActionResult:
        start = time.time()
        subcommand = "plan" if self.dry_run else "apply"
        cmd = ["terraform", f"-chdir={self.terraform_dir}", subcommand, "-target", resource_address]
        if not self.dry_run:
            cmd.append("-auto-approve")
        for k, v in tf_vars.items():
            cmd += ["-var", f"{k}={v}"]

        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=DEFAULT_TIMEOUT_SECONDS)
        except (subprocess.SubprocessError, FileNotFoundError, OSError) as e:
            return ActionResult(action_id, resource_address, False, f"exécution Terraform impossible : {e}")

        success = proc.returncode == 0
        tail = (proc.stdout if success else proc.stderr)[-500:]
        prefix = "[DRY-RUN plan] " if self.dry_run else ""
        return ActionResult(action_id, resource_address, success, f"{prefix}{tail}",
                             verified_ready=success, duration_seconds=time.time() - start)
