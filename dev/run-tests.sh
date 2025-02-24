#!/bin/sh

export COMPOSE_DOCKER_CLI_BUILD=0

DC="docker compose -f dev/docker-compose.dev.yml"

$DC up --build --detach
$DC exec -T backend scripts/wait.sh datastore-writer 9011
$DC exec -T backend scripts/wait.sh datastore-reader 9010
$DC exec -T backend scripts/wait.sh keycloak 8080
$DC exec -T backend ./entrypoint.sh pytest --cov
error=$?
$DC down --volumes
exit $error
