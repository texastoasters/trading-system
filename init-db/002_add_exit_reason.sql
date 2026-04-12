-- init-db/002_add_exit_reason.sql
-- Adds exit_reason column to trades table for systems already running 001.
-- Safe to run multiple times (IF NOT EXISTS).
ALTER TABLE trades ADD COLUMN IF NOT EXISTS exit_reason TEXT;
