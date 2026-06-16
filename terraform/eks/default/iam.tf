module "iam_assumable_role_carts" {
  source  = "terraform-aws-modules/iam/aws//modules/iam-role-for-service-accounts-eks"
  version = "~> 5.58"

  role_name = "${var.environment_name}-carts-dynamo"

  role_policy_arns = {
    carts_dynamo = module.dependencies.carts_dynamodb_policy_arn
  }

  oidc_providers = {
    main = {
      provider_arn               = module.retail_app_eks.eks_oidc_provider_arn
      namespace_service_accounts = ["carts:carts"]
    }
  }

  tags = module.tags.result
}