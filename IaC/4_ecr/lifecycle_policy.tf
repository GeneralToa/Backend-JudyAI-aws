resource "aws_ecr_lifecycle_policy" "lifecycle_policy" {
  for_each = {
    for repo in local.config.repositories : "${local.identifier}-${repo.name}" => repo
    if repo.lifecycle_policy == true
  }

  policy = <<POLICY
{
  "rules": [
    {
      "rulePriority": 1,
      "description": "remove untagged images",
      "selection": {
        "countNumber": 1,
        "countType": "sinceImagePushed",
        "countUnit": "days",
        "tagStatus": "untagged"
      },
      "action": {
        "type": "expire"
      }
    },
    {
      "rulePriority": 2,
      "description": "Keep last 5 images",
      "selection": {
          "tagStatus": "tagged",
          "tagPatternList": ["*"],
          "countType": "imageCountMoreThan",
          "countNumber": 5
      },
      "action": {
          "type": "expire"
      }
    }
  ]
}
POLICY

  repository = aws_ecr_repository.ecr[each.key].name
}
