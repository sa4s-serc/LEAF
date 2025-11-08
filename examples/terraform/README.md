# GCP Terraform Configuration

This Terraform configuration sets up a Google Cloud Platform (GCP) environment with various resources including VPC networks, Cloud SQL instances, service accounts, and IAM roles.

## Prerequisites

- Terraform installed (version compatible with provider versions in `provider.tf`)
- Google Cloud SDK installed and configured
- A GCP project with necessary APIs enabled

## Files Structure

- `provider.tf`: Defines the required providers and their versions
- `variable.tf`: Declares input variables
- `terraform.tfvars`: Sets values for the declared variables
- `serviceaccount.tf`: Creates a service account for Cloud SQL
- `network.tf`: Sets up VPC network, subnets, firewall rules, and NAT configuration
- `iam.tf`: Configures IAM roles for the service account
- `main.tf`: Creates Cloud SQL instances (public and private) and databases

## Usage

1. Clone this repository
2. Navigate to the project directory
3. Update `terraform.tfvars` with your specific values
4. Run the following commands:

   ```
   terraform init
   terraform plan
   terraform apply
   ```

5. When prompted, review the planned changes and type `yes` to apply

## Resources Created

- VPC Network and Subnets
- Firewall Rules
- NAT Gateway
- Service Account
- IAM Role Bindings
- Cloud SQL Instances (Public and Private)
- Cloud SQL Databases and Users
- VPC Access Connector

## Variables

| Name | Description | Default |
|------|-------------|---------|
| project_id | GCP Project ID | "" |
| region | Primary region | "" |
| zone | Primary zone | "" |
| sec_region | Secondary region | "" |
| sec_zone | Secondary zone | "" |

## Outputs

- `service_account_email`: Email of the created service account

## Notes

- The configuration includes both public and private Cloud SQL instances
- Ensure that you have the necessary permissions to create these resources in your GCP project
- Remember to run `terraform destroy` when you're done to avoid unnecessary charges

## Security Considerations

- The firewall rules allow SSH access from a specific IP (39.33.11.48/32). Ensure this is your correct IP or adjust as needed.
- Sensitive information like passwords are hardcoded in this example. For production use, consider using Terraform variables or a secrets management solution.

## Customization

You can modify the `terraform.tfvars` file to customize the deployment for your needs. Adjust network CIDR ranges, instance types, and other parameters as required.

gcloud run deploy springboot-cloudsql-run --image us-central1-docker.pkg.dev/leaf-test-3/my-repo/quickstart-springboot:1.0.1 --region=us-central1 --allow-unauthenticated --service-account="cloudsql-service-account-id@leaf-test-3.iam.gserviceaccount.com" --vpc-connector=private-cloud-sql --project=leaf-test-3