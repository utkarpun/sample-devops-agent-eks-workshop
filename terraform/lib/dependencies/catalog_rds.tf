module "catalog_rds" {
  source  = "terraform-aws-modules/rds-aurora/aws"
  version = "~> 10.0"

  name                        = "${var.environment_name}-catalog"
  engine                      = "aurora-mysql"
  engine_version              = "8.0"
  instance_class              = "db.t3.medium"
  allow_major_version_upgrade = true

  instances = {
    one = {}
  }

  vpc_id  = var.vpc_id
  subnets = var.subnet_ids

  master_password        = random_string.catalog_db_master.result
  create_random_password = false
  database_name          = "catalog"
  storage_encrypted      = true
  apply_immediately      = true
  skip_final_snapshot    = true

  create_db_parameter_group = true
  db_parameter_group_name   = "${var.environment_name}-catalog"
  db_parameter_group_family = "aurora-mysql8.0"

  create_db_cluster_parameter_group = true
  db_cluster_parameter_group_name   = "${var.environment_name}-catalog"
  db_cluster_parameter_group_family = "aurora-mysql8.0"

  tags = var.tags
}

resource "aws_vpc_security_group_ingress_rule" "catalog_rds_sg" {
  count = length(concat(var.allowed_security_group_ids, [var.catalog_security_group_id]))

  security_group_id            = module.catalog_rds.security_group_id
  from_port                    = module.catalog_rds.cluster_port
  to_port                      = module.catalog_rds.cluster_port
  ip_protocol                  = "tcp"
  referenced_security_group_id = concat(var.allowed_security_group_ids, [var.catalog_security_group_id])[count.index]
}

resource "aws_vpc_security_group_ingress_rule" "catalog_rds_cidr" {
  security_group_id = module.catalog_rds.security_group_id
  from_port         = module.catalog_rds.cluster_port
  to_port           = module.catalog_rds.cluster_port
  ip_protocol       = "tcp"
  cidr_ipv4         = var.vpc_cidr
}

resource "random_string" "catalog_db_master" {
  length  = 10
  special = false
}