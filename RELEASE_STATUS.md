RELEASE_ID = 2026-09-05T074455Z
CANDIDATE_SHA = efed776caffc6275b481f5af0571f30e3ca7bdef
PREVIOUS_GOOD_SHA = 803e195cd3722aa5c03b653f2d39e003acdede7f

SCOPE = PRODUCT_CANDIDATE_WITHOUT_WEB_ADMIN

STATIC = PASS
UNIT = PASS
INTEGRATION = PASS
REGRESSION = PASS

TOTAL_TESTS = 481
PASSED = 481
FAILED = 0
SKIPPED = 0

BUILD = PASS
DOCKER_QA = PASS
DOCKER_QA_TESTS = 481/481
STAGING = NOT_RUN
E2E = NOT_RUN

CRITICAL_BUGS = 0
NON_CRITICAL_BUGS = 0
QA_VERDICT = PASS

PRODUCTION = NOT_DEPLOYED
PRODUCTION_CHANGED = NO
PRODUCTION_SMOKE = NOT_RUN

DATABASE_MIGRATIONS = 0061,0062,0063
DATABASE_MIGRATIONS_QA = PASS
SECRET_SAFETY = PASS
UNEXPLAINED_CHANGES = 0

PRODUCT_CANDIDATE_FILES =
.dockerignore
.env.example
.gitignore
app/bot/menu.py
app/bot/runtime.py
app/core/config.py
app/daily_reports.py
app/db/models.py
app/integrations/robokassa.py
app/integrations/subscriptions.py
app/main.py
app/poller.py
app/traffic/system_conversion_processor.py
alembic/versions/0061_channel_daily_reports_enabled.py
alembic/versions/0062_subscription_orders.py
alembic/versions/0063_channel_subscription_expiry.py
docs/ROBOKASSA_SETUP.md
tests/test_bot_added_auto_connect.py
tests/test_daily_reports.py
tests/test_onboarding.py
tests/test_promotion_channel_summary.py
tests/test_runtime_entrypoints.py
tests/test_robokassa_result_callback.py
tests/test_subscriptions.py
tests/test_system_conversion_processor.py

EXCLUDED_WEB_ADMIN_FILES =
app/web_admin_readonly.py
tests/test_web_admin_readonly.py
app/api/routes.py

EXCLUDED_SECRET_FILE =
ROBOKASSA_PRODUCTION.env

EXCLUDED_GENERATED_ARTIFACTS =
releases/2026-09-05_efed776caffc6275b481f5af0571f30e3ca7bdef.md
releases/2026-09-05_efed776caffc6275b481f5af0571f30e3ca7bdef_2026-09-05T044654Z.md
releases/2026-09-05_efed776caffc6275b481f5af0571f30e3ca7bdef_2026-09-05T045059Z.md
releases/2026-09-05_efed776caffc6275b481f5af0571f30e3ca7bdef_2026-09-05T050317Z.md
releases/2026-09-05_efed776caffc6275b481f5af0571f30e3ca7bdef_2026-09-05T050638Z.md
releases/2026-09-05_efed776caffc6275b481f5af0571f30e3ca7bdef_2026-09-05T053005Z.md
releases/2026-09-05_efed776caffc6275b481f5af0571f30e3ca7bdef_2026-09-05T054113Z.md
releases/2026-09-05_efed776caffc6275b481f5af0571f30e3ca7bdef_2026-09-05T055811Z.md
releases/2026-09-05_efed776caffc6275b481f5af0571f30e3ca7bdef_2026-09-05T074455Z.md

PROVENANCE =
USER-owned valid product changes: Robokassa/payment, subscriptions/migrations, daily reports, system conversions, bot/menu/runtime and their tests.
ARCHITECT-REVIEW-ONLY groundwork: web-admin files listed in EXCLUDED_WEB_ADMIN_FILES.
USER secret file: ROBOKASSA_PRODUCTION.env; ignored and excluded.
UNKNOWN generated artifacts: files listed in EXCLUDED_GENERATED_ARTIFACTS; retained, not deleted.

RECOMMENDATION = QA_GO_FOR_PRODUCT_CANDIDATE; ARCHITECT_REVIEW_REQUIRED_BEFORE_COMMIT_OR_DEPLOY

RELEASE_METADATA_FILES =
RELEASE_STATUS.md — UNKNOWN-owned canonical QA/release metadata; COMPLETED_VALID_CHANGE
