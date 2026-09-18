#!/bin/sh
set -eu

: "${DB_HOSTNAME:?DB_HOSTNAME must be set}"
: "${DB_PORT:=5432}"
: "${DB_NAME:?DB_NAME must be set}"
: "${DB_USERNAME:?DB_USERNAME must be set}"
: "${DB_PASSWORD:?DB_PASSWORD must be set}"

config="/tmp/dhis.conf"
{
  printf '%s\n' \
    'connection.dialect = org.hibernate.dialect.PostgreSQLDialect' \
    'connection.driver_class = org.postgresql.Driver'
  printf 'connection.url = jdbc:postgresql://%s:%s/%s\n' \
    "$DB_HOSTNAME" "$DB_PORT" "$DB_NAME"
  printf 'connection.username = %s\n' "$DB_USERNAME"
  printf 'connection.password = %s\n' "$DB_PASSWORD"
  printf '%s\n' \
    '' \
    'flyway.repair_before_migration=on' \
    '' \
    'tracker.import.preheat.cache.enabled=off' \
    '' \
    'server.https = off'
} > "$config"

cp "$config" /opt/dhis2/dhis.conf
exec "$@"
