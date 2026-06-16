module "orders_rds" {
  source  = "terraform-aws-modules/rds-aurora/aws"
  version = "~> 10.0"

  name           = "${var.environment_name}-orders"
  engine         = "aurora-postgresql"
  engine_version = "15.10"

  instances = {
    one = {
      instance_class = "db.t3.medium"
    }
  }

  vpc_id  = var.vpc_id
  subnets = var.subnet_ids

  manage_master_user_password = false
  master_password_wo          = random_string.orders_db_master.result
  master_password_wo_version  = 1
  database_name               = "orders"
  storage_encrypted           = true
  apply_immediately           = true
  skip_final_snapshot         = true

  db_parameter_group = {
    name   = "${var.environment_name}-orders"
    family = "aurora-postgresql15"
  }

  cluster_parameter_group = {
    name   = "${var.environment_name}-orders"
    family = "aurora-postgresql15"
  }

  tags = var.tags
}

resource "aws_vpc_security_group_ingress_rule" "orders_rds_sg" {
  count = length(concat(var.allowed_security_group_ids, [var.orders_security_group_id]))

  security_group_id            = module.orders_rds.security_group_id
  from_port                    = module.orders_rds.cluster_port
  to_port                      = module.orders_rds.cluster_port
  ip_protocol                  = "tcp"
  referenced_security_group_id = concat(var.allowed_security_group_ids, [var.orders_security_group_id])[count.index]
}

resource "aws_vpc_security_group_ingress_rule" "orders_rds_cidr" {
  security_group_id = module.orders_rds.security_group_id
  from_port         = module.orders_rds.cluster_port
  to_port           = module.orders_rds.cluster_port
  ip_protocol       = "tcp"
  cidr_ipv4         = var.vpc_cidr
}

resource "random_string" "orders_db_master" {
  length  = 10
  special = false
}
