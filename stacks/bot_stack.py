from aws_cdk import Duration, RemovalPolicy, Stack, aws_dynamodb, aws_scheduler
from aws_cdk import aws_iam as iam
from aws_cdk import aws_lambda as _lambda
from constructs import Construct


class BotStack(Stack):
    def __init__(self, scope: Construct, id: str, **kw):
        super().__init__(scope, id, **kw)

        games_table = aws_dynamodb.Table(
            self,
            "Games",
            table_name="games",
            partition_key={
                "name": "game_id",
                "type": aws_dynamodb.AttributeType.NUMBER,
            },
            billing_mode=aws_dynamodb.BillingMode.PAY_PER_REQUEST,
            removal_policy=RemovalPolicy.RETAIN,
        )

        posts_table = aws_dynamodb.Table(
            self,
            "Posts",
            table_name="posts",
            partition_key={
                "name": "clip_id",
                "type": aws_dynamodb.AttributeType.NUMBER,
            },
            billing_mode=aws_dynamodb.BillingMode.PAY_PER_REQUEST,
            removal_policy=RemovalPolicy.RETAIN,
        )

        players_table = aws_dynamodb.Table(
            self,
            "Players",
            table_name="players",
            partition_key={
                "name": "player_id",
                "type": aws_dynamodb.AttributeType.NUMBER,
            },
            billing_mode=aws_dynamodb.BillingMode.PAY_PER_REQUEST,
            time_to_live_attribute="ttl",
            removal_policy=RemovalPolicy.RETAIN,
        )

        fn = lambda name, handler, timeout: _lambda.Function(
            self,
            name,
            runtime=_lambda.Runtime.PYTHON_3_14,
            architecture=_lambda.Architecture.ARM_64,
            handler=handler,
            code=_lambda.Code.from_asset("src"),
            timeout=Duration.seconds(timeout),
            environment={
                "GAMES_TABLE_NAME": games_table.table_name,
                "POSTS_TABLE_NAME": posts_table.table_name,
                "PLAYERS_TABLE_NAME": players_table.table_name,
            },
        )

        scheduler_fn = fn(
            name="Scheduler", handler="lambdas.scheduler.handler", timeout=60
        )
        ingestor_fn = fn(
            name="Ingestor", handler="lambdas.ingestor.handler", timeout=60
        )
        poster_fn = fn(name="Poster", handler="lambdas.poster.handler", timeout=120)
        for table in (games_table, posts_table, players_table):
            table.grant_read_write_data(scheduler_fn)
            table.grant_read_write_data(ingestor_fn)
            table.grant_read_write_data(poster_fn)

        # Scheduler -> Lambda exec role
        exec_role = iam.Role(
            self,
            id="SchedExec",
            assumed_by=iam.ServicePrincipal("scheduler.amazonaws.com"),
        )
        scheduler_fn.add_environment("INGESTOR_FUNCTION_ARN", ingestor_fn.function_arn)
        scheduler_fn.add_environment("POSTER_FUNCTION_ARN", poster_fn.function_arn)
        scheduler_fn.add_environment("SCHEDULER_EXEC_ROLE_ARN", exec_role.role_arn)
        scheduler_fn.add_environment(
            "INGESTOR_SCHEDULE_NAME", "bluesky-ingestor-gameday"
        )
        scheduler_fn.add_environment("POSTER_SCHEDULE_NAME", "bluesky-poster-gameday")
        poster_fn.grant_invoke(exec_role)
        ingestor_fn.grant_invoke(exec_role)
        scheduler_fn.grant_invoke(exec_role)

        # Daily trigger for the scheduler lambda; it owns the game-day
        # 5-min burst schedules. Hourly baselines below cover everything
        # outside the game window (separate schedules, so no conflict).
        aws_scheduler.CfnSchedule(
            self,
            "DailySync",
            schedule_expression="cron(0 9 * * ? *)",
            schedule_expression_timezone="America/Toronto",
            flexible_time_window={"mode": "OFF"},
            target={"arn": scheduler_fn.function_arn, "role_arn": exec_role.role_arn},
        )
        aws_scheduler.CfnSchedule(
            self,
            "IngestorHourly",
            schedule_expression="cron(4 * * * ? *)",
            schedule_expression_timezone="America/Toronto",
            flexible_time_window={"mode": "OFF"},
            target={"arn": ingestor_fn.function_arn, "role_arn": exec_role.role_arn},
        )
        aws_scheduler.CfnSchedule(
            self,
            "PosterHourly",
            schedule_expression="cron(5 * * * ? *)",
            schedule_expression_timezone="America/Toronto",
            flexible_time_window={"mode": "OFF"},
            target={"arn": poster_fn.function_arn, "role_arn": exec_role.role_arn},
        )

        scheduler_fn.add_to_role_policy(
            iam.PolicyStatement(
                actions=[
                    "scheduler:CreateSchedule",
                    "scheduler:UpdateSchedule",
                    "scheduler:DeleteSchedule",
                    "scheduler:GetSchedule",
                ],
                resources=["*"],
            )
        )  # scope to arn:aws:scheduler:*:*:schedule/default/bluesky-* in prod
        scheduler_fn.add_to_role_policy(
            iam.PolicyStatement(
                actions=["iam:PassRole"], resources=[exec_role.role_arn]
            )
        )
        # secrets: don't create in CDK, read existing SSM SecureString in Lambda via ssm:GetParameter
