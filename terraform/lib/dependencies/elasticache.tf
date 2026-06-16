resource "aws_elasticache_subnet_group" "checkout" {
  name       = "${var.environment_name}-checkout"
  subnet_ids = var.subnet_ids
}

resource "aws_security_group" "checkout_redis" {
  name        = "${var.environment_name}-checkout-redis"
  vpc_id      = var.vpc_id
  description = "Security group for checkout Redis"
  tags        = merge(var.tags, { Name = "${var.environment_name}-checkout-redis" })
}

resource "aws_vpc_security_group_ingress_rule" "checkout_redis" {
  count = length(concat(var.allowed_security_group_ids, [var.checkout_security_group_id]))

  security_group_id            = aws_security_group.checkout_redis.id
  from_port                    = 6379
  to_port                      = 6379
  ip_protocol                  = "tcp"
  referenced_security_group_id = concat(var.allowed_security_group_ids, [var.checkout_security_group_id])[count.index]
}

resource "aws_elasticache_replication_group" "checkout" {
  replication_group_id       = "${var.environment_name}-checkout"
  description                = "${var.environment_name} checkout Redis"
  node_type                  = "cache.t3.micro"
  num_cache_clusters         = 1
  port                       = 6379
  subnet_group_name          = aws_elasticache_subnet_group.checkout.name
  security_group_ids         = [aws_security_group.checkout_redis.id]
  transit_encryption_enabled = false
  apply_immediately          = true
  tags                       = var.tags
}
