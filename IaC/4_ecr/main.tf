resource "aws_ecr_repository" "ecr" {
  for_each = {
    for repo in local.config.repositories : "${local.identifier}-${repo.name}" => repo
  }

  name                 = each.key
  image_tag_mutability = each.value.image_tag_mutability

  encryption_configuration {
    encryption_type = each.value.encryption_type
  }

  image_scanning_configuration {
    scan_on_push = each.value.scan_on_push
  }
}
