SELECT 'CREATE ROLE diagnostic_reader LOGIN' WHERE NOT EXISTS (
    SELECT 1 FROM pg_roles WHERE rolname = 'diagnostic_reader'
) \gexec
ALTER ROLE diagnostic_reader PASSWORD :'reader_password';
SELECT format('GRANT CONNECT ON DATABASE %I TO diagnostic_reader', current_database()) \gexec
ALTER ROLE diagnostic_reader SET default_transaction_read_only = on;
