# Образ с ченджлогами Liquibase, вшитыми на этапе сборки.
# Ченджлоги копируются в /liquibase/changelog — то же расположение, что было у
# bind-mount, поэтому liquibase.properties (changeLogFile: changelog/...) не меняется.
# Отказ от bind-mount устраняет зависимость от файлового шаринга Docker Desktop
# (баг "error while creating mount source path /run/desktop/mnt/host/...").
FROM liquibase/liquibase:4.31
COPY liquibase/ /liquibase/changelog
