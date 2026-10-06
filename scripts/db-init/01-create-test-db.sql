-- Runs once, when the compose Postgres volume is created.
-- Integration tests use app_test, so they never touch your dev data.
CREATE DATABASE app_test;
