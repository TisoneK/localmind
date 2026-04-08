-- Add title column to sessions table
ALTER TABLE sessions ADD COLUMN title TEXT;

-- Create index for title-based queries
CREATE INDEX IF NOT EXISTS idx_sessions_title ON sessions(title);
