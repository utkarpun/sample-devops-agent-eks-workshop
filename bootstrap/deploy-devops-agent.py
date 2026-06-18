#!/usr/bin/env python3
"""Deploy DevOps Agent infrastructure and configure MCP plugin (us-east-1 only).

Called from BootstrapDocument DeployEKSCluster step during parallel deploy.
Expects AWS_REGION set in environment (no IMDS lookup).
Uses /tmp/devops.kubeconfig for kubectl isolation (merged by caller).
"""
import os
import sys
import subprocess
import json
import time

REGION = os.environ.get('AWS_REGION', '')
PARTICIPANT_USER = 'participant'
PARTICIPANT_HOME = f'/home/{PARTICIPANT_USER}'
STATUS_FILE = f'{PARTICIPANT_HOME}/.devops-agent-deploy-status'


def write_status(status, detail=''):
    """Write deployment status to a file for debugging."""
    with open(STATUS_FILE, 'w') as f:
        f.write(json.dumps({'status': status, 'region': REGION, 'detail': detail}))
    subprocess.run(['chown', f'{PARTICIPANT_USER}:{PARTICIPANT_USER}', STATUS_FILE], check=False)


if REGION != 'us-east-1':
    print(f"Region is {REGION}, not us-east-1. Skipping DevOps Agent deployment.")
    try:
        write_status('skipped', f'Region {REGION} is not us-east-1')
    except Exception:
        pass
    sys.exit(0)

try:
    import boto3
    from botocore.exceptions import ClientError, BotoCoreError

    # Pre-flight: verify boto3 knows about devops-agent service
    try:
        boto3.client('devops-agent', region_name=REGION)
    except Exception as preflight_err:
        raise RuntimeError(
            f"boto3 does not support devops-agent service (need botocore >= 1.43.6). "
            f"pip3 upgrade may have failed silently. Error: {preflight_err}"
        ) from preflight_err

    TRANSIENT_ERRORS = {'ThrottlingException', 'TooManyRequestsException',
                        'ServiceUnavailable', 'RequestLimitExceeded', 'InternalFailure'}

    def retry_transient(func, max_attempts=3, initial_delay=5):
        """Retry boto3 calls on transient errors with exponential backoff."""
        from botocore.exceptions import (
            EndpointConnectionError, ConnectTimeoutError,
            ReadTimeoutError, ConnectionClosedError
        )
        TRANSIENT_BOTOCORE = (EndpointConnectionError, ConnectTimeoutError,
                              ReadTimeoutError, ConnectionClosedError)
        for attempt in range(max_attempts):
            try:
                return func()
            except ClientError as e:
                code = e.response['Error']['Code']
                if code in TRANSIENT_ERRORS and attempt < max_attempts - 1:
                    wait = initial_delay * (2 ** attempt)
                    print(f"  Transient error ({code}), retry {attempt+1}/{max_attempts} in {wait}s")
                    time.sleep(wait)
                else:
                    raise
            except TRANSIENT_BOTOCORE as e:
                if attempt < max_attempts - 1:
                    wait = initial_delay * (2 ** attempt)
                    print(f"  Connection error ({type(e).__name__}), retry {attempt+1}/{max_attempts} in {wait}s")
                    time.sleep(wait)
                else:
                    raise
        raise RuntimeError("unreachable")

    # Step 0: Clone the DevOps Agent workshop repo to participant home
    repo_dir = os.path.join(PARTICIPANT_HOME, 'sample-devops-agent-eks-workshop')
    if not os.path.exists(repo_dir):
        subprocess.run(
            ['sudo', '-u', PARTICIPANT_USER, 'git', 'clone', '--branch', 'melbourne-fixes', '--depth', '1',
             'https://github.com/utkarpun/sample-devops-agent-eks-workshop.git', repo_dir],
            check=True
        )

    # Step 1: Deploy retail store cluster via Terraform (retry on transient failures)
    deploy_script = os.path.join(repo_dir, 'terraform', 'deploy.sh')
    deploy_env = os.environ.copy()
    deploy_env['CLUSTER_NAME'] = 'retail-store'
    deploy_env['AWS_REGION'] = REGION
    deploy_env['AWS_DEFAULT_REGION'] = REGION
    deploy_cwd = os.path.join(repo_dir, 'terraform')

    for tf_attempt in range(1, 4):
        print(f"Applying Terraform (attempt {tf_attempt}/3)...")
        tf_result = subprocess.run(['bash', deploy_script], env=deploy_env, cwd=deploy_cwd)
        if tf_result.returncode == 0:
            print(f"Terraform succeeded on attempt {tf_attempt}")
            break
        if tf_attempt >= 3:
            raise subprocess.CalledProcessError(tf_result.returncode, ['bash', deploy_script])
        print(f"Terraform attempt {tf_attempt} failed (exit {tf_result.returncode}), waiting 15s before retry...")
        time.sleep(15)

    # Step 1b: Apply NodeClass network policy (CRD is ready after terraform)
    print("Applying NodeClass network policy configuration...")
    subprocess.run(['aws', 'eks', 'update-kubeconfig', '--name', 'retail-store',
                    '--region', REGION, '--kubeconfig', '/tmp/devops.kubeconfig'], check=True)
    nodeclass_yaml = "apiVersion: eks.amazonaws.com/v1\nkind: NodeClass\nmetadata:\n  name: default\nspec:\n  networkPolicy: DefaultAllow\n  networkPolicyEventLogs: Enabled\n"
    for attempt in range(1, 31):
        result = subprocess.run(
            ['kubectl', '--kubeconfig', '/tmp/devops.kubeconfig', 'apply', '-f', '-'],
            input=nodeclass_yaml, text=True, capture_output=True
        )
        if result.returncode == 0:
            print(f"NodeClass applied successfully on attempt {attempt}")
            break
        if attempt < 30:
            print(f"  NodeClass attempt {attempt}/30 failed, retrying in 10s...")
            time.sleep(10)
        else:
            print(f"WARNING: NodeClass apply failed after 30 attempts (non-fatal): {result.stderr}")

    # Step 2: Create Agent Space first (need ID for trust policy)
    iam = boto3.client('iam', region_name=REGION)
    devops = boto3.client('devops-agent', region_name=REGION)
    account_id = boto3.client('sts', region_name=REGION).get_caller_identity()['Account']
    role_name = 'DevOpsAgentOperatorRole'

    # Create or find existing agent space
    spaces = retry_transient(lambda: devops.list_agent_spaces())
    agent_space_id = None
    for space in spaces.get('agentSpaces', []):
        if space.get('name') == 'apex-workshop':
            agent_space_id = space['agentSpaceId']
            break
    if not agent_space_id:
        space_response = retry_transient(lambda: devops.create_agent_space(name='apex-workshop'))
        agent_space_id = space_response['agentSpace']['agentSpaceId']
    print(f"Agent Space: {agent_space_id}")

    # Step 3: Create IAM role with SourceArn trust policy (delete first if exists)
    try:
        try:
            for ip in iam.list_instance_profiles_for_role(RoleName=role_name)['InstanceProfiles']:
                iam.remove_role_from_instance_profile(RoleName=role_name, InstanceProfileName=ip['InstanceProfileName'])
        except Exception:
            pass
        for p in iam.list_attached_role_policies(RoleName=role_name)['AttachedPolicies']:
            iam.detach_role_policy(RoleName=role_name, PolicyArn=p['PolicyArn'])
        iam.delete_role(RoleName=role_name)
        time.sleep(2)
    except Exception:
        pass

    trust_policy = json.dumps({
        "Version": "2012-10-17",
        "Statement": [{
            "Effect": "Allow",
            "Principal": {"Service": "aidevops.amazonaws.com"},
            "Action": "sts:AssumeRole",
            "Condition": {
                "StringEquals": {"aws:SourceAccount": account_id},
                "ArnEquals": {"aws:SourceArn": f"arn:aws:aidevops:{REGION}:{account_id}:agentspace/{agent_space_id}"}
            }
        }]
    })
    iam.create_role(
        RoleName=role_name,
        AssumeRolePolicyDocument=trust_policy,
        Description='IAM role for DevOps Agent Operator Web App'
    )
    for policy in [
        'arn:aws:iam::aws:policy/AIDevOpsAgentAccessPolicy',
        'arn:aws:iam::aws:policy/AIDevOpsOperatorAppAccessPolicy'
    ]:
        iam.attach_role_policy(RoleName=role_name, PolicyArn=policy)
    time.sleep(15)
    role_arn = f'arn:aws:iam::{account_id}:role/{role_name}'
    print(f"IAM Role: {role_arn}")

    # Step 4: Enable Operator Web App (retry for IAM propagation)
    for attempt in range(1, 5):
        try:
            devops.enable_operator_app(
                agentSpaceId=agent_space_id,
                authFlow='iam',
                operatorAppRoleArn=role_arn
            )
            print("Enabled Operator Web App")
            break
        except Exception as e:
            err = str(e).lower()
            if attempt < 4 and any(k in err for k in ['not authorized', 'cannot be assumed', 'access denied', 'validation']):
                print(f"  Retry {attempt}/4 (IAM propagation): {e}")
                time.sleep(10 * attempt)
            else:
                raise

    # Step 5: Associate AWS account with agent space
    try:
        retry_transient(lambda: devops.associate_service(
            agentSpaceId=agent_space_id,
            serviceId='aws',
            configuration={'aws': {'accountId': account_id, 'accountType': 'monitor', 'assumableRoleArn': role_arn}}
        ))
        print(f"Associated account {account_id}")
    except ClientError as e:
        if 'already associated' in str(e).lower() or 'Conflict' in str(e) or e.response['Error']['Code'] == 'ConflictException':
            print("Account already associated (OK)")
        else:
            raise

    # Step 6: EKS access entry for DevOps Agent
    eks = boto3.client('eks', region_name=REGION)
    try:
        retry_transient(lambda: eks.create_access_entry(
            clusterName='retail-store',
            principalArn=role_arn,
            type='STANDARD'
        ))
    except ClientError as e:
        if e.response['Error']['Code'] == 'ResourceInUseException':
            pass
        else:
            raise

    try:
        retry_transient(lambda: eks.associate_access_policy(
            clusterName='retail-store',
            principalArn=role_arn,
            policyArn='arn:aws:eks::aws:cluster-access-policy/AmazonAIOpsAssistantPolicy',
            accessScope={'type': 'cluster'}
        ))
    except ClientError as e:
        if 'already associated' in str(e).lower() or e.response['Error']['Code'] == 'ResourceInUseException':
            pass
        else:
            raise
    print("Configured EKS access for DevOps Agent")

    # Step 7: Merge devops-agent MCP config into .mcp.json
    mcp_json_path = '/workshop/.mcp.json'
    if os.path.exists(mcp_json_path):
        with open(mcp_json_path) as f:
            mcp_config = json.load(f)
    else:
        mcp_config = {"mcpServers": {}}

    mcp_config['mcpServers']['devops-agent'] = {
        "command": "uvx",
        "timeout": 600000,
        "type": "stdio",
        "args": [
            "mcp-proxy-for-aws@1.4.2",
            "https://connect.aidevops.us-east-1.api.aws/mcp",
            "--metadata",
            "AWS_REGION=us-east-1"
        ]
    }

    with open(mcp_json_path, 'w') as f:
        json.dump(mcp_config, f, indent=2)

    subprocess.run(['chown', f'{PARTICIPANT_USER}:{PARTICIPANT_USER}', mcp_json_path], check=True)

    # Step 8: Add devops-agent to .claude.json allowedMcpServers
    claude_json_path = '/workshop/.claude.json'
    if os.path.exists(claude_json_path):
        with open(claude_json_path) as f:
            claude_config = json.load(f)
    else:
        claude_config = {}

    if 'mcpServers' not in claude_config:
        claude_config['mcpServers'] = {}
    if 'devops-agent' not in claude_config.get('mcpServers', {}):
        claude_config['mcpServers']['devops-agent'] = {"allow": ["*"]}

    with open(claude_json_path, 'w') as f:
        json.dump(claude_config, f, indent=2)

    subprocess.run(['chown', f'{PARTICIPANT_USER}:{PARTICIPANT_USER}', claude_json_path], check=True)
    print("Configured DevOps Agent MCP plugin")

    print("DevOps Agent deployment complete.")
    write_status('success', 'All steps completed')

except Exception as e:
    print(f"DevOps Agent deployment FAILED: {e}")
    import traceback
    traceback.print_exc()
    try:
        write_status('failed', str(e)[:500])
    except Exception:
        pass
    sys.exit(1)
