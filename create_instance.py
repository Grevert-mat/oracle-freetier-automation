#!/usr/bin/env python3
"""
Oracle Cloud Always Free Instance Automation Script
Creates ARM Ampere instances (4 vCPU, 24GB RAM) automatically
Retries every minute until successful
"""

import oci
import logging
import time
import sys
from datetime import datetime

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

class OracleInstanceCreator:
    def __init__(self, config_file="~/.oci/config", profile="DEFAULT"):
        """Initialize Oracle client"""
        self.config = oci.config.from_file(config_file, profile)
        self.compute_client = oci.core.ComputeClient(self.config)
        self.network_client = oci.core.VirtualNetworkClient(self.config)
                # NOVO: client certo para listar ADs
        self.identity_client = oci.identity.IdentityClient(self.config)
        
    def get_availability_domains(self, tenancy_iid):
        """Get available domains in compartment"""
        try:
                    response = self.identity_client.list_availability_domains(tenancy_id)
                return [ad.name for ad in response.data]
        except Exception as e:
            logger.error(f"Error getting ADs: {e}")
            return []
    
    def get_vcn_and_subnet(self, compartment_id):
        """Get default VCN and subnet"""
        try:
            # List VCNs
            vcn_response = self.network_client.list_vcns(compartment_id=compartment_id)
            if not vcn_response.data:
                logger.error("No VCN found")
                return None, None
            
            vcn_id = vcn_response.data[0].id
            
            # List subnets
            subnet_response = self.network_client.list_subnets(
                compartment_id=compartment_id,
                vcn_id=vcn_id
            )
            
            if not subnet_response.data:
                logger.error("No subnet found")
                return vcn_id, None
            
            subnet_id = subnet_response.data[0].id
            return vcn_id, subnet_id
            
        except Exception as e:
            logger.error(f"Error getting VCN/Subnet: {e}")
            return None, None
    
    def create_instance(self, compartment_id, availability_domain, subnet_id, instance_name="openclaw-server"):
        """Create Always Free ARM Ampere instance"""
        try:
            launch_details = oci.core.models.LaunchInstanceDetails(
                compartment_id=compartment_id,
                display_name=instance_name,
                image_id=self._get_ampere_image_id(compartment_id),
                shape="VM.Standard.A1.Flex",
                shape_config=oci.core.models.LaunchInstanceShapeConfigDetails(
                    ocpus=4,
                    memory_in_gbs=24
                ),
                availability_domain=availability_domain,
                create_vnic_details=oci.core.models.CreateVnicDetails(
                    subnet_id=subnet_id,
                    assign_public_ip=True
                ),
                metadata={
                    "ssh_authorized_keys": self._get_ssh_key()
                }
            )
            
            response = self.compute_client.launch_instance(launch_details)
            instance_id = response.data.id
            logger.info(f"✓ Instance created successfully: {instance_id}")
            return instance_id
            
        except oci.exceptions.ServiceError as e:
            if "out of capacity" in str(e).lower():
                logger.warning(f"⚠ Out of capacity: {e}")
                return None
            else:
                logger.error(f"✗ Service error: {e}")
                raise
        except Exception as e:
            logger.error(f"✗ Unexpected error: {e}")
            raise
    
    def _get_ampere_image_id(self, compartment_id):
        """Get latest Oracle Linux 9 ARM64 image"""
        try:
            response = self.compute_client.list_images(
                compartment_id=compartment_id,
                shape="VM.Standard.A1.Flex"
            )
            if response.data:
                # Return first image (usually latest)
                return response.data[0].id
            logger.warning("No image found, using default")
            return None
        except Exception as e:
            logger.warning(f"Error getting image: {e}")
            return None
    
    def _get_ssh_key(self):
        """Get SSH public key from ~/.ssh/id_rsa.pub or return empty"""
        try:
            import os
            ssh_key_path = os.path.expanduser("~/.ssh/id_rsa.pub")
            if os.path.exists(ssh_key_path):
                with open(ssh_key_path, 'r') as f:
                    return f.read().strip()
        except Exception as e:
            logger.warning(f"Could not read SSH key: {e}")
        return ""
    
    def run_with_retry(self, compartment_id, max_retries=None, retry_interval=60):
        """Try to create instance with automatic retry"""
        attempt = 0
        max_retries_display = max_retries if max_retries else "infinite"
        
        while True:
            attempt += 1
            logger.info(f"\n--- Attempt {attempt} ({max_retries_display} max) ---")
            logger.info(f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
            
            try:
                # Get ADs and networking
                ads = self.get_availability_domains(tenancy_id
                if not ads:
                    logger.error("No availability domains found")
                    if max_retries and attempt >= max_retries:
                        break
                    logger.info(f"Retrying in {retry_interval} seconds...")
                    time.sleep(retry_interval)
                    continue
                
                vcn_id, subnet_id = self.get_vcn_and_subnet(compartment_id)
                if not subnet_id:
                    logger.error("Could not get subnet")
                    if max_retries and attempt >= max_retries:
                        break
                    logger.info(f"Retrying in {retry_interval} seconds...")
                    time.sleep(retry_interval)
                    continue
                
                # Try first AD
                ad = ads[0]
                logger.info(f"Using AD: {ad}")
                
                instance_id = self.create_instance(
                    compartment_id=compartment_id,
                    availability_domain=ad,
                    subnet_id=subnet_id
                )
                
                if instance_id:
                    logger.info("\n" + "="*50)
                    logger.info("SUCCESS! Instance created.")
                    logger.info("="*50)
                    return instance_id
                
            except Exception as e:
                logger.error(f"Error during attempt: {e}")
            
            # Check if we should retry
            if max_retries and attempt >= max_retries:
                logger.error(f"Max retries ({max_retries}) reached. Exiting.")
                break
            
            logger.info(f"Retrying in {retry_interval} seconds...")
            time.sleep(retry_interval)
        
        return None


def main():
    """Main function"""
    # Configuration
    compartment_id = "ocid1.tenancy.oc1..aaaaaaaay22z7rphg6mwbftmwohuismc7wqw2gwvfik3kop5jxz7pndyz4uq"  # Replace with your tenancy OCID
    config_file = "~/.oci/config"
    profile = "DEFAULT"
    max_retries = None  # None = infinite retries
    retry_interval = 60  # seconds
    
    logger.info("Oracle Cloud Always Free Instance Creator")
    logger.info("Specs: ARM Ampere, 4 vCPU, 24 GB RAM")
    logger.info("="*50)
    
    try:
        creator = OracleInstanceCreator(config_file, profile)
        instance_id = creator.run_with_retry(
            compartment_id=compartment_id,
            max_retries=max_retries,
            retry_interval=retry_interval
        )
        
        if instance_id:
            logger.info(f"Instance ID: {instance_id}")
            logger.info("You can now configure and use your instance.")
            return 0
        else:
            logger.error("Failed to create instance")
            return 1
            
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
