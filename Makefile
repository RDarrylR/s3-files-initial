.PHONY: init plan apply destroy fmt validate outputs \
       seed seed-before seed-after seed-small seed-large \
       invoke-before invoke-after benchmark \
       deploy-runner wait-ec2 benchmark-ec2 benchmark-ec2-large benchmark-ec2-list \
       ec2-ssh ec2-logs \
       logs-before logs-after status clean

# --- Configuration ---

INFRA_DIR  = infrastructure
REGION     = $(shell cd $(INFRA_DIR) && terraform output -raw aws_region 2>/dev/null || echo "us-east-1")
BUCKET     = $(shell cd $(INFRA_DIR) && terraform output -raw bucket_name 2>/dev/null)
BEFORE_FN  = $(shell cd $(INFRA_DIR) && terraform output -raw before_function_name 2>/dev/null)
AFTER_FN   = $(shell cd $(INFRA_DIR) && terraform output -raw after_function_name 2>/dev/null)
EC2_ID     = $(shell cd $(INFRA_DIR) && terraform output -raw ec2_benchmark_instance_id 2>/dev/null)

# --- Terraform ---

init:
	cd $(INFRA_DIR) && terraform init

plan:
	cd $(INFRA_DIR) && terraform plan

apply:
	cd $(INFRA_DIR) && terraform apply

destroy:
	cd $(INFRA_DIR) && terraform destroy

fmt:
	cd $(INFRA_DIR) && terraform fmt -recursive

validate:
	cd $(INFRA_DIR) && terraform validate

outputs:
	@cd $(INFRA_DIR) && terraform output

# --- Seeding ---
# The before and after Lambdas read from separate prefixes (inbox-before/ and
# inbox-after/) to avoid S3-origin directory ownership conflicts on the NFS
# mount. See the "Access Point Ownership" section of the blog post.

seed: seed-before seed-after

seed-before:
	python scripts/seed_inbox.py --bucket $(BUCKET) --region $(REGION) --count 20 --size medium --prefix inbox-before/

seed-after:
	python scripts/seed_inbox.py --bucket $(BUCKET) --region $(REGION) --count 20 --size medium --prefix inbox-after/

seed-small:
	python scripts/seed_inbox.py --bucket $(BUCKET) --region $(REGION) --count 50 --size small --prefix inbox-before/
	python scripts/seed_inbox.py --bucket $(BUCKET) --region $(REGION) --count 50 --size small --prefix inbox-after/

seed-large:
	python scripts/seed_inbox.py --bucket $(BUCKET) --region $(REGION) --count 5 --size large --prefix inbox-before/
	python scripts/seed_inbox.py --bucket $(BUCKET) --region $(REGION) --count 5 --size large --prefix inbox-after/

# --- Invocation ---

invoke-before:
	aws lambda invoke \
		--function-name $(BEFORE_FN) \
		--region $(REGION) \
		--cli-binary-format raw-in-base64-out \
		--payload file://test-events/process-event.json \
		/dev/stdout 2>/dev/null | jq .

invoke-after:
	aws lambda invoke \
		--function-name $(AFTER_FN) \
		--region $(REGION) \
		--cli-binary-format raw-in-base64-out \
		--payload file://test-events/process-event.json \
		/dev/stdout 2>/dev/null | jq .

status:
	@echo "=== Before Lambda ==="
	@aws lambda invoke \
		--function-name $(BEFORE_FN) \
		--region $(REGION) \
		--cli-binary-format raw-in-base64-out \
		--payload file://test-events/status-event.json \
		/dev/stdout 2>/dev/null | jq .
	@echo ""
	@echo "=== After Lambda ==="
	@aws lambda invoke \
		--function-name $(AFTER_FN) \
		--region $(REGION) \
		--cli-binary-format raw-in-base64-out \
		--payload file://test-events/status-event.json \
		/dev/stdout 2>/dev/null | jq .

# --- Benchmark ---

benchmark:
	python scripts/benchmark.py \
		--bucket $(BUCKET) \
		--before-function $(BEFORE_FN) \
		--after-function $(AFTER_FN) \
		--region $(REGION) \
		--file-count 20 \
		--runs 3

# --- Logs ---

logs-before:
	aws logs tail /aws/lambda/$(BEFORE_FN) --follow --region $(REGION)

logs-after:
	aws logs tail /aws/lambda/$(AFTER_FN) --follow --region $(REGION)

# --- EC2 three-way benchmark (S3 API vs S3 Files vs Mountpoint) ---
# The benchmark runner lives on the EC2 host at /opt/runner/benchmark.py.
# We upload it to s3://$(BUCKET)/runner/benchmark.py and user-data pulls it down.
# Re-run `make deploy-runner` any time you change src/ec2_runner/benchmark.py.

deploy-runner:
	aws s3 cp --region $(REGION) \
		src/ec2_runner/benchmark.py s3://$(BUCKET)/runner/benchmark.py
	@echo "Refreshing the runner on the EC2 instance..."
	aws ssm send-command --region $(REGION) \
		--instance-ids $(EC2_ID) \
		--document-name AWS-RunShellScript \
		--parameters 'commands=["aws s3 cp s3://$(BUCKET)/runner/benchmark.py /opt/runner/benchmark.py && chmod +x /opt/runner/benchmark.py"]' \
		--query 'Command.CommandId' --output text > /dev/null
	@echo "Done (runner refreshed via SSM)."

wait-ec2:
	@echo "Waiting for EC2 instance $(EC2_ID) to finish bootstrap..."
	@until aws ssm describe-instance-information --region $(REGION) \
		--filters "Key=InstanceIds,Values=$(EC2_ID)" \
		--query 'InstanceInformationList[0].PingStatus' --output text 2>/dev/null | grep -q Online; do \
		sleep 10; echo "  still waiting..."; \
	done
	@echo "SSM agent online."

benchmark-ec2:
	python scripts/ec2_benchmark.py \
		--bucket $(BUCKET) \
		--instance-id $(EC2_ID) \
		--region $(REGION) \
		--runs 3 \
		--seed-dir-count 10000 \
		--seed-large-count 5 \
		--seed-large-size-mib 1024

benchmark-ec2-list:
	python scripts/ec2_benchmark.py \
		--bucket $(BUCKET) \
		--instance-id $(EC2_ID) \
		--region $(REGION) \
		--runs 3 \
		--skip-large \
		--seed-dir-count 10000

benchmark-ec2-large:
	python scripts/ec2_benchmark.py \
		--bucket $(BUCKET) \
		--instance-id $(EC2_ID) \
		--region $(REGION) \
		--runs 3 \
		--skip-list \
		--seed-large-count 5 \
		--seed-large-size-mib 1024

ec2-ssh:
	aws ssm start-session --region $(REGION) --target $(EC2_ID)

ec2-logs:
	aws ssm send-command --region $(REGION) \
		--instance-ids $(EC2_ID) \
		--document-name AWS-RunShellScript \
		--parameters 'commands=["tail -n 200 /var/log/user-data.log"]' \
		--query 'Command.CommandId' --output text | xargs -I{} sh -c 'sleep 3; aws ssm get-command-invocation --region $(REGION) --command-id {} --instance-id $(EC2_ID) --query StandardOutputContent --output text'

# --- Cleanup ---

clean:
	rm -rf build/
	rm -rf $(INFRA_DIR)/modules/*/.build/
