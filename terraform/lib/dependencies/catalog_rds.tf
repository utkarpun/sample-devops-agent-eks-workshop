module "catalog_rds" {
  source  = "terraform-aws-modules/rds-aurora/aws"
  version = "~> 10.0"

  name                        = "${var.environment_name}-catalog"
  engine                      = "aurora-mysql"
  engine_version              = "8.0"
  allow_major_version_upgrade = true

  instances = {
    one = {
      instance_class = "db.t3.medium"
    }
  }

  vpc_id                 = var.vpc_id
  subnets                = var.subnet_ids
  create_db_subnet_group = true

  manage_master_user_password = false
  master_username             = "admin"
  master_password_wo          = random_string.catalog_db_master.result
  master_password_wo_version  = 1
  database_name               = "catalog"
  storage_encrypted           = true
  apply_immediately           = true
  skip_final_snapshot         = true

  db_parameter_group = {
    name   = "${var.environment_name}-catalog"
    family = "aurora-mysql8.0"
  }

  cluster_parameter_group = {
    name   = "${var.environment_name}-catalog"
    family = "aurora-mysql8.0"
  }

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